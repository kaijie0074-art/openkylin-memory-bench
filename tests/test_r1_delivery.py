"""Tiny fabricated archives only; no package installation, network or model calls."""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), PROJECT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assembler = load("assemble-r1-delivery")
checker = load("check-r1-delivery")


def inputs(tmp_path):
    return argparse.Namespace(source_root=PROJECT, output=tmp_path / "complete", slim_output=tmp_path / "slim",
                              pdf=tmp_path / "missing.pdf", deb=tmp_path / "missing.deb",
                              video=tmp_path / "missing.mp4", record=[], assemble=False)


def test_inspection_creates_no_output_and_reports_missing_media(tmp_path):
    args = inputs(tmp_path)
    result = assembler.assemble(args)
    assert result["inputs_ready"] is False and len(result["missing_inputs"]) >= 3
    assert result["output_created"] is False and not args.output.exists()


def test_public_source_allowlist_contains_recipes_and_required_scripts():
    names = {path.relative_to(PROJECT).as_posix() for path in assembler.source_files(PROJECT)}
    assert set(assembler.REQUIRED_SOURCE) <= names
    assert ".github/workflows/ci.yml" in names
    assert "competition/评审复现说明.md" in names
    assert "competition/publication/public-results.md" in names
    assert "competition/publication/render-captioned-video.py" in names
    assert "docs/agent-image-delivery-R1.md" in names
    assert not names & {"competition/publication/投稿邮件草稿.md", "competition/publication/投稿准备总览.md",
                        "docs/acceptance.md", "docs/human-review-guide.md", "docs/learning-checkpoints.md",
                        "docs/local-proxy-reconnection.md"}
    assert not any("ready-to-submit" in name or ".runtime" in name for name in names)


def test_full_delivery_requires_original_video_rebuild_input():
    assert assembler.EDITED_VIDEO in assembler.RECORDS
    assert "reports/delivery-R1-acceptance-20261004" in assembler.RECORDS
    assert checker.REQUIRED_RECORDS == {assembler.EDITED_VIDEO: assembler.EDITED_VIDEO_SHA256}
    checker.verify_rebuild_records(dict(checker.REQUIRED_RECORDS))
    with pytest.raises(checker.Rejected, match="video_rebuild_input_missing_or_changed"):
        checker.verify_rebuild_records({})
    with pytest.raises(checker.Rejected, match="video_rebuild_input_missing_or_changed"):
        checker.verify_rebuild_records({assembler.EDITED_VIDEO: "0" * 64})


@pytest.mark.parametrize("kind", ["pdf", "deb", "video"])
def test_private_media_path_rejected_without_reading_contents(tmp_path, monkeypatch, kind):
    args = inputs(tmp_path)
    private = tmp_path / ".env.local"
    private.write_text("synthetic test configuration, never a real credential")
    setattr(args, kind, private)
    original = Path.open
    def guarded(path, *a, **k):
        assert path != private, "private file must not be read"
        return original(path, *a, **k)
    monkeypatch.setattr(Path, "open", guarded)
    with pytest.raises(assembler.Rejected, match="private_or_wrong_media_path"):
        assembler.assemble(args)
    assert not args.output.exists()


def test_dangling_release_checksum_symlink_cannot_write_outside_output(tmp_path):
    args = inputs(tmp_path)
    target = tmp_path / "external-target.txt"
    checksum = tmp_path / "complete-发行SHA256SUMS.txt"
    checksum.symlink_to(target)
    with pytest.raises(assembler.Rejected, match="symlink_path_forbidden"):
        assembler.assemble(args)
    assert not target.exists() and not args.output.exists()


def test_raw_video_symlink_rejected_before_opening_target(tmp_path, monkeypatch):
    args = inputs(tmp_path)
    args.source_root = tmp_path / "source"
    raw = args.source_root / assembler.RAW_VIDEO
    raw.parent.mkdir(parents=True)
    private = tmp_path / ".env.local"
    private.write_text("synthetic private fixture")
    raw.symlink_to(private)
    original = Path.open
    def guarded(path, *a, **k):
        assert path not in {raw, private}, "raw symlink target must not be opened"
        return original(path, *a, **k)
    monkeypatch.setattr(Path, "open", guarded)
    with pytest.raises(assembler.Rejected, match="symlink_path_forbidden"):
        assembler.assemble(args)
    assert not args.output.exists()


@pytest.mark.parametrize("kind", ["media", "record"])
def test_private_configuration_directory_rejected_before_content_read(tmp_path, kind):
    args = inputs(tmp_path)
    if kind == "media":
        args.pdf = tmp_path / ".env.local/export.pdf"
    else:
        args.record = ["reports/.env.local/snapshot.json"]
    with pytest.raises(assembler.Rejected, match="private_"):
        assembler.assemble(args)


def test_generated_text_never_overwrites_existing_file(tmp_path):
    path = tmp_path / "manifest.txt"
    path.write_text("original fixture")
    with pytest.raises(FileExistsError):
        assembler.text_file(path, "replacement")
    assert path.read_text() == "original fixture"


@pytest.mark.parametrize("name", ["reports/video-R1-20261004/first-render/video.mp4",
                                "reports/video-R1-20261004/first-render-technical.json",
                                "competition/publication/ready-to-submit-R1/private.md"])
def test_diagnostic_and_private_files_excluded(name):
    assert assembler.excluded(Path(name))
    with pytest.raises(checker.Rejected, match="private_archive_member"):
        checker.safe_name(name)


