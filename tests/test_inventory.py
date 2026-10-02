"""Independent inventory and content coverage, including immutable v1 evidence."""
import asyncio
import hashlib
import json

import pytest

from kmb.adapters import snapshot_inventory, snapshot_text
from kmb.models import Criterion, EvidenceBundle, PrivateRubric
from kmb.scorers import score


def bundle(**kwargs):
    return EvidenceBundle(run_id="inventory-test", task_id="inventory-test", ability="update",
                          family="fixture", split="dev", agent="fixture", provenance="simulated",
                          **kwargs).freeze()


def verdict(evidence, kind, path):
    rubric = PrivateRubric(task_id=evidence.task_id, criteria=[Criterion(
        id="test", kind=kind, path=path, description="synthetic", expected="expected")])
    return asyncio.run(score(evidence, rubric, "A")).verdict


def test_binary_does_not_hide_missing_file_and_does_not_fabricate_text(tmp_path):
    (tmp_path / "git-object").write_bytes(b"\x00\xff")
    (tmp_path / "result.txt").write_text("expected")
    files, complete, _ = snapshot_text(tmp_path)
    inventory, enumerated = snapshot_inventory(tmp_path)
    assert not complete and enumerated
    e = bundle(files_after=files, files_complete=complete,
               file_inventory_after=inventory, file_inventory_complete=enumerated)
    assert verdict(e, "file_absent", "old/result.txt") == "pass"
    assert verdict(e, "file_exists", "git-object") == "pass"
    assert verdict(e, "text_equals", "git-object") == "undetermined"
    assert verdict(e, "text_equals", "result.txt") == "pass"
    assert verdict(e, "file_absent", "git-object") == "fail"


def test_symlinks_are_not_followed_and_cannot_prove_descendant_absence(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    (private / "secret").write_text("private")
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "link").symlink_to(private, target_is_directory=True)
    entries, complete = snapshot_inventory(root)
    assert entries == {"link": "symlink"} and not complete
    e = bundle(files_after={}, files_complete=False, file_inventory_after=entries,
               file_inventory_complete=complete)
    assert verdict(e, "file_absent", "link") == "fail"
    assert verdict(e, "file_absent", "link/secret") == "undetermined"
    assert verdict(e, "file_exists", "link") == "fail"


def test_redacted_path_and_directory_entries(tmp_path):
    (tmp_path / "token-value").write_text("x")
    (tmp_path / "result.txt").mkdir()
    entries, complete = snapshot_inventory(tmp_path, ("token-value",))
    assert entries == {"result.txt": "directory"} and not complete
    e = bundle(files_after={}, files_complete=False, file_inventory_after=entries)
    assert verdict(e, "file_exists", "result.txt") == "fail"


def test_legacy_checksum_is_preserved_and_new_fields_cannot_escape_hash():
    e = bundle(schema_version="1", files_after={"x": "y"})
    raw = e.model_dump(exclude={"evidence_hash", "file_inventory_after", "file_inventory_complete"})
    old_hash = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":")).encode()).hexdigest()
    assert e.evidence_hash == old_hash
    original = dict(raw, evidence_hash=old_hash)
    EvidenceBundle.model_validate(original).verify()
    with pytest.raises(ValueError):
        EvidenceBundle.model_validate(dict(original, file_inventory_after={"z": "file"}))
    e.file_inventory_after = {"z": "file"}
    with pytest.raises(ValueError):
        e.verify()


def test_inventory_mutation_invalidates_v2_hash():
    e = bundle(files_complete=False, file_inventory_after={"x": "file"})
    e.file_inventory_after["x"] = "directory"
    with pytest.raises(ValueError):
        e.verify()


def test_explicit_partial_inventory_overrides_legacy_text_complete_flag():
    e = bundle(files_after={}, files_complete=True,
               file_inventory_after={'unreadable': 'directory'}, file_inventory_complete=False)
    assert verdict(e, 'file_absent', 'unreadable/secret.txt') == 'undetermined'
