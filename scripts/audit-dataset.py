#!/usr/bin/env python3
"""Verify the input-derived engineering dataset audit without running agents.

The AI-authored oracle in datasets/engineering-v2/audit.json was derived from
task inputs, then compared to the private rubrics. This script checks coverage,
structural validity, source drift, and the one identified prompt clarification.
It is not an independent human-review certificate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from kmb.dataset import load_dataset, validate_dataset


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): _digest(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.relative_to(root).parts[0] in {"tasks", "rubrics", "simulation", "LICENSE", "README.md"}
    }


def _manifest_digest(manifest: dict[str, str]) -> str:
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def audit(source: Path, candidate: Path) -> dict:
    source = source.resolve()
    candidate = candidate.resolve()
    source_validation = validate_dataset(source)
    candidate_validation = validate_dataset(candidate)
    assert not source_validation["errors"], source_validation["errors"]
    assert not candidate_validation["errors"], candidate_validation["errors"]
    assert source_validation["total"] == candidate_validation["total"] == 60
    assert source_validation["counts"] == candidate_validation["counts"]
    assert source_validation["families"] == candidate_validation["families"]

    old_pairs = {task.id: (task, rubric) for task, rubric in load_dataset(source)}
    new_pairs = {task.id: (task, rubric) for task, rubric in load_dataset(candidate)}
    assert set(old_pairs) == set(new_pairs)
    ledger = json.loads((candidate / "audit.json").read_text(encoding="utf-8"))
    assert ledger["schema_version"] == 1
    items = ledger["items"]
    assert len(items) == 60
    by_id = {item["task_id"]: item for item in items}
    assert len(by_id) == len(items) and set(by_id) == set(new_pairs)

    for task_id, (task, rubric) in new_pairs.items():
        old_task, old_rubric = old_pairs[task_id]
        item = by_id[task_id]
        assert (item["ability"], item["split"], item["family"]) == (
            task.ability, task.split, task.family
        ), task_id
        assert item["basis_from_task_input"].strip(), task_id
        criteria = {criterion.id: criterion for criterion in rubric.criteria}
        assert "result-1" in criteria, task_id
        result = criteria["result-1"]
        assert result.path == item["independent_expected_result_path"], task_id
        assert result.expected == item["independent_expected_result"], task_id
        extras = item["additional_criteria_from_task_input"]
        assert set(extras) == set(criteria) - {"result-1"}, task_id
        for criterion_id, expected in extras.items():
            actual = criteria[criterion_id]
            assert expected["kind"] == actual.kind, (task_id, criterion_id)
            if actual.kind == "semantic":
                assert expected["acceptance"].strip(), (task_id, criterion_id)
            else:
                assert expected["path"] == actual.path, (task_id, criterion_id)
                if actual.kind not in {"file_absent", "file_exists"}:
                    assert expected["expected"] == actual.expected, (task_id, criterion_id)
        assert old_rubric == rubric, task_id
        if task_id == "boundary-007":
            assert item["review"] == "clarified_in_v2"
            old_payload = old_task.model_dump()
            new_payload = task.model_dump()
            old_final = old_payload["sessions"][-1]["prompt"]
            new_final = new_payload["sessions"][-1]["prompt"]
            old_payload["sessions"][-1]["prompt"] = new_final
            assert old_payload == new_payload and old_final != new_final
            assert "摘要正文仅为引号内的文字" in new_final
            assert "保留文件是操作要求，不属于摘要正文" in new_final
        else:
            assert item["review"] == "clear_from_inputs", task_id
            assert old_task == task, task_id

    old_manifest = _manifest(source)
    new_manifest = _manifest(candidate)
    changed = sorted(
        key for key in set(old_manifest) | set(new_manifest)
        if old_manifest.get(key) != new_manifest.get(key)
    )
    assert changed == ["README.md", "tasks/boundary-007.json"], changed
    return {
        "status": "passed_static_audit",
        "source": str(source),
        "candidate": str(candidate),
        "source_manifest_sha256": _manifest_digest(old_manifest),
        "candidate_manifest_sha256": _manifest_digest(new_manifest),
        "source_boundary_007_sha256": old_manifest["tasks/boundary-007.json"],
        "candidate_boundary_007_sha256": new_manifest["tasks/boundary-007.json"],
        "changed_files": [
            {"path": key, "old_sha256": old_manifest.get(key), "new_sha256": new_manifest.get(key)}
            for key in changed
        ],
        "task_count": 60,
        "review_counts": {"clear_from_inputs": 59, "clarified_in_v2": 1},
        "split_counts": candidate_validation["counts"]["split"],
        "loader_compatible": True,
        "limits": "This is a static input/rubric audit; no agent outcome or independent human agreement is inferred.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=PROJECT / "data")
    parser.add_argument("--candidate", type=Path, default=PROJECT / "datasets" / "engineering-v2")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.source, args.candidate)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
