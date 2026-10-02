"""The engineering dataset's independent oracle must stay aligned with inputs."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from kmb.dataset import load_dataset, validate_dataset

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "data"
CANDIDATE = PROJECT / "datasets" / "engineering-v2"
SCRIPT = PROJECT / "scripts" / "audit-dataset.py"


def test_engineering_dataset_audit_and_loader() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=True
    )
    report = json.loads(completed.stdout)
    assert report["status"] == "passed_static_audit"
    assert report["task_count"] == 60
    assert report["review_counts"] == {"clear_from_inputs": 59, "clarified_in_v2": 1}
    assert [item["path"] for item in report["changed_files"]] == [
        "README.md", "tasks/boundary-007.json"
    ]
    assert validate_dataset(CANDIDATE)["errors"] == []
    assert len(load_dataset(CANDIDATE)) == 60
    saved = json.loads((CANDIDATE / "audit-report.json").read_text())
    # The saved audit records its original machine paths, not portable task identity.
    assert Path(report["source"]) == SOURCE.resolve()
    assert Path(report["candidate"]) == CANDIDATE.resolve()
    location_fields = {"source", "candidate"}
    assert {k: v for k, v in report.items() if k not in location_fields} == {
        k: v for k, v in saved.items() if k not in location_fields
    }


def test_engineering_audit_detects_private_answer_drift(tmp_path: Path) -> None:
    altered = tmp_path / "candidate"
    shutil.copytree(CANDIDATE, altered)
    path = altered / "rubrics" / "reuse-007.json"
    rubric = json.loads(path.read_text())
    rubric["criteria"][0]["expected"]["remaining"] = 0
    path.write_text(json.dumps(rubric), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--source", str(SOURCE), "--candidate", str(altered)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "reuse-007" in completed.stderr
