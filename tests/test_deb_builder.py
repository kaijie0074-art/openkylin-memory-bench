"""Only temporary fixtures/mocks: these tests do not build or install Linux packages."""
import argparse
import importlib.util
import json
import tomllib
import zipfile
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deb_builder", PROJECT / "scripts/build-deb.py")
deb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deb)


def make_wheel(path, name, version, files, requires=()):
    info = f"{name.replace('-', '_')}-{version}.dist-info"
    metadata = f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\nRequires-Python: >=3.11\n"
    metadata += "".join(f"Requires-Dist: {value}\n" for value in requires)
    with zipfile.ZipFile(path, "w") as archive:
        for key, value in files.items():
            archive.writestr(key, value)
        archive.writestr(info + "/METADATA", metadata)
        archive.writestr(info + "/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
        archive.writestr(info + "/licenses/LICENSE", "test license\n")
        if name == deb.PACKAGE:
            archive.writestr(info + "/entry_points.txt", "[console_scripts]\nkmb = kmb.cli:main\n")
    return deb.digest(path)


def elf_bytes(machine=183, elf_class=2, elf_data=1, elf_type=3):
    header = bytearray(64)
    header[:7] = b"\x7fELF" + bytes([elf_class, elf_data, 1])
    order = "little" if elf_data == 1 else "big"
    header[16:18] = elf_type.to_bytes(2, order)
    header[18:20] = machine.to_bytes(2, order)
    return bytes(header)


@pytest.fixture
def release(tmp_path):
    root = tmp_path / "source"
    (root / "src/kmb").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "scripts").mkdir()
    (root / "src/kmb/__init__.py").write_text("")
    (root / "src/kmb/cli.py").write_text("def main(): return 0\n")
    (root / "data/LICENSE").write_text("test data license\n")
    (root / "scripts/run-benchmark.py").write_text("# isolated fixture\n")
    (root / "LICENSE").write_text("test license\n")
    (root / "pyproject.toml").write_text('''[project]
name = "openkylin-memory-bench"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["example>=1,<2"]
''')
    house = tmp_path / "wheelhouse"
    house.mkdir()
    dep = house / "example-1.0-py3-none-any.whl"
    dep_hash = make_wheel(dep, "example", "1.0", {"example.py": ""})
    app = house / "openkylin_memory_bench-0.1.0-py3-none-any.whl"
    files = {"kmb/__init__.py": "", "kmb/cli.py": "def main(): return 0\n",
             "kmb/data/LICENSE": "test data license\n"}
    app_hash = make_wheel(app, deb.PACKAGE, "0.1.0", files, ["example<2,>=1"])
    (root / "uv.lock").write_text(f'''version = 1
[[package]]
name = "openkylin-memory-bench"
version = "0.1.0"
dependencies = [{{name = "example"}}]
[[package]]
name = "example"
version = "1.0"
wheels = [{{url="https://example.invalid/example-1.0-py3-none-any.whl", hash="sha256:{dep_hash}"}}]
''')
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(f"example==1.0 --hash=sha256:{dep_hash}\n"
                            f"openkylin-memory-bench==0.1.0 --hash=sha256:{app_hash}\n")
    return argparse.Namespace(source_root=root, wheelhouse=house, requirements=requirements,
                              wheel=app, output=tmp_path / "output", python=Path("/usr/bin/python3"),
                              build=False, dry_run=False)


def argv(args):
    return ["--source-root", str(args.source_root), "--wheelhouse", str(args.wheelhouse),
            "--requirements", str(args.requirements), "--wheel", str(args.wheel),
            "--output", str(args.output)]


