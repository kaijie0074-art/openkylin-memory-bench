"""Load trusted task metadata separately from private grading material.

Only ``TaskSpec.public_input()`` may cross the agent boundary.  This module is
used by the trusted host runner; it is not an agent-side data access API.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .models import PrivateRubric, TaskSpec

ABILITIES = ("retention", "recall", "update", "distinction", "boundary", "reuse")
SPLITS = ("dev", "selection", "holdout")
EXPECTED_SPLIT_COUNTS = {"dev": 24, "selection": 18, "holdout": 18}


def _data_root(root: Path) -> Path:
    root = Path(root)
    return root / "data" if (root / "data" / "tasks").is_dir() else root


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read dataset file {path}: {exc}") from exc


def load_dataset(root: Path, split: str | None = None) -> list[tuple[TaskSpec, PrivateRubric]]:
    """Return validated public/private pairs in deterministic task-ID order.

    ``root`` can be a project directory or its data directory.  Rubrics never
    become a field of TaskSpec and simulation fixtures are never read here.
    """
    if split is not None and split not in SPLITS:
        raise ValueError(f"unknown split {split!r}; expected one of {SPLITS}")
    data_root = _data_root(root)
    paths = sorted((data_root / "tasks").glob("*.json"))
    if not paths:
        raise ValueError(f"no task JSON files in {data_root / 'tasks'}")
    result: list[tuple[TaskSpec, PrivateRubric]] = []
    seen: set[str] = set()
    for path in paths:
        task = TaskSpec.model_validate(_read_json(path))
        if task.id != path.stem:
            raise ValueError(f"task ID {task.id!r} differs from filename {path.name!r}")
        if task.id in seen:
            raise ValueError(f"duplicate task ID: {task.id}")
        seen.add(task.id)
        rubric_path = data_root / "rubrics" / path.name
        rubric = PrivateRubric.model_validate(_read_json(rubric_path))
        if rubric.task_id != task.id:
            raise ValueError(f"rubric task_id mismatch for {path.name}")
        if split is None or task.split == split:
            result.append((task, rubric))
    return sorted(result, key=lambda pair: pair[0].id)


def validate_dataset(root: Path) -> dict[str, Any]:
    """Audit the shipped 60-case benchmark without running any agent.

    Errors are accumulated, including parse errors, orphan rubrics, family
    leakage, split imbalance and invalid grading specifications.  This checks
    structural guarantees, not scientific novelty or empirical validity.
    """
    data_root = _data_root(root)
    errors: list[str] = []
    tasks: list[TaskSpec] = []
    seen: set[str] = set()
    task_paths = sorted((data_root / "tasks").glob("*.json"))
    if not task_paths:
        errors.append("no task JSON files found")
    task_stems = {p.stem for p in task_paths}
    rubric_stems = {p.stem for p in (data_root / "rubrics").glob("*.json")}
    for orphan in sorted(rubric_stems - task_stems):
        errors.append(f"orphan rubric: {orphan}")
    for path in task_paths:
        try:
            task = TaskSpec.model_validate(_read_json(path))
            if task.id != path.stem:
                errors.append(f"task ID/filename mismatch: {path.name}")
            if task.id in seen:
                errors.append(f"duplicate task ID: {task.id}")
            seen.add(task.id)
            tasks.append(task)
            if not task.family.strip():
                errors.append(f"empty family: {task.id}")
            if any(not s.prompt.strip() for s in task.sessions):
                errors.append(f"empty session prompt: {task.id}")
            rubric = PrivateRubric.model_validate(
                _read_json(data_root / "rubrics" / path.name)
            )
            if rubric.task_id != task.id:
                errors.append(f"rubric task_id mismatch: {task.id}")
            if not any(c.kind != "semantic" for c in rubric.criteria):
                errors.append(f"no objective criterion: {task.id}")
            for criterion in rubric.criteria:
                if (
                    criterion.kind in {"text_equals", "text_contains", "text_not_contains", "action_contains"}
                    and not isinstance(criterion.expected, str)
                ):
                    errors.append(f"text criterion must have string expected: {task.id}/{criterion.id}")
                if criterion.kind == "csv_equals":
                    rows = criterion.expected
                    if not isinstance(rows, list) or not rows or not all(
                        isinstance(row, list) and all(isinstance(cell, str) for cell in row)
                        for row in rows
                    ):
                        errors.append(f"CSV expected must be nonempty string rows: {task.id}/{criterion.id}")
        except (ValueError, TypeError) as exc:
            errors.append(f"{path.name}: {exc}")

    split_counts = Counter(t.split for t in tasks)
    ability_counts = Counter(t.ability for t in tasks)
    by_split = {s: Counter(t.ability for t in tasks if t.split == s) for s in SPLITS}
    families: dict[str, set[str]] = defaultdict(set)
    family_splits: dict[str, set[str]] = defaultdict(set)
    for task in tasks:
        families[task.split].add(task.family)
        family_splits[task.family].add(task.split)
    for family, splits in sorted(family_splits.items()):
        if len(splits) > 1:
            errors.append(f"family crosses splits: {family}: {sorted(splits)}")
    for split, expected in EXPECTED_SPLIT_COUNTS.items():
        if split_counts[split] != expected:
            errors.append(f"split {split}: expected {expected}, found {split_counts[split]}")
        for ability in ABILITIES:
            if by_split[split][ability] != expected // len(ABILITIES):
                errors.append(
                    f"split/ability {split}/{ability}: expected {expected // len(ABILITIES)}, "
                    f"found {by_split[split][ability]}"
                )
    return {
        "total": len(tasks),
        "counts": {"split": dict(split_counts), "ability": dict(ability_counts),
                   "split_ability": {s: dict(by_split[s]) for s in SPLITS}},
        "families": {s: sorted(families[s]) for s in SPLITS},
        "errors": errors,
        "validation": "static_only",
    }


def pilot_tasks(root: Path) -> list[tuple[TaskSpec, PrivateRubric]]:
    """Twelve development tasks: first two IDs of each of the six abilities."""
    pairs = load_dataset(root, "dev")
    selected: list[tuple[TaskSpec, PrivateRubric]] = []
    for ability in ABILITIES:
        candidates = [p for p in pairs if p[0].ability == ability]
        if len(candidates) < 2:
            raise ValueError(f"pilot requires two dev tasks for {ability}")
        selected.extend(candidates[:2])
    return selected