def test_public_audit_metadata_is_derived_without_changing_original(tmp_path):
    source = tmp_path / "original.json"
    value = {"source": "/local/source/data", "candidate": "/local/source/datasets/engineering-v2",
             "source_manifest_sha256": "0" * 64, "status": "approved-fixture"}
    source.write_text(json.dumps(value))
    original_hash = assembler.digest(source)
    exported = tmp_path / "public/audit-report.json"
    assembler.public_source_copy(source, exported, "datasets/engineering-v2/audit-report.json")
    result = json.loads(exported.read_text())
    assert result["source"] == "data" and result["candidate"] == "datasets/engineering-v2"
    assert result["source_manifest_sha256"] == value["source_manifest_sha256"]
    assert assembler.digest(source) == original_hash


@pytest.fixture
def source_fixture(tmp_path):
    project = tmp_path / "source"
    core = project / "src/kmb"
    core.mkdir(parents=True)
    (core / "fixture.py").write_text("# synthetic source only\n")
    (project / "uv.lock").write_text("# synthetic lock only\n")
    for folder in ("tasks", "rubrics"):
        directory = project / "datasets/engineering-v2" / folder
        directory.mkdir(parents=True)
        (directory / "fixture.json").write_text("{}\n")
    return project, {"core_sha256": {"fixture.py": checker.digest(core / "fixture.py")},
                     "uv_lock_sha256": checker.digest(project / "uv.lock"),
                     "frozen_experiment_fingerprint": assembler.fingerprint(project)}


@pytest.mark.parametrize("kind", ["core", "lock", "fingerprint"])
def test_provenance_must_match_verified_source_before_deb_claim(source_fixture, kind):
    project, provenance = source_fixture
    if kind == "core":
        provenance["core_sha256"]["fixture.py"] = "0" * 64
    elif kind == "lock":
        provenance["uv_lock_sha256"] = "0" * 64
    else:
        provenance["frozen_experiment_fingerprint"] = "0" * 64
    with pytest.raises(checker.Rejected):
        checker.verify_frozen_source(project, provenance)


def test_frozen_source_fingerprint_and_hashes_match(source_fixture):
    project, provenance = source_fixture
    assert checker.verify_frozen_source(project, provenance) == {
        "core_and_lock_match_verified_source": True,
        "experiment_fingerprint": provenance["frozen_experiment_fingerprint"]}


@pytest.mark.parametrize("name", ["/absolute", "../escape", "root/../../escape", "root\\escape",
                                ".env.local", "root/.git/config", "root/with\nnewline"])
def test_unsafe_archive_names_rejected(name):
    with pytest.raises(checker.Rejected):
        checker.safe_name(name)


@pytest.mark.parametrize("kind", ["symlink", "setuid"])
def test_zip_link_or_privileged_mode_rejected_before_extraction(tmp_path, kind):
    path = tmp_path / "fixture.zip"
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("root/fixture.txt")
        info.external_attr = ((stat.S_IFLNK | 0o777) if kind == "symlink" else (stat.S_IFREG | 0o4755)) << 16
        archive.writestr(info, "fixture")
    output = tmp_path / "extract"
    with pytest.raises(checker.Rejected):
        checker.zip_inventory(path, output)
    assert not output.exists()


def test_tar_link_rejected_before_extraction(tmp_path):
    path = tmp_path / "fixture.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("root/linked")
        info.type, info.linkname = tarfile.SYMTYPE, "../target"
        archive.addfile(info)
    output = tmp_path / "extract"
    with pytest.raises(checker.Rejected, match="tar_link_or_special_member"):
        checker.tar_inventory(path, output)
    assert not output.exists()


def test_zip_crc_corruption_rejected(tmp_path):
    path = tmp_path / "fixture.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("root/fixture.txt", b"unique-fixture-body")
    value = path.read_bytes().replace(b"unique-fixture-body", b"changed-fixture-body")
    path.write_bytes(value)
    with pytest.raises(checker.Rejected, match="zip_crc_failed"):
        checker.zip_inventory(path)


def test_zip_source_bytes_must_match_manifest_not_only_member_names(tmp_path):
    path = tmp_path / "source.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("openkylin-memory-bench/fixture.py", "altered fixture")
    with pytest.raises(checker.Rejected, match="source_zip_sha256_mismatch"):
        checker.verify_source_zip(path, {"fixture.py": "0" * 64})


def test_tar_safe_member_extraction_preserves_executable_permission(tmp_path):
    path = tmp_path / "fixture.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        value = b"# synthetic fixture\n"
        info = tarfile.TarInfo("root/script.py")
        info.mode, info.size = 0o755, len(value)
        archive.addfile(info, io.BytesIO(value))
    output = tmp_path / "extract"
    assert checker.tar_inventory(path, output) == ["root/script.py"]
    assert (output / "root/script.py").stat().st_mode & 0o777 == 0o755


def test_html_missing_file_and_fragment_are_not_accepted(tmp_path):
    page = tmp_path / "index.html"
    page.write_text('<a href="missing.json">bad</a>')
    with pytest.raises(checker.Rejected, match="broken_html_link"):
        checker.html_links(tmp_path)
    page.write_text('<a href="#missing">bad</a><div id="good"></div>')
    with pytest.raises(checker.Rejected, match="broken_html_fragment"):
        checker.html_links(tmp_path)
    page.write_text('<a href="#good">good</a><div id="good"></div>')
    assert checker.html_links(tmp_path)["local_links_checked"] == 1
