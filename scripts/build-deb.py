#!/usr/bin/env python3
"""Inspect by default; explicitly build one offline openKylin 3.0 ARM64 package.

This is a narrow release tool, not a cross-platform packaging framework. It uses
only the standard library and never downloads dependencies or installs a .deb.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import zipfile
from collections import deque
from email.parser import BytesParser
from itertools import product
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = "openkylin-memory-bench"
PACKAGE_REVISION = "3"
INSTALL = Path("opt/openkylin-memory-bench/venv")
SUPPORT = Path("usr/lib/openkylin-memory-bench")
UV_VERSION = "0.11.15"
TARGET_PYTHON = (3, 12, 2)
DEBUGPY_FOREIGN_HELPER = (INSTALL / "lib/python3.12/site-packages/debugpy/_vendored/pydevd/"
                         "pydevd_attach_to_process/attach_linux_amd64.so")
# Observed in the locked debugpy 1.8.22 universal wheel, and matched on the guest.
DEBUGPY_FOREIGN_SHA256 = "8fafec366c6a3b38d3429c7a9ad9957fd029f170eac9fd37bdecbbbebb3e9441"


class Rejected(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise Rejected(code)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def marker_applies(marker, version=TARGET_PYTHON):
    """Only the marker grammar present in this project's lock is supported."""
    if not marker:
        return True
    match = re.fullmatch(r"python_full_version\s*(>=|<=|==|!=|<|>)\s*'([0-9.]+)'", marker)
    if match:
        target = tuple(map(int, match[2].split(".")))
        target += (0,) * (3 - len(target))
        return {">=": version >= target, "<=": version <= target, "==": version == target,
                "!=": version != target, "<": version < target, ">": version > target}[match[1]]
    if marker == "sys_platform == 'win32'":
        return False
    raise Rejected("unsupported_lock_marker")


def runtime_closure(lock):
    packages = lock["package"]
    roots = [p for p in packages if canonical(p["name"]) == PACKAGE]
    require(len(roots) == 1, "project_missing_or_ambiguous_in_lock")
    queue = deque([(roots[0], ())])
    found, expanded = {}, set()
    while queue:
        package, extras = queue.popleft()
        name = canonical(package["name"])
        require(name not in found or found[name]["version"] == package["version"],
                "conflicting_runtime_versions")
        found[name] = package
        for scope in (None, *extras):
            if (name, scope) in expanded:
                continue
            expanded.add((name, scope))
            if scope is None:
                deps = package.get("dependencies", [])
            else:
                require(scope in package.get("optional-dependencies", {}), "unknown_lock_extra")
                deps = package["optional-dependencies"][scope]
            for dep in deps:
                require(not set(dep) - {"name", "version", "source", "marker", "extra"},
                        "unsupported_dependency_fields")
                if not marker_applies(dep.get("marker")):
                    continue
                matches = [p for p in packages if canonical(p["name"]) == canonical(dep["name"])
                           and ("version" not in dep or dep["version"] == p["version"])
                           and all(marker_applies(m) for m in p.get("resolution-markers", []))]
                require(len(matches) == 1, "ambiguous_lock_dependency")
                queue.append((matches[0], tuple(dep.get("extra", []))))
    return found


def read_requirements(path, lock, closure, project_hash):
    text = path.read_text(encoding="utf-8")
    require(len(text) < 2_000_000, "requirements_too_large")
    records, pending = [], ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pending += " " + line.removesuffix("\\").strip()
        if not line.endswith("\\"):
            records.append(pending.strip())
            pending = ""
    require(not pending, "unfinished_requirements_line")
    selected = {}
    for line in records:
        base, *hashes = line.split(" --hash=")
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9.+!-]+)(?:\s*;\s*(.+))?", base)
        require(match is not None and hashes, "requirements_must_be_exact_hashed_names")
        require(all(re.fullmatch(r"sha256:[0-9a-f]{64}", h) for h in hashes),
                "invalid_requirement_hash")
        name, version, marker = canonical(match[1]), match[2], match[3]
        packages = [p for p in lock["package"]
                    if canonical(p["name"]) == name and p["version"] == version]
        require(len(packages) == 1, "requirement_not_in_lock")
        p = packages[0]
        allowed = ({"sha256:" + project_hash} if name == PACKAGE else
                   {a["hash"] for a in [*p.get("wheels", []), p.get("sdist", {})] if "hash" in a})
        require(set(hashes) <= allowed, "requirement_hash_not_in_lock")
        if marker_applies(marker):
            require(name not in selected, "duplicate_active_requirement")
            selected[name] = {"version": version, "hashes": set(hashes)}
    require({n: p["version"] for n, p in selected.items()} ==
            {n: p["version"] for n, p in closure.items()}, "requirements_runtime_closure_mismatch")
    return selected


