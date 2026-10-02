"""Trusted-side evidence persistence. Re-scoring never changes the original evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from kmb.models import EvidenceBundle, ScoreResult


def write_json(path: Path, data: Any, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "x" if exclusive else "w"
    with path.open(mode, encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def save_evidence(bundle: EvidenceBundle, directory: Path) -> Path:
    bundle.freeze()
    path = directory / "evidence.json"
    write_json(path, bundle.model_dump(), exclusive=True)
    path.chmod(0o444)
    return path


def load_evidence(path: Path) -> EvidenceBundle:
    return EvidenceBundle.model_validate_json(path.read_text()).verify()


def load_evidence_tree(root: Path) -> list[EvidenceBundle]:
    paths = [root] if root.is_file() else sorted(root.rglob("evidence.json"))
    result = [load_evidence(p) for p in paths]
    if len({e.run_id for e in result}) != len(result):
        raise ValueError("duplicate run IDs in evidence collection")
    return result


def save_score(result: ScoreResult, directory: Path) -> Path:
    # New scores never overwrite previous score versions.
    import uuid
    path = directory / result.run_id / f"{result.scorer}-{uuid.uuid4().hex[:10]}.json"
    write_json(path, result.model_dump(), exclusive=True)
    return path


def load_scores(root: Path) -> list[ScoreResult]:
    paths = [root] if root.is_file() else sorted(root.rglob("*.json"))
    return [ScoreResult.model_validate_json(p.read_text()) for p in paths]


def private_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