def test_default_inspection_is_read_only(release, monkeypatch, capsys):
    def forbidden(*a, **k):
        raise AssertionError("inspection must not run commands")
    monkeypatch.setattr(deb.subprocess, "run", forbidden)
    assert deb.main(argv(release)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "inputs_inspected" and result["dependency_count"] == 2
    assert result["native_abi_and_linux_installation_verified"] is False
    assert not release.output.exists()


def test_current_lock_closure_is_83_with_markers_and_extras():
    closure = deb.runtime_closure(tomllib.loads((PROJECT / "uv.lock").read_text()))
    assert len(closure) == 83
    assert closure["numpy"]["version"] == "2.5.3"
    assert "linkify-it-py" in closure and "colorama" not in closure
    assert not {"pytest", "hypothesis", "ruff"} & set(closure)


@pytest.mark.parametrize("marker", ["os_name == 'posix'", "python_version >= '3.12'",
                                    "python_full_version >= '3.12' or extra == 'unsafe'"])
def test_unknown_marker_does_not_guess(marker):
    with pytest.raises(deb.Rejected, match="unsupported_lock_marker"):
        deb.marker_applies(marker)


@pytest.mark.parametrize("kind", ["missing", "hash", "extra", "duplicate", "foreign", "symlink"])
def test_bad_wheelhouse_rejected(release, kind):
    dep = release.wheelhouse / "example-1.0-py3-none-any.whl"
    if kind == "missing":
        dep.unlink()
    elif kind == "hash":
        dep.write_bytes(dep.read_bytes() + b"changed")
    elif kind == "extra":
        make_wheel(release.wheelhouse / "extra-1.0-py3-none-any.whl", "extra", "1.0", {})
    elif kind == "duplicate":
        (release.wheelhouse / "example-1.0-1-py3-none-any.whl").write_bytes(dep.read_bytes())
    elif kind == "foreign":
        dep.rename(release.wheelhouse / "example-1.0-cp312-cp312-macosx_11_0_arm64.whl")
    else:
        old = release.source_root / "example.whl"
        dep.rename(old)
        dep.symlink_to(old)
    with pytest.raises(deb.Rejected):
        deb.inspect_inputs(release)
    assert not release.output.exists()


@pytest.mark.parametrize("replacement", ["example>=1", "-e .", "--index-url https://example.invalid",
                                         "example @ https://example.invalid/a.whl", "example==1.0"])
def test_requirements_must_be_locked_hashed_names(release, replacement):
    release.requirements.write_text(replacement + "\n")
    with pytest.raises(deb.Rejected):
        deb.inspect_inputs(release)


def test_missing_project_requirement_not_silently_added(release):
    release.requirements.write_text(release.requirements.read_text().splitlines()[0] + "\n")
    with pytest.raises(deb.Rejected, match="closure_mismatch"):
        deb.inspect_inputs(release)


def test_same_version_old_wheel_rejected(release):
    (release.source_root / "src/kmb/cli.py").write_text("def main(): return 7\n")
    with pytest.raises(deb.Rejected, match="source_mismatch"):
        deb.inspect_inputs(release)


def test_extra_wheel_payload_is_rejected(release):
    with zipfile.ZipFile(release.wheel, "a") as archive:
        archive.writestr("personal-report.json", "must not ship")
    with pytest.raises(deb.Rejected, match="source_mismatch"):
        deb.inspect_inputs(release)


def test_filename_must_agree_with_metadata(release):
    path = release.wheelhouse / "example-1.0-py3-none-any.whl"
    path.rename(release.wheelhouse / "different-1.0-py3-none-any.whl")
    with pytest.raises(deb.Rejected, match="filename_metadata"):
        deb.inspect_inputs(release)


def test_wheel_hash_is_bound_to_the_same_locked_artifact_filename(release):
    lock = release.source_root / "uv.lock"
    lock.write_text(lock.read_text().replace("example-1.0-py3-none-any.whl", "example-1.0-cp312-cp312-win_amd64.whl"))
    with pytest.raises(deb.Rejected, match="lock_artifact_mismatch"):
        deb.inspect_inputs(release)


def test_renamed_foreign_wheel_cannot_hide_internal_tags(release):
    original = release.wheelhouse / "example-1.0-py3-none-any.whl"
    with zipfile.ZipFile(original) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    contents["example-1.0.dist-info/WHEEL"] = b"Wheel-Version: 1.0\nTag: cp312-cp312-win_amd64\n"
    contents["example.pyd"] = b"MZsynthetic"
    with zipfile.ZipFile(original, "w") as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    # Even a supposedly locked hash must not override the filename/tag mismatch.
    with pytest.raises(deb.Rejected, match="tag_metadata_mismatch"):
        deb.wheel_info(original)


def test_wheel_zip_traversal_is_rejected(release):
    with zipfile.ZipFile(release.wheel, "a") as archive:
        archive.writestr("../escaped", "bad")
    with pytest.raises(deb.Rejected, match="unsafe_wheel_member"):
        deb.inspect_inputs(release)


def test_build_on_mac_rejected_before_output_or_install(release, monkeypatch):
    monkeypatch.setattr(deb.sys, "platform", "darwin")
    monkeypatch.setattr(deb.subprocess, "run", lambda *a, **k: pytest.fail("must not run commands"))
    assert deb.main([*argv(release), "--build"]) == 2
    assert not release.output.exists()


def test_output_existing_or_inside_input_rejected(release):
    release.output.mkdir()
    with pytest.raises(deb.Rejected, match="output_already_exists"):
        deb.fresh_output(release.output, release)
    with pytest.raises(deb.Rejected, match="protected_input"):
        deb.fresh_output(release.wheelhouse / "new", release)
    with pytest.raises(deb.Rejected, match="protected_input"):
        deb.fresh_output(release.source_root / "src/new", release)


@pytest.mark.parametrize("name,value,code", [
    ("bin/kmb", "/temporary/build/python", "build_path"),
    ("site/.env", "not a key", "forbidden_payload"),
    ("site/.local.env", "not a key", "forbidden_payload"),
    ("site/.env.local", "not a key", "forbidden_payload"),
    ("site/__editable__.pth", "import x", "editable_payload"),
    ("x.dist-info/direct_url.json", "{}", "direct_url_payload"),
    ("site/config", "fake-secret-for-test", "credential_in_payload"),
])
def test_payload_rejects_leaks(tmp_path, name, value, code):
    stage = tmp_path / "stage"
    file = stage / name
    file.parent.mkdir(parents=True)
    file.write_text(value)
    with pytest.raises(deb.Rejected, match=code):
        deb.scan_payload(stage, ["/temporary/build"], ["fake-secret-for-test"])


def test_cache_symlink_rejected_but_system_python_chain_allowed(tmp_path):
    stage = tmp_path / "stage"
    binaries = stage / deb.INSTALL / "bin"
    binaries.mkdir(parents=True)
    (binaries / "python").symlink_to("/usr/bin/python3")
    (binaries / "python3").symlink_to("python")
    deb.scan_payload(stage, [], [])
    (stage / "cache-link").symlink_to("/private/cache/distribution")
    with pytest.raises(deb.Rejected, match="external_payload_symlink"):
        deb.scan_payload(stage, [], [])


def test_installed_metadata_license_loss_is_rejected(release, tmp_path, monkeypatch):
    plan = deb.inspect_inputs(release)
    venv = tmp_path / "venv"
    site = venv / "lib/python3.12/site-packages"
    for info in plan["wheels"].values():
        with zipfile.ZipFile(info["path"]) as archive:
            archive.extractall(site)  # Only these tiny test-generated fixtures.
    monkeypatch.setattr(deb, "command", lambda *a: json.dumps(
        {"prefix": str(venv), "base_prefix": "/usr", "packages": list(plan["expected"].items())}))
    result = deb.verify_installed(venv, plan["expected"], plan["wheels"], tmp_path, [])
    assert result["count"] == 2 and result["metadata_files_verified"] >= 6
    next(site.glob("example-*.dist-info/licenses/LICENSE")).unlink()
    with pytest.raises(deb.Rejected, match="metadata_or_license"):
        deb.verify_installed(venv, plan["expected"], plan["wheels"], tmp_path, [])


def test_installed_extra_distribution_rejected(release, tmp_path, monkeypatch):
    plan = deb.inspect_inputs(release)
    monkeypatch.setattr(deb, "command", lambda *a: json.dumps({"prefix": "wrong", "packages":
        [*plan["expected"].items(), ("unlocked", "9.9")]}))
    with pytest.raises(deb.Rejected, match="installed_closure"):
        deb.verify_installed(tmp_path, plan["expected"], plan["wheels"], tmp_path, [])


def test_ldd_missing_library_blocks_package(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "test.so").write_bytes(elf_bytes())
    monkeypatch.setattr(deb, "command", lambda *a: "libmissing.so => not found\n")
    with pytest.raises(deb.Rejected, match="missing_dynamic_library"):
        deb.dynamic_dependencies(stage, stage / deb.INSTALL, {"tools": {"ldd": "ldd"}}, tmp_path, [])


def test_native_elf_still_requires_ldd_and_records_payload(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    binary = stage / "native.so"
    binary.write_bytes(elf_bytes())
    calls = []
    def ldd(args, output, records):
        calls.append(args)
        records.append({"program": "ldd", "exit_code": 0})
        return "linux-vdso.so.1 (0x000000000001)\n"
    monkeypatch.setattr(deb, "command", ldd)
    records = []
    result = deb.dynamic_dependencies(stage, stage / deb.INSTALL, {"tools": {"ldd": "ldd"}}, tmp_path, records)
    assert calls == [["ldd", str(binary)]]
    assert result["elf_files"] == ["native.so"] and result["foreign_elf_files"] == []
    assert records[0]["payload_file"] == "native.so"


def test_exact_upstream_foreign_helper_preserved_with_identity(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    helper = stage / deb.DEBUGPY_FOREIGN_HELPER
    helper.parent.mkdir(parents=True)
    original = elf_bytes(machine=62)
    helper.write_bytes(original)
    monkeypatch.setattr(deb, "digest", lambda path: deb.DEBUGPY_FOREIGN_SHA256)
    monkeypatch.setattr(deb, "command", lambda *a: pytest.fail("foreign helper must not execute ldd"))
    result = deb.dynamic_dependencies(stage, stage / deb.INSTALL, {"tools": {"ldd": "ldd"}}, tmp_path, [])
    assert result["elf_files"] == []
    assert helper.read_bytes() == original
    recorded = result["foreign_elf_files"][0]
    assert recorded["path"] == str(deb.DEBUGPY_FOREIGN_HELPER)
    assert recorded["machine"] == 62 and recorded["sha256"] == deb.DEBUGPY_FOREIGN_SHA256
    assert recorded["action"] == "preserved_without_ldd" and "not executable" in recorded["reason"]


def test_unknown_foreign_elf_is_not_silently_skipped(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "another_amd64.so").write_bytes(elf_bytes(machine=62))
    monkeypatch.setattr(deb, "command", lambda *a: pytest.fail("foreign binary must be rejected first"))
    with pytest.raises(deb.Rejected, match="unsupported_elf_architecture"):
        deb.dynamic_dependencies(stage, stage / deb.INSTALL, {"tools": {"ldd": "ldd"}}, tmp_path, [])


@pytest.mark.parametrize("changes", [{"machine": 183}, {"elf_class": 1}, {"elf_data": 2}, {"elf_type": 2}])
def test_foreign_helper_path_with_different_header_is_rejected(tmp_path, monkeypatch, changes):
    stage = tmp_path / "stage"
    helper = stage / deb.DEBUGPY_FOREIGN_HELPER
    helper.parent.mkdir(parents=True)
    helper.write_bytes(elf_bytes(**{"machine": 62, **changes}))
    monkeypatch.setattr(deb, "digest", lambda path: deb.DEBUGPY_FOREIGN_SHA256)
    monkeypatch.setattr(deb, "command", lambda *a: pytest.fail("must reject changed helper"))
    with pytest.raises(deb.Rejected, match="unrecognized_debugpy"):
        deb.dynamic_dependencies(stage, stage / deb.INSTALL, {"tools": {"ldd": "ldd"}}, tmp_path, [])


def test_foreign_helper_same_header_but_changed_bytes_is_rejected(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    helper = stage / deb.DEBUGPY_FOREIGN_HELPER
    helper.parent.mkdir(parents=True)
    helper.write_bytes(elf_bytes(machine=62))
    monkeypatch.setattr(deb, "command", lambda *a: pytest.fail("must reject changed helper"))
    with pytest.raises(deb.Rejected, match="unrecognized_debugpy"):
        deb.dynamic_dependencies(stage, stage / deb.INSTALL, {"tools": {"ldd": "ldd"}}, tmp_path, [])


def test_environment_removes_credentials_and_loader_overrides(monkeypatch):
    for key in ["KMB_API_KEY", "UV_INDEX_URL", "PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "PIP_INDEX_URL"]:
        monkeypatch.setenv(key, "test-value")
    env = deb.safe_env()
    assert not any(k in env for k in ["KMB_API_KEY", "UV_INDEX_URL", "PYTHONPATH", "PYTHONHOME",
                                    "LD_PRELOAD", "PIP_INDEX_URL"])
    assert env["UV_OFFLINE"] == "1" and env["UV_PYTHON_DOWNLOADS"] == "never"


def test_failed_build_records_failure_never_success(release, monkeypatch):
    plan = deb.inspect_inputs(release)
    monkeypatch.setattr(deb, "build_host", lambda *a: {"tools": {"uv": "/usr/bin/uv"}})
    calls = []
    def reject(command, *args):
        calls.append(command)
        raise deb.Rejected("simulated_command_failure")
    monkeypatch.setattr(deb, "command", reject)
    with pytest.raises(deb.Rejected):
        deb.build(release, plan, release.output)
    result = json.loads((release.output / "build-result.json").read_text())
    assert result["status"] == "failed" and not result["linux_installation_verified"]
    assert "deb" not in result and len(calls) == 1
    assert "--relocatable" in calls[0] and "--offline" in calls[0]
    assert not list(release.output.glob("*.deb"))


def test_mock_success_packages_only_locked_payload_without_installing(release, monkeypatch):
    plan = deb.inspect_inputs(release)
    host = {"architecture": "arm64", "tools": {"uv": "/usr/bin/uv", "dpkg-deb": "/usr/bin/dpkg-deb"}}
    monkeypatch.setattr(deb, "build_host", lambda *a: host)
    calls = []
    def mock_command(command, cwd, records):
        calls.append([str(a) for a in command])
        if "venv" in command:
            venv = Path(command[-1])
            (venv / "bin").mkdir(parents=True)
            (venv / "bin/python").symlink_to("/usr/bin/python3")
            (venv / "bin/python3").symlink_to("python")
            deb.write_file(venv / "pyvenv.cfg", "home = /usr/bin\nrelocatable = true\n")
        elif "install" in command:
            venv = Path(command[command.index("--python") + 1]).parent.parent
            site = venv / "lib/python3.12/site-packages"
            for info in plan["wheels"].values():
                with zipfile.ZipFile(info["path"]) as archive:
                    archive.extractall(site)
        elif "-c" in command:
            venv = Path(command[0]).parent.parent
            return json.dumps({"prefix": str(venv), "base_prefix": "/usr",
                               "packages": list(plan["expected"].items())})
        elif "--build" in command:
            Path(command[-1]).write_bytes(b"mock-deb-not-installable")
        return ""
    monkeypatch.setattr(deb, "command", mock_command)
    monkeypatch.setattr(deb, "dynamic_dependencies", lambda *a: {"elf_files": [], "system_libraries": {}})
    result = deb.build(release, plan, release.output)
    assert result["status"] == "built_not_installed" and not result["linux_installation_verified"]
    stage = release.output / "stage"
    assert result["installed"]["count"] == 2
    assert "python3 (<< 3.13)" in (stage / "DEBIAN/control").read_text()
    assert "Architecture: arm64" in (stage / "DEBIAN/control").read_text()
    assert "Version: 0.1.0-3" in (stage / "DEBIAN/control").read_text()
    packaged_lock = stage / deb.INSTALL / "lib/python3.12/uv.lock"
    assert packaged_lock.read_bytes() == (release.source_root / "uv.lock").read_bytes()
    assert deb.digest(packaged_lock) == plan["inputs"]["uv.lock"]
    assert result["deb"]["filename"] == "openkylin-memory-bench_0.1.0-3_arm64.deb"
    assert "-I -m kmb.cli" in (stage / "usr/bin/kmb").read_text()
    assert "-I /usr/lib/openkylin-memory-bench/scripts/run-benchmark.py" in (stage / "usr/bin/kmb-batch").read_text()
    assert (stage / deb.SUPPORT / "data").readlink() == (
        Path("/") / deb.INSTALL / "lib/python3.12/site-packages/kmb/data")
    assert not (stage / "DEBIAN/postinst").exists()
    install = next(c for c in calls if "install" in c)
    for flag in ["--offline", "--no-index", "--no-deps", "--no-editable", "--require-hashes"]:
        assert flag in install
    assert install[install.index("--link-mode") + 1] == "copy"
    assert not any(c[0] in {"apt", "apt-get", "docker"} or "-i" in c for c in calls)


def test_packaged_layout_preserves_frozen_fingerprint(tmp_path, monkeypatch):
    import shutil

    from kmb import review

    source = tmp_path / "source"
    source_package = source / "src/kmb"
    installed_package = tmp_path / "venv/lib/python3.12/site-packages/kmb"
    for package in [source_package, installed_package]:
        package.mkdir(parents=True)
        for path in (PROJECT / "src/kmb").glob("*.py"):
            shutil.copyfile(path, package / path.name)
        shutil.copytree(PROJECT / "data", package / "data")
    shutil.copyfile(PROJECT / "uv.lock", source / "uv.lock")
    monkeypatch.setattr(review, "__file__", str(source_package / "review.py"))
    expected = review.experiment_fingerprint(source_package / "data")
    monkeypatch.setattr(review, "__file__", str(installed_package / "review.py"))
    assert review.experiment_fingerprint(installed_package / "data") != expected
    shutil.copyfile(source / "uv.lock", installed_package.parents[1] / "uv.lock")
    assert review.experiment_fingerprint(installed_package / "data") == expected


def test_permissions_are_readable_without_preserving_privilege_bits(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    script = stage / "entry"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o4700)
    data = stage / "data"
    data.write_text("public fixture")
    data.chmod(0o600)
    deb.normalize_modes(stage)
    assert stage.stat().st_mode & 0o7777 == 0o755
    assert script.stat().st_mode & 0o7777 == 0o755
    assert data.stat().st_mode & 0o7777 == 0o644


@pytest.mark.parametrize("os_id,version,machine,python_version,uv_version,code", [
    ("debian", "3.0", "aarch64", [3, 12, 2], "0.11.15", "openkylin_3"),
    ("openkylin", "2.0", "aarch64", [3, 12, 2], "0.11.15", "openkylin_3"),
    ("openkylin", "3.0", "x86_64", [3, 12, 2], "0.11.15", "linux_arm64"),
    ("openkylin", "3.0", "aarch64", [3, 13, 1], "0.11.15", "python312"),
    ("openkylin", "3.0", "aarch64", [3, 12, 2], "0.11.14", "uv_version"),
])
def test_build_host_gates(os_id, version, machine, python_version, uv_version, code,
                         tmp_path, monkeypatch):
    monkeypatch.setattr(deb.sys, "platform", "linux")
    monkeypatch.setattr(deb.platform, "machine", lambda: machine)
    original_read = Path.read_text
    def read(path, *a, **k):
        return f'ID={os_id}\nVERSION_ID="{version}"\n' if path == Path("/etc/os-release") else original_read(path, *a, **k)
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(deb.shutil, "which", lambda name: "/usr/bin/" + name)
    def run(args, *other):
        if "--version" in args:
            return f"uv {uv_version}"
        return json.dumps({"version": python_version, "prefix": "/usr", "base_prefix": "/usr"})
    monkeypatch.setattr(deb, "command", run)
    with pytest.raises(deb.Rejected, match=code):
        deb.build_host(Path("/usr/bin/python3"), tmp_path, [])


@pytest.mark.parametrize("name,accepted", [
    ("a-1-py3-none-any.whl", True),
    ("a-1-cp312-cp312-manylinux_2_28_aarch64.whl", True),
    ("a-1-cp39-abi3-manylinux2014_aarch64.whl", True),
    ("a-1-cp313-cp313-manylinux_2_28_aarch64.whl", False),
    ("a-1-cp312-cp312-manylinux_2_28_x86_64.whl", False),
    ("a-1-cp312-cp312-macosx_11_0_arm64.whl", False),
])
def test_target_wheel_precheck(name, accepted):
    assert deb.target_wheel(name) is accepted