def wheel_info(path):
    require(path.is_file() and not path.is_symlink() and path.suffix == ".whl", "invalid_wheel_file")
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        require(len({m.filename for m in members}) == len(members), "duplicate_wheel_member")
        require(sum(m.file_size for m in members) <= 2_000_000_000, "wheel_too_large")
        for item in members:
            parts = PurePosixPath(item.filename).parts
            require(parts and not item.filename.startswith("/") and ".." not in parts
                    and "\\" not in item.filename
                    and not stat.S_ISLNK(item.external_attr >> 16), "unsafe_wheel_member")
        metadata = [m.filename for m in members if re.fullmatch(r"[^/]+\.dist-info/METADATA", m.filename)]
        require(len(metadata) == 1, "invalid_wheel_metadata")
        message = BytesParser().parsebytes(archive.read(metadata[0]))
        filename = path.name.removesuffix(".whl").split("-")
        require(len(filename) in {5, 6} and canonical(filename[0]) == canonical(message["Name"] or "")
                and filename[1] == message["Version"], "wheel_filename_metadata_mismatch")
        wheel_metadata = BytesParser().parsebytes(archive.read(metadata[0].replace("METADATA", "WHEEL")))
        filename_tags = {"-".join(tag) for tag in product(*(v.split(".") for v in filename[-3:]))}
        require(set(wheel_metadata.get_all("Tag", [])) == filename_tags, "wheel_tag_metadata_mismatch")
        return {"name": canonical(message["Name"] or ""), "version": message["Version"],
                "metadata": message, "dist_info": metadata[0].split("/")[0],
                "sha256": digest(path), "path": path}


def target_wheel(filename):
    """Conservative precheck; uv and the native interpreter make the final ABI check."""
    match = re.fullmatch(r".+-([^-]+)-([^-]+)-([^-]+)\.whl", filename)
    if not match:
        return False
    python, abi, target = match.groups()
    if target == "any":
        return abi == "none" and any(t in {"py3", "py312"} for t in python.split("."))
    if not all(re.fullmatch(r"(?:manylinux(?:_\d+_\d+|2014)|linux)_aarch64", t)
               for t in target.split(".")):
        return False
    if python == "cp312" and abi in {"cp312", "abi3", "none"}:
        return True
    stable = re.fullmatch(r"cp(\d)(\d+)", python)
    return bool(stable and abi == "abi3" and (int(stable[1]), int(stable[2])) <= (3, 12))


def source_payload(root):
    expected = {}
    for base, prefix in ((root / "src/kmb", "kmb"), (root / "data", "kmb/data")):
        require(base.is_dir(), "source_payload_missing")
        for path in sorted(base.rglob("*")):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            require(not path.is_symlink(), "source_symlink_forbidden")
            if path.is_file():
                relative = path.relative_to(base).as_posix()
                require(not any(p.startswith(".") for p in Path(relative).parts),
                        "hidden_source_payload_forbidden")
                expected[f"{prefix}/{relative}"] = digest(path)
    return expected


def normalized_dependency(value):
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)(.*)", value.replace(" ", ""))
    require(match is not None, "invalid_project_dependency")
    return canonical(match[1]) + ",".join(sorted(match[2].split(",")))


