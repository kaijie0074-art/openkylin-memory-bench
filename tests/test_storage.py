"""Persistence checks use authored fixtures only; no real-agent claims or calls."""
from __future__ import annotations

import json
import stat

import pytest

from kmb.models import EvidenceBundle, ScoreResult
from kmb.storage import (
    load_evidence,
    load_evidence_tree,
    load_scores,
    private_write,
    save_evidence,
    save_score,
)


def bundle(run_id="fixture-run"):
    return EvidenceBundle(
        run_id=run_id, task_id="fixture-task", ability="update", family="fixture",
        split="dev", agent="test-fixture", provenance="simulated",
        files_after={"answer.json": '{"value": 2}'},
    ).freeze()


def test_evidence_roundtrip_preserves_checksum_and_default_floats(tmp_path):
    original = bundle()
    path = save_evidence(original, tmp_path / "run")
    loaded = load_evidence(path)
    assert loaded.model_dump() == original.model_dump()
    assert loaded.verify() is loaded
    assert isinstance(loaded.elapsed_seconds, float)
    assert not (path.stat().st_mode & stat.S_IWUSR)


def test_modified_evidence_is_rejected(tmp_path):
    path = save_evidence(bundle(), tmp_path / "run")
    path.chmod(0o600)
    data = json.loads(path.read_text())
    data["files_after"]["answer.json"] = '{"value": 3}'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="modified"):
        load_evidence(path)


def test_existing_evidence_is_never_overwritten(tmp_path):
    first = save_evidence(bundle(), tmp_path / "run")
    before = first.read_bytes()
    with pytest.raises(FileExistsError):
        save_evidence(bundle("other"), tmp_path / "run")
    assert first.read_bytes() == before


def test_duplicate_run_ids_rejected_when_collecting(tmp_path):
    save_evidence(bundle(), tmp_path / "first")
    save_evidence(bundle(), tmp_path / "second")
    with pytest.raises(ValueError, match="duplicate run IDs"):
        load_evidence_tree(tmp_path)


def test_rescores_get_new_files_and_unknown_usage_stays_unknown(tmp_path):
    score = ScoreResult(run_id="fixture-run", task_id="fixture-task", evidence_hash=bundle().evidence_hash,
                        scorer="A", status="not_scored", error="test-only fixture")
    first = save_score(score, tmp_path)
    second = save_score(score, tmp_path)
    assert first != second
    results = load_scores(tmp_path)
    assert len(results) == 2
    assert all(s.usage.input_tokens is None and s.usage.output_tokens is None for s in results)


@pytest.mark.parametrize("run_id", ["../escape", "a/../../escape", "/tmp/escape", "a\\b", ".", ".."])
def test_unsafe_run_ids_cannot_become_output_paths(tmp_path, run_id):
    # Either model validation or the storage boundary must reject the identifier.
    with pytest.raises(ValueError):
        unsafe = bundle(run_id)
        score = ScoreResult(run_id=unsafe.run_id, task_id=unsafe.task_id,
                            evidence_hash=unsafe.evidence_hash, scorer="A")
        save_score(score, tmp_path / "scores")


def test_private_files_are_exclusive_and_private(tmp_path):
    path = tmp_path / "private.json"
    private_write(path, {"purpose": "test"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        private_write(path, {"purpose": "overwrite"})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_elapsed_time_rejected(value):
    with pytest.raises(ValueError):
        EvidenceBundle(run_id="test", task_id="test", ability="update", family="test", split="dev",
                       agent="test-fixture", provenance="simulated", elapsed_seconds=value)
