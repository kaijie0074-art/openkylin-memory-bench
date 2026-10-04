#!/usr/bin/env python3
"""Check a sealed R1 ZIP and optionally execute offline checks outside the source tree."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

PROJECT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = "openkylin-memory-bench"
EXECUTABLES = ("scripts/verify-frozen-rules.py", "scripts/build-deb.py", "scripts/run-benchmark.py",
               "scripts/assemble-r1-delivery.py", "scripts/check-r1-delivery.py")
REQUIRED_SOURCE = (*EXECUTABLES, "containers/openclaw.Dockerfile", "containers/hermes.Dockerfile",
                   "scripts/revise-demo-video.py", "scripts/build-introduction-pdf.py",
                   "competition/publication/render-captioned-video.py")
REQUIRED_RECORDS = {"reports/desktop-recording-final-20261002/edited-highlights-20261002.mp4":
                    "86de24a148bc1ba502dc23ceab8b8edd78b82c083ff60030ec2c6c2b5417b952"}


class Rejected(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Rejected(message)


def verify_rebuild_records(hashes):
    require(all(hashes.get(name) == value for name, value in REQUIRED_RECORDS.items()),
            "video_rebuild_input_missing_or_changed")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def local(path):
    path = path.expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    require(not any(p.is_symlink() for p in (path, *path.parents)), "symlink_path_forbidden")
    return path.resolve()


def safe_name(name):
    path = PurePosixPath(name)
    require(name and not path.is_absolute() and ".." not in path.parts and "\\" not in name
            and not any(ord(character) < 32 for character in name), "unsafe_archive_member")
    require(not any(part in {".git", ".runtime", ".venv"} or "ready-to-submit" in part
                    or "first-render" in part for part in path.parts), "private_archive_member")
    require(not any(part.startswith(".env") and part != ".env.example" for part in path.parts), "private_configuration_member")
    return path


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_field")
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def checksums(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        sha, separator, name = line.partition("  ")
        require(separator and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha), "invalid_checksum_line")
        safe_name(name)
        require(name not in result, "duplicate_checksum_entry")
        result[name] = sha
    require(result, "empty_checksum_list")
    return result


def zip_inventory(path, extract=None):
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        require(len(members) == len({m.filename for m in members}), "duplicate_zip_member")
        for member in members:
            relative = safe_name(member.filename)
            mode = member.external_attr >> 16
            require(not stat.S_ISLNK(mode) and (not stat.S_IFMT(mode) or stat.S_ISREG(mode) or member.is_dir()), "non_regular_zip_member")
            require(not mode & 0o7000, "privileged_archive_mode")
            require(not member.flag_bits & 1, "encrypted_zip_not_supported")
            require(member.file_size <= 4_000_000_000, "archive_member_too_large")
            if extract is not None:
                destination = extract / str(relative)
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
                    destination.chmod(mode & 0o777 or 0o644)
        require(archive.testzip() is None, "zip_crc_failed")
        return [m.filename for m in members if not m.is_dir()]


def tar_inventory(path, extract=None):
    with tarfile.open(path, "r:*") as archive:
        members = archive.getmembers()
        require(len(members) == len({m.name for m in members}), "duplicate_tar_member")
        for member in members:
            relative = safe_name(member.name)
            require(member.isfile() or member.isdir(), "tar_link_or_special_member")
            require(not member.mode & 0o7000, "privileged_archive_mode")
            require(member.size <= 4_000_000_000, "archive_member_too_large")
            if extract is not None:
                destination = extract / str(relative)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
                    destination.chmod(member.mode & 0o777 or 0o644)
        return [m.name for m in members if m.isfile()]


def verify_folder(folder):
    expected = checksums(folder / "checksums.sha256")
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    require(actual == set(expected) | {"checksums.sha256"}, "delivery_inventory_mismatch")
    for name, value in expected.items():
        require(digest(folder / name) == value, f"delivery_sha256_mismatch:{name}")
    manifest = read_json(folder / "交付清单.json")
    require(manifest["revision"] == "R1" and manifest["new_experiment"] is False, "unexpected_delivery_identity")
    require(set(manifest["files"]) == set(expected) - {"交付清单.json"}, "manifest_inventory_mismatch")
    for name, row in manifest["files"].items():
        require(row["sha256"] == expected[name] and row["bytes"] == (folder / name).stat().st_size, "manifest_hash_or_size_mismatch")
    return manifest


def verify_source_zip(path, expected):
    with zipfile.ZipFile(path) as archive:
        for name, value in expected.items():
            hashed = hashlib.sha256()
            with archive.open(SOURCE_ROOT + "/" + name) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hashed.update(block)
            require(hashed.hexdigest() == value, f"source_zip_sha256_mismatch:{name}")


def verify_frozen_source(project, provenance):
    core = {p.name: digest(p) for p in sorted((project / "src/kmb").glob("*.py"))}
    lock = digest(project / "uv.lock")
    require(core, "frozen_source_core_missing")
    require(core == provenance["core_sha256"] and lock == provenance["uv_lock_sha256"], "provenance_core_or_lock_disagrees_with_source")
    value = hashlib.sha256()
    for path in sorted((project / "src/kmb").glob("*.py")):
        value.update(path.name.encode() + path.read_bytes())
    for folder in ("tasks", "rubrics"):
        for path in sorted((project / "datasets/engineering-v2" / folder).glob("*.json")):
            value.update((folder + "/" + path.name).encode() + path.read_bytes())
    value.update((project / "uv.lock").read_bytes())
    require(value.hexdigest() == provenance["frozen_experiment_fingerprint"], "frozen_source_fingerprint_mismatch")
    return {"core_and_lock_match_verified_source": True, "experiment_fingerprint": value.hexdigest()}


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []
        self.ids = set()
    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key == "id" and value:
                self.ids.add(value)
            if key in {"href", "src"} and value:
                self.references.append(value)


def html_links(root):
    pages = list(root.rglob("*.html"))
    parsed = {}
    for page in pages:
        parser = Links()
        parser.feed(page.read_text(encoding="utf-8"))
        parsed[page] = parser
    checked = 0
    for page, parser in parsed.items():
        for reference in parser.references:
            link = urlsplit(reference)
            if link.scheme or link.netloc:
                require(link.scheme != "file", "machine_local_html_link")
                continue  # External URLs are not probed by this offline check.
            destination = (page.parent / unquote(link.path)).resolve() if link.path else page
            require(destination.is_relative_to(root.resolve()) and destination.exists(), f"broken_html_link:{page.relative_to(root)}:{reference}")
            if link.fragment and destination in parsed:
                require(unquote(link.fragment) in parsed[destination].ids, f"broken_html_fragment:{reference}")
            checked += 1
    return {"html_pages": len(pages), "local_links_checked": checked, "external_urls_requested": 0}


def ar_members(path):
    with path.open("rb") as stream:
        require(stream.read(8) == b"!<arch>\n", "invalid_deb_ar_header")
        while header := stream.read(60):
            require(len(header) == 60 and header[-2:] == b"`\n", "invalid_deb_ar_member")
            name, size = header[:16].decode("ascii").strip().removesuffix("/"), int(header[48:58])
            require(size < 2_000_000_000, "deb_ar_member_too_large")
            data = stream.read(size)
            require(len(data) == size, "truncated_deb_ar_member")
            if size % 2:
                require(stream.read(1) == b"\n", "invalid_deb_ar_padding")
            yield name, data


def check_deb(path, provenance):
    identity, core, lock, verifier_sha, wrapper = None, {}, None, None, None
    members = list(ar_members(path))
    require(len({name for name, _ in members}) == len(members), "duplicate_deb_ar_member")
    require(dict(members).get("debian-binary") == b"2.0\n", "unsupported_deb_version")
    for name, data in members:
        if not name.startswith(("control.tar", "data.tar")):
            continue
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            for member in archive.getmembers():
                normalized = member.name.removeprefix("./")
                if normalized in {"", "."}:
                    continue
                safe_name(normalized)
                if not member.isfile():
                    continue  # Never extract or execute .deb content, including its interpreter links.
                source = archive.extractfile(member)
                if name.startswith("control.tar") and normalized == "control":
                    identity = dict(line.split(": ", 1) for line in source.read().decode().splitlines() if ": " in line)
                elif normalized.startswith("opt/openkylin-memory-bench/venv/lib/python3.12/site-packages/kmb/") and normalized.endswith(".py"):
                    relative = normalized.removeprefix("opt/openkylin-memory-bench/venv/lib/python3.12/site-packages/kmb/")
                    if "/" not in relative:
                        core[relative] = hashlib.sha256(source.read()).hexdigest()
                elif normalized == "opt/openkylin-memory-bench/venv/lib/python3.12/uv.lock":
                    lock = hashlib.sha256(source.read()).hexdigest()
                elif normalized == "usr/lib/openkylin-memory-bench/scripts/verify-frozen-rules.py":
                    verifier_sha = hashlib.sha256(source.read()).hexdigest()
                elif normalized == "usr/bin/kmb-verify-rules":
                    wrapper = source.read().decode()
    require(identity and identity.get("Version") == "0.1.0-4" and identity.get("Architecture") == "arm64", "deb_identity_mismatch")
    require(core == provenance["core_sha256"] and lock == provenance["uv_lock_sha256"], "deb_frozen_core_or_lock_changed")
    require(verifier_sha == provenance["source_files_sha256"]["scripts/verify-frozen-rules.py"], "deb_verifier_source_mismatch")
    require(wrapper and " -I /usr/lib/openkylin-memory-bench/scripts/verify-frozen-rules.py" in wrapper, "deb_verifier_wrapper_missing")
    return {"version": "0.1.0-4", "architecture": "arm64", "core_and_lock_match": True,
            "verifier_sha256": verifier_sha, "installation_executed": False}


OFFLINE_RUNNER = """import sys,runpy,socket,subprocess
sys.dont_write_bytecode=True
sys.path.insert(0,sys.argv[1])
def denied(*a,**k): raise RuntimeError('offline_delivery_check_forbids_network_provider_and_subprocess')
socket.create_connection=denied
socket.socket.connect=denied
socket.socket.connect_ex=denied
subprocess.run=denied
import httpx
httpx.Client=denied
httpx.AsyncClient=denied
import kmb.provider
kmb.provider.JudgeProvider.from_env=denied
import kmb.cli
kmb.cli.configured_provider=denied
mode,target=sys.argv[2:4]
sys.argv=[target,*sys.argv[4:]]
if mode=='script': runpy.run_path(target,run_name='__main__')
else: runpy.run_module(target,run_name='__main__')
"""


def command(python, project, args, output, label):
    environment = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
    environment.update(PYTHONDONTWRITEBYTECODE="1", UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never")
    invocation = [str(python), "-I", "-B", "-c", OFFLINE_RUNNER, str(project / "src"), *args]
    completed = subprocess.run(invocation, cwd=project, env=environment, capture_output=True,
                               text=True, timeout=900, check=False)
    (output / f"{label}.stdout.log").write_text(completed.stdout)
    (output / f"{label}.stderr.log").write_text(completed.stderr)
    require(completed.returncode == 0, f"offline_command_failed:{label}:{completed.returncode}")
    return {"label": label, "exit_code": completed.returncode, "source": "extracted_R1_archive",
            "python_dependency_environment": "existing_not_clean_install", "model_calls": 0, "agent_runs": 0}


def check(args):
    archive, release_list, output = local(args.archive), local(args.release_checksums), local(args.output)
    require(archive.is_file() and release_list.is_file(), "release_inputs_missing")
    require(not output.exists() and not output.is_relative_to(PROJECT), "output_must_be_new_and_outside_source_tree")
    expected = checksums(release_list)
    require(archive.name in expected and digest(archive) == expected[archive.name], "release_archive_sha256_mismatch")
    names = zip_inventory(archive)
    roots = {PurePosixPath(name).parts[0] for name in names}
    require(len(roots) == 1, "delivery_archive_root_ambiguous")
    output.mkdir(parents=True, exist_ok=False)
    unpack = output / "delivery"
    unpack.mkdir()
    zip_inventory(archive, unpack)
    folder = unpack / next(iter(roots))
    manifest = verify_folder(folder)
    provenance = read_json(folder / "07_验收记录/来源与冻结指纹.json")
    source_tar, source_zip = folder / "03_源码与复现/源码.tar.gz", folder / "03_源码与复现/源码.zip"
    source_members = tar_inventory(source_tar)
    zip_members = zip_inventory(source_zip)
    expected_source = {SOURCE_ROOT + "/" + name for name in provenance["source_files_sha256"]}
    require(set(source_members) == set(zip_members) == expected_source, "source_archive_inventory_mismatch")
    require(set(REQUIRED_SOURCE) <= set(provenance["source_files_sha256"]), "required_source_file_missing")
    verify_source_zip(source_zip, provenance["source_files_sha256"])
    workspace = output / "workspace"
    workspace.mkdir()
    tar_inventory(source_tar, workspace)
    project = workspace / SOURCE_ROOT
    for name, value in provenance["source_files_sha256"].items():
        require(digest(project / name) == value, f"source_archive_sha256_mismatch:{name}")
    verified_source = verify_frozen_source(project, provenance)
    for name in EXECUTABLES:
        require((project / name).stat().st_mode & 0o111, f"script_permission_missing:{name}")
    records = folder / "08_完整实验凭证.tar.gz"
    report = {"schema_version": 1, "revision": "R1", "status": "checking", "kind": manifest["kind"],
              "archive_sha256": expected[archive.name], "release_checksums_verified": True,
              "zip_crc_verified": True, "safe_archive_members_verified": True,
              "source_files_verified": len(expected_source), "source_script_permissions_verified": True,
              "host": {"os": platform.system(), "machine": platform.machine()},
              "verification_environment": "controlled_existing_dependency_environment",
              "clean_openkylin_installation": False, "model_calls": 0, "agent_runs": 0,
              "frozen_experiment_fingerprint": provenance["frozen_experiment_fingerprint"], "commands": []}
    report["frozen_source"] = verified_source
    if manifest["kind"] == "complete":
        verify_rebuild_records(provenance["record_files_sha256"])
        record_members = tar_inventory(records)
        require(set(record_members) == set(provenance["record_files_sha256"]), "record_archive_inventory_mismatch")
        tar_inventory(records, project)
        for name, value in provenance["record_files_sha256"].items():
            require(digest(project / name) == value, f"record_archive_sha256_mismatch:{name}")
        protocol = read_json(project / "reports/engineering-v3-protocol-20261001.json")
        require(protocol["experiment_fingerprint"] == verified_source["experiment_fingerprint"], "protocol_source_fingerprint_mismatch")
        report["original_record_bytes_verified"] = len(record_members)
        deb = list((folder / "05_安装包").glob("*.deb"))
        require(len(deb) == 1, "deb_missing_or_ambiguous")
        report["deb"] = check_deb(deb[0], provenance)
        if args.run_offline:
            python = args.python.expanduser().absolute()
            require(python.is_file(), "existing_python_missing")
            report["commands"].append(command(python, project, ["script", str(project / "scripts/verify-frozen-rules.py"),
                "--dataset", "datasets/engineering-v2", "--evidence", "reports/engineering-v3-holdout-20261001/runs",
                "--scores", "reports/engineering-v3-holdout-20261001/scores", "--protocol", "reports/engineering-v3-protocol-20261001.json",
                "--output", str(output / "rule-verification")], output, "verify-rules"))
            verification = read_json(output / "rule-verification/verification.json")
            require(verification["different_rule_count"] == 0 and verification["verified_rule_count"] == 108, "offline_rules_mismatch")
            require(verification["experiment_fingerprint"] == provenance["frozen_experiment_fingerprint"], "extracted_fingerprint_changed")
            report["offline_rules"] = {key: verification[key] for key in (
                "verification_scope", "B_C_verification", "matching_rule_count", "different_rule_count",
                "input_hashes_unchanged", "verifier_sha256")}
            report["commands"].append(command(python, project, ["module", "kmb.cli", "--dataset", "datasets/engineering-v2",
                "report", "--evidence", "reports/engineering-v3-holdout-20261001/runs", "--scores", "reports/engineering-v3-holdout-20261001/scores",
                "--protocol", "reports/engineering-v3-protocol-20261001.json", "--output", str(output / "regenerated-report")], output, "regenerate-report"))
            report["regenerated_report_links"] = html_links(output / "regenerated-report")
    else:
        require(manifest["kind"] == "slim" and not records.exists(), "unexpected_slim_payload")
        require(not (folder / "04_正式实验报告/report").exists(), "slim_must_not_include_incomplete_formal_report")
        require(not args.run_offline, "slim_archive_cannot_replace_complete_offline_verification")
    report["delivery_html_links"] = html_links(folder)
    report["status"] = "passed" if not args.run_offline or len(report["commands"]) == 2 else "failed"
    (output / "check-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--release-checksums", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory outside this source tree")
    parser.add_argument("--python", type=Path, default=Path(sys.executable), help="Existing dependencies, never installs")
    parser.add_argument("--run-offline", action="store_true", help="Complete archive only: recompute A and regenerate report")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(check(args), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc), "clean_openkylin_installation": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