def verify_project_wheel(info, root, project):
    require(info["name"] == PACKAGE and info["version"] == project["version"], "project_wheel_identity")
    require(info["metadata"]["Requires-Python"] == project["requires-python"], "project_python_metadata")
    require({normalized_dependency(d) for d in info["metadata"].get_all("Requires-Dist", [])} ==
            {normalized_dependency(d) for d in project["dependencies"]}, "project_dependency_metadata")
    expected = source_payload(root)
    with zipfile.ZipFile(info["path"]) as archive:
        actual = {p: hashlib.sha256(archive.read(p)).hexdigest() for p in archive.namelist()
                  if not p.endswith("/") and not p.startswith(info["dist_info"] + "/")}
        require(actual == expected, "project_wheel_source_mismatch")
        points = configparser.ConfigParser()
        points.read_string(archive.read(info["dist_info"] + "/entry_points.txt").decode())
        require(dict(points["console_scripts"]) == {"kmb": "kmb.cli:main"}, "project_entrypoint_mismatch")
        require(archive.read(info["dist_info"] + "/licenses/LICENSE") == (root / "LICENSE").read_bytes(),
                "project_license_mismatch")
    return expected


def inspect_inputs(args):
    root = args.source_root.resolve()
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    lock = tomllib.loads((root / "uv.lock").read_text())
    require(canonical(project["name"]) == PACKAGE, "unexpected_project")
    require(re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", project["version"]), "unsupported_release_version")
    closure = runtime_closure(lock)
    require(closure[PACKAGE]["version"] == project["version"], "project_lock_version_mismatch")
    require({canonical(re.match(r"[A-Za-z0-9_.-]+", d)[0]) for d in project["dependencies"]} ==
            {canonical(d["name"]) for d in closure[PACKAGE].get("dependencies", [])},
            "project_lock_direct_dependencies_mismatch")
    wheel = args.wheel.resolve()
    house = args.wheelhouse.resolve()
    require(house.is_dir() and wheel.parent == house, "project_wheel_must_be_in_wheelhouse")
    app = wheel_info(wheel)
    payload = verify_project_wheel(app, root, project)
    requirements = read_requirements(args.requirements, lock, closure, app["sha256"])
    chosen = {}
    for path in sorted(house.iterdir()):
        info = wheel_info(path)
        name = info["name"]
        require(name in requirements and info["version"] == requirements[name]["version"],
                "extra_or_wrong_version_wheel")
        require("sha256:" + info["sha256"] in requirements[name]["hashes"], "wheel_hash_mismatch")
        if name != PACKAGE:
            require(any(a["hash"] == "sha256:" + info["sha256"] and
                        Path(unquote(urlsplit(a["url"]).path)).name == path.name
                        for a in closure[name].get("wheels", [])), "wheel_lock_artifact_mismatch")
        require(target_wheel(path.name), "wheel_not_linux_arm64_python312")
        require(name not in chosen, "multiple_wheels_for_one_package")
        chosen[name] = info
    require(set(chosen) == set(closure), "incomplete_wheelhouse")
    require(chosen[PACKAGE]["path"] == wheel, "project_wheel_ambiguous")
    support = ["pyproject.toml", "uv.lock", "LICENSE", "scripts/run-benchmark.py"]
    inputs = {p: digest(root / p) for p in support}
    inputs["requirements"] = digest(args.requirements)
    return {"root": root, "version": project["version"], "wheels": chosen,
            "expected": {n: p["version"] for n, p in closure.items()}, "inputs": inputs,
            "source_payload_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}


def fresh_output(path, args):
    require(not path.exists() and not path.is_symlink(), "output_already_exists")
    result = path.resolve()
    for protected in (args.source_root / "src", args.source_root / "data", args.wheelhouse):
        require(not result.is_relative_to(protected.resolve()), "output_inside_protected_input")
    return result


def safe_env():
    # No caller credentials, loader overrides, Python imports or package-index configuration.
    env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR") if k in os.environ}
    env.update(LC_ALL="C", PYTHONDONTWRITEBYTECODE="1", UV_OFFLINE="1",
               UV_PYTHON_DOWNLOADS="never", PIP_CONFIG_FILE=os.devnull)
    return env


def command(args, cwd, records):
    completed = subprocess.run([str(a) for a in args], cwd=cwd, env=safe_env(),
                               text=True, capture_output=True, timeout=900, check=False)
    records.append({"program": Path(args[0]).name, "exit_code": completed.returncode})
    require(completed.returncode == 0, "subprocess_failed")
    return completed.stdout


def build_host(python, cwd, records):
    require(sys.platform == "linux" and platform.machine() == "aarch64", "linux_arm64_required")
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
                   if "=" in line)
    require(release.get("ID", "").strip('"') == "openkylin" and
            release.get("VERSION_ID", "").strip('"') == "3.0", "openkylin_3_required")
    require(str(python) == "/usr/bin/python3", "system_python_required")
    info = json.loads(command([python, "-I", "-B", "-c", ("import sys,json;print(json.dumps({"
                              "'version':list(sys.version_info[:3]),'prefix':sys.prefix,"
                              "'base_prefix':sys.base_prefix,'executable':sys.executable}))")], cwd, records))
    require(info["version"][:2] == [3, 12] and info["prefix"] == info["base_prefix"],
            "system_python312_required")
    resolved = python.resolve()
    require(any(resolved.is_relative_to(p) for p in (Path("/usr"), Path("/opt/system"))),
            "non_system_interpreter")
    programs = {name: shutil.which(name) for name in ("uv", "dpkg-deb", "dpkg-query", "ldd")}
    require(all(programs.values()), "build_tools_missing")
    uv_version = command([programs["uv"], "--version"], cwd, records).split()
    require(len(uv_version) >= 2 and uv_version[1] == UV_VERSION, "uv_version_mismatch")
    return {"os": "openkylin", "os_version": "3.0", "architecture": "arm64",
            "python": info, "python_resolved": str(resolved), "uv": UV_VERSION,
            "tools": programs}


INVENTORY = """import sys,json,importlib.metadata as m
print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix,
 'packages':[(d.metadata['Name'],d.version) for d in m.distributions()]}))"""


def verify_installed(venv, expected, wheels, output, records):
    inventory = json.loads(command([venv / "bin/python", "-I", "-B", "-c", INVENTORY], output, records))
    pairs = [(canonical(n), v) for n, v in inventory["packages"]]
    require(len(pairs) == len({n for n, _ in pairs}) and dict(pairs) == expected,
            "installed_closure_mismatch")
    require(inventory["prefix"] == str(venv), "relocated_prefix_mismatch")
    site = venv / "lib/python3.12/site-packages"
    metadata_count = 0
    for info in wheels.values():
        with zipfile.ZipFile(info["path"]) as archive:
            for name in archive.namelist():
                if name.startswith(info["dist_info"] + "/") and not name.endswith(("/", "/RECORD")):
                    require((site / name).is_file() and (site / name).read_bytes() == archive.read(name),
                            "installed_metadata_or_license_changed")
                    metadata_count += 1
    return {"count": len(pairs), "versions": dict(pairs), "metadata_files_verified": metadata_count}


def scan_payload(stage, forbidden_paths, secret_values):
    for path in stage.rglob("*"):
        relative = path.relative_to(stage)
        require(not any(p in {"reports", ".runtime", ".env"} or p.startswith(".env.")
                        or p.endswith(".env") for p in relative.parts),
                "forbidden_payload_name")
        require(not any("__editable__" in p or p.endswith(".egg-link") for p in relative.parts),
                "editable_payload")
        if path.is_symlink():
            target = path.readlink()
            resolved = path.resolve()
            system_python = (relative.is_relative_to(INSTALL / "bin") and
                             relative.name.startswith("python") and
                             any(resolved.is_relative_to(p) for p in
                                 (Path("/usr/bin"), Path("/opt/system"))))
            package_data = (relative == SUPPORT / "data" and
                            target == Path("/") / INSTALL / "lib/python3.12/site-packages/kmb/data")
            require(resolved.is_relative_to(stage.resolve()) or system_python or package_data,
                    "external_payload_symlink")
        elif path.is_file():
            content = path.read_bytes()
            require(not any(s.encode() in content for s in forbidden_paths), "build_path_in_payload")
            require(not any(s.encode() in content for s in secret_values), "credential_in_payload")
            if path.name == "direct_url.json":
                require(False, "direct_url_payload")


def elf_identity(path):
    with path.open("rb") as stream:
        header = stream.read(64)
    if not header.startswith(b"\x7fELF"):
        return None
    require(len(header) >= 52 and header[4] in {1, 2} and header[5] in {1, 2}
            and header[6] == 1 and (header[4] != 2 or len(header) == 64), "invalid_elf_header")
    byteorder = "little" if header[5] == 1 else "big"
    return {"elf_class": header[4], "elf_data": header[5],
            "elf_type": int.from_bytes(header[16:18], byteorder),
            "machine": int.from_bytes(header[18:20], byteorder)}


def dynamic_dependencies(stage, venv, host, output, records):
    libraries, scanned, foreign = {}, [], []
    for path in sorted(stage.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        identity = elf_identity(path)
        if identity is None:
            continue
        relative = path.relative_to(stage)
        if relative == DEBUGPY_FOREIGN_HELPER:
            require(identity == {"elf_class": 2, "elf_data": 1, "elf_type": 3, "machine": 62}
                    and digest(path) == DEBUGPY_FOREIGN_SHA256, "unrecognized_debugpy_foreign_helper")
            foreign.append({"path": str(relative), **identity, "sha256": digest(path),
                            "action": "preserved_without_ldd",
                            "reason": "Locked upstream x86_64 debugger attach helper; not executable or validated on ARM64."})
            continue
        require(identity["elf_class"] == 2 and identity["elf_data"] == 1
                and identity["machine"] == 183 and identity["elf_type"] in {2, 3},
                "unsupported_elf_architecture_or_type")
        before = len(records)
        try:
            result = command([host["tools"]["ldd"], str(path)], output, records)
        finally:
            if len(records) > before:
                records[-1]["payload_file"] = str(relative)
        require("not found" not in result, "missing_dynamic_library")
        scanned.append(str(relative))
        for name in re.findall(r"(?:=>\s*)?(/[^\s()]+)\s+\(", result):
            library = Path(name).resolve()
            if library.is_relative_to(venv):
                continue
            require(any(library.is_relative_to(p) for p in
                        (Path("/lib"), Path("/usr/lib"), Path("/opt/system"))), "non_system_library")
            if str(library) in libraries:
                continue
            owners = None
            for candidate in dict.fromkeys([str(library), name]):
                try:
                    owners = command([host["tools"]["dpkg-query"], "--search", candidate], output, records)
                    break
                except Rejected:
                    continue
            require(owners is not None, "dynamic_library_without_system_package")
            packages = {line.rsplit(": ", 1)[0] for line in owners.splitlines() if ": " in line}
            require(len(packages) == 1, "ambiguous_library_owner")
            package = packages.pop()
            require(re.fullmatch(r"[a-z0-9][a-z0-9+.-]*(?::arm64)?", package), "invalid_library_owner")
            version = command([host["tools"]["dpkg-query"], "-W", "-f=${Version}", package], output, records).strip()
            require(re.fullmatch(r"[A-Za-z0-9.+:~_-]+", version), "invalid_system_package_version")
            libraries[str(library)] = {"package": package, "version": version, "sha256": digest(library)}
    return {"elf_files": scanned, "foreign_elf_files": foreign, "system_libraries": libraries}


def write_file(path, content, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755 if executable else 0o644)


def normalize_modes(stage):
    """A restrictive builder umask must not make the installed CLI root-only."""
    stage.chmod(0o755)
    for path in stage.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)


def build(args, plan, output):
    records = []
    # Host rejection happens before creating any output or running install commands.
    host = build_host(args.python, plan["root"], records)
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    artifact = {"schema_version": 1, "status": "building", "linux_installation_verified": False,
                "model_calls": 0, "network_downloads_allowed": False, "host": host,
                "inputs": plan["inputs"], "source_payload_sha256": plan["source_payload_sha256"],
                "stages": [], "commands": records}
    try:
        inputs = output / "inputs"
        inputs.mkdir()
        wheels = {}
        for name, info in plan["wheels"].items():
            copied = inputs / info["path"].name
            shutil.copyfile(info["path"], copied)
            require(digest(copied) == info["sha256"], "input_changed_during_copy")
            wheels[name] = {**info, "path": copied}
        req = inputs / "requirements.txt"
        shutil.copyfile(args.requirements, req)
        require(digest(req) == plan["inputs"]["requirements"], "requirements_changed")
        artifact["wheels"] = [{"name": name, "version": info["version"],
                               "filename": info["path"].name, "sha256": info["sha256"]}
                              for name, info in sorted(wheels.items())]
        temporary = output / "venv-build"
        uv = host["tools"]["uv"]
        command([uv, "venv", "--relocatable", "--no-project", "--no-config", "--offline",
                 "--no-managed-python", "--no-python-downloads", "--python", args.python,
                 str(temporary)], output, records)
        command([uv, "pip", "install", "--python", str(temporary / "bin/python"),
                 "--offline", "--no-config", "--no-python-downloads", "--no-index",
                 "--find-links", str(inputs), "--no-deps", "--no-editable", "--require-hashes",
                 "--only-binary", ":all:", "--link-mode", "copy", "-r", str(req)], output, records)
        stage = output / "stage"
        venv = stage / INSTALL
        venv.parent.mkdir(parents=True)
        temporary.rename(venv)
        command([uv, "pip", "check", "--offline", "--no-config", "--python", str(venv / "bin/python")],
                output, records)
        artifact["installed"] = verify_installed(venv, plan["expected"], wheels, output, records)
        artifact["stages"].append("locked_wheels_installed_and_relocated")
        # Preserve the frozen fingerprint's existing lock lookup in installed layouts.
        packaged_lock = venv / "lib/python3.12/uv.lock"
        shutil.copyfile(plan["root"] / "uv.lock", packaged_lock)
        require(digest(packaged_lock) == plan["inputs"]["uv.lock"], "packaged_lock_changed")
        batch = plan["root"] / "scripts/run-benchmark.py"
        require(digest(batch) == plan["inputs"]["scripts/run-benchmark.py"], "batch_script_changed")
        write_file(stage / SUPPORT / "scripts/run-benchmark.py", batch.read_text())
        (stage / SUPPORT / "data").symlink_to(Path("/") / INSTALL / "lib/python3.12/site-packages/kmb/data")
        for name, tail in (("kmb", "-m kmb.cli"),
                           ("kmb-batch", "/usr/lib/openkylin-memory-bench/scripts/run-benchmark.py")):
            write_file(stage / "usr/bin" / name,
                       f'#!/bin/sh\nexec /{INSTALL}/bin/python -I {tail} "$@"\n', True)
        doc = stage / "usr/share/doc" / PACKAGE
        write_file(doc / "copyright", (plan["root"] / "LICENSE").read_text())
        write_file(doc / "offline-example.json", json.dumps({"schema_version": 1, "agent": "simulation",
                   "pilot": True, "variant": "A", "simulation_behavior": "expected"}, indent=2) + "\n")
        write_file(doc / "README", "Python dependencies are bundled; Docker, fixed agent images and a model endpoint are separate.\n"
                   "Installation does not run a benchmark. See build-manifest.json for the build scope.\n")
        artifact["dynamic"] = dynamic_dependencies(stage, venv, host, output, records)
        libraries = artifact["dynamic"]["system_libraries"].values()
        depends = {f"{item['package']} (>= {item['version']})" for item in libraries}
        depends.update({"python3 (>= 3.12)", "python3 (<< 3.13)"})
        # This recorded manifest deliberately contains hashes and identities, not source-machine paths.
        manifest = {"version": plan["version"], "revision": PACKAGE_REVISION,
                    "architecture": "arm64", "python_minor": "3.12",
                    "uv": UV_VERSION, "input_hashes": plan["inputs"], "wheels": artifact["wheels"],
                    "dependency_count": len(plan["expected"]), "linux_installation_verified": False,
                    "foreign_elf_files": artifact["dynamic"].get("foreign_elf_files", [])}
        write_file(doc / "build-manifest.json", json.dumps(manifest, indent=2) + "\n")
        write_file(stage / "DEBIAN/control", f"Package: {PACKAGE}\nVersion: {plan['version']}-{PACKAGE_REVISION}\n"
                   "Architecture: arm64\nMaintainer: KMB local build <noreply@example.invalid>\n"
                   "Section: science\nPriority: optional\n" + "Depends: " + ", ".join(sorted(depends)) + "\n"
                   "Description: Offline-packaged cross-session agent memory evaluation\n"
                   " Includes locked Python dependencies; real agents and model access are configured separately.\n")
        normalize_modes(stage)
        secret_values = tuple(v for k, v in os.environ.items() if len(v) >= 8 and
                              re.search(r"(?:API_KEY|TOKEN|PASSWORD|SECRET)$", k))
        scan_payload(stage, [str(output), str(plan["root"]), str(args.wheelhouse.resolve())], secret_values)
        # Verify source metadata remained stable; do not silently package a mixed snapshot.
        require(all(digest(plan["root"] / name) == value for name, value in plan["inputs"].items()
                    if name != "requirements"), "source_changed_during_build")
        require(hashlib.sha256(json.dumps(source_payload(plan["root"]), sort_keys=True).encode()).hexdigest()
                == plan["source_payload_sha256"], "payload_source_changed_during_build")
        artifact["stages"].append("metadata_licenses_payload_and_dynamic_dependencies_checked")
        deb = output / f"{PACKAGE}_{plan['version']}-{PACKAGE_REVISION}_arm64.deb"
        command([host["tools"]["dpkg-deb"], "--build", "--root-owner-group", str(stage), str(deb)], output, records)
        require(deb.is_file() and deb.stat().st_size > 0, "deb_artifact_missing")
        artifact.update(status="built_not_installed", deb={"filename": deb.name, "sha256": digest(deb),
                                                           "size": deb.stat().st_size})
        artifact["stages"].append("deb_created_not_installed")
        return artifact
    except (Exception, KeyboardInterrupt) as exc:
        artifact.update(status="failed", error_code=str(exc) if isinstance(exc, Rejected) else type(exc).__name__)
        raise
    finally:
        write_file(output / "build-result.json", json.dumps(artifact, indent=2) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=PROJECT)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True,
                        help="Complete hashed non-editable runtime requirements, including the project wheel")
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory; inspect never creates it")
    parser.add_argument("--python", type=Path, default=Path("/usr/bin/python3"))
    parser.add_argument("--build", action="store_true", help="Explicitly build; never installs the resulting .deb")
    parser.add_argument("--dry-run", action="store_true", help="Alias for the default read-only inspection")
    args = parser.parse_args(argv)
    try:
        require(not (args.build and args.dry_run), "build_and_dry_run_conflict")
        output = fresh_output(args.output, args)
        plan = inspect_inputs(args)
        if args.build:
            result = build(args, plan, output)
        else:
            result = {"status": "inputs_inspected", "build_requested": False, "output_created": False,
                      "target": "openKylin 3.0 / arm64 / system Python 3.12", "uv_required": UV_VERSION,
                      "dependency_count": len(plan["expected"]), "inputs": plan["inputs"],
                      "source_payload_sha256": plan["source_payload_sha256"],
                      "native_abi_and_linux_installation_verified": False}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "rejected", "error_code": str(exc) if isinstance(exc, Rejected)
                          else type(exc).__name__, "linux_installation_verified": False}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted", "linux_installation_verified": False}), file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
