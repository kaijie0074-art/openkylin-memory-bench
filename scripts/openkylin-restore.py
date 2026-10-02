#!/usr/bin/env python3
"""Inspect or explicitly restore the dedicated openKylin Live lab, never format it.

Each mutating stage needs --execute and the previously recorded filesystem UUID.
No model configuration is loaded. Existing projects, reports and image layers are
preserved. SSH bootstrap and initial blank-disk creation are separate operations.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

DEVICE = "/dev/vda"
ROOT = "/mnt/kmb"
SIZE = 12 * 1024**3
UID = 10001
PACKAGES = {"docker.io": "24.0.7-ok3", "containerd": "1.5.9-ok6", "runc": "1.1.0-ok1"}
CONFIGS = {
    "/etc/docker/daemon.json": '{"data-root":"/mnt/kmb/docker"}\n',
    "/etc/containerd/config.toml":
        'version = 2\nroot = "/mnt/kmb/containerd"\nstate = "/run/containerd"\n',
    "/etc/systemd/system/docker.service.d/kmb-data.conf":
        '[Service]\nExecStart=\nExecStart=/opt/system/bin/dockerd -H fd:// '
        '--containerd=/run/containerd/containerd.sock --config-file=/etc/docker/daemon.json\n',
}
CLEAN_ENV = {"PATH": "/opt/system/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C",
             "HOME": "/root", "DEBIAN_FRONTEND": "noninteractive"}
STAGES = ("inspect", "mount", "accounts", "packages", "docker", "swap", "memory",
          "project", "dependencies", "validate")


class System:
    """The injected root/runner are for local tests; CLI always uses the real guest."""
    def __init__(self, root: Path = Path("/"), runner=subprocess.run, trusted_uid: int = 0):
        self.root, self.runner, self.trusted_uid = root, runner, trusted_uid

    def path(self, value: str) -> Path:
        return self.root / value.lstrip("/")

    def run(self, *args: str, required: bool = True, timeout: int = 120):
        result = self.runner(list(args), capture_output=True, text=True, timeout=timeout,
                             check=False, env=CLEAN_ENV.copy())
        if required and result.returncode:
            # Do not echo arbitrary command output or environment into the audit log.
            raise RuntimeError(f"{Path(args[0]).name} failed (exit {result.returncode})")
        return result

    def safe(self, value: str) -> Path:
        path = self.path(value)
        # /mnt itself is an openKylin system alias; managed descendants cannot be aliases.
        start = ROOT if value.startswith(ROOT + "/") or value == ROOT else "/etc"
        if not value.startswith(start):
            raise RuntimeError("path is outside managed configuration/data roots")
        cursor = self.path(start)
        for piece in ("", *PurePosixPath(value).relative_to(start).parts):
            if piece:
                cursor = cursor / piece
            if cursor.is_symlink():
                raise RuntimeError("refusing a symlink in a managed path")
        return path


def account_check(system: System) -> dict:
    exists = {}
    for database in ("passwd", "group"):
        exists[database] = False
        for key in ("kmb", str(UID)):
            result = system.run("getent", database, key, required=False)
            if result.returncode == 2:
                continue
            if result.returncode != 0:
                raise RuntimeError("cannot inspect runner identity")
            fields = result.stdout.strip().split(":")
            if len(fields) < 3 or fields[0] != "kmb" or fields[2] != str(UID):
                raise RuntimeError("runner name/UID/GID is occupied by another identity")
            if database == "passwd" and (len(fields) < 7 or fields[3] != str(UID)
                    or fields[5] != ROOT + "/home" or fields[6] != "/bin/bash"):
                raise RuntimeError("existing runner account differs")
            exists[database] = True
    return exists


def config_matches(path: Path, wanted: str) -> bool:
    if path.name == "daemon.json":
        try:
            return json.loads(path.read_text()) == json.loads(wanted)
        except (ValueError, OSError):
            return False
    return path.read_text() == wanted


def check_configs(system: System) -> list[str]:
    missing = []
    for name, wanted in CONFIGS.items():
        path = system.safe(name)
        if not path.exists():
            missing.append(name)
        elif (not path.is_file() or path.stat().st_uid != system.trusted_uid
              or stat.S_IMODE(path.stat().st_mode) & 0o022
              or not config_matches(path, wanted)):
            raise RuntimeError("an existing Docker configuration is not owned/matching")
    return missing


def inspect(system: System, expected_uuid: str) -> dict:
    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", expected_uuid):
        raise RuntimeError("provide the recorded full filesystem UUID")
    release = dict(line.split("=", 1) for line in system.path("/etc/os-release").read_text().splitlines()
                   if "=" in line)
    if (release.get("ID", "").strip('"') != "openkylin"
            or release.get("VERSION_ID", "").strip('"') != "3.0"
            or "boot=casper" not in system.path("/proc/cmdline").read_text()
            or system.run("uname", "-m").stdout.strip() != "aarch64"):
        raise RuntimeError("requires the openKylin 3.0 ARM64 Live guest")
    nodes = json.loads(system.run("lsblk", "-bJ", "-o",
        "NAME,TYPE,SIZE,RO,FSTYPE,MOUNTPOINTS", DEVICE).stdout)["blockdevices"]
    if len(nodes) != 1:
        raise RuntimeError("ambiguous data disk")
    node = nodes[0]
    if (node.get("name") != "vda" or node.get("type") != "disk" or node.get("size") != SIZE
            or node.get("ro") or node.get("children") or node.get("fstype") != "ext4"
            or system.run("blkid", "-s", "LABEL", "-o", "value", DEVICE).stdout.strip() != "KMB_DATA"
            or system.run("blkid", "-s", "UUID", "-o", "value", DEVICE).stdout.strip() != expected_uuid):
        raise RuntimeError("data disk identity mismatch; restore never formats disks")
    root = system.safe(ROOT)
    canonical = str(root.resolve())
    mounts = [m for m in node.get("mountpoints", []) if m]
    if mounts and mounts != [canonical]:
        raise RuntimeError("data disk is mounted elsewhere")
    target_mount = system.run("findmnt", "-n", "-o", "SOURCE", "--mountpoint", ROOT,
                              required=False)
    if target_mount.returncode not in (0, 1) or (target_mount.returncode == 0
                                               and target_mount.stdout.strip() != DEVICE):
        raise RuntimeError("mountpoint is occupied or cannot be inspected")
    if bool(mounts) != (target_mount.returncode == 0):
        raise RuntimeError("mount observations disagree")
    if not mounts and root.exists() and any(root.iterdir()):
        raise RuntimeError("refusing to hide existing mountpoint contents")
    docker_overlay_mounts = 0
    if mounts:
        tree = json.loads(system.run("findmnt", "-J", "-R", "--target", ROOT,
                                     "-o", "TARGET,SOURCE,FSTYPE").stdout)["filesystems"]
        if (len(tree) != 1 or tree[0].get("target") != canonical
                or tree[0].get("source") != DEVICE or tree[0].get("fstype") != "ext4"):
            raise RuntimeError("unexpected/nested data mounts")
        # A running Docker container normally adds an overlay mount under its
        # data-root. Report that state without misidentifying the dedicated disk;
        # even a leftover mount must block all restore mutations independently of
        # the later Docker/ps snapshots (which can race with container teardown).
        overlay_target = re.compile(re.escape(canonical) + r"/docker/overlay2/[0-9a-f]{64}/merged")
        for child in tree[0].get("children", []):
            if (child.get("source") != "overlay" or child.get("fstype") != "overlay"
                    or not overlay_target.fullmatch(child.get("target", ""))
                    or child.get("children")):
                raise RuntimeError("unexpected/nested data mounts")
            docker_overlay_mounts += 1
    accounts = account_check(system)
    missing = check_configs(system)
    packages = {}
    for name, wanted in PACKAGES.items():
        result = system.run("dpkg-query", "-W", "-f=${Status}\t${Version}", name, required=False)
        if result.returncode == 1:
            packages[name] = None
        elif result.returncode == 0:
            fields = result.stdout.strip().split("\t")
            if len(fields) != 2 or fields[0] != "install ok installed" or fields[1] != wanted:
                raise RuntimeError("installed package version differs; no automatic upgrade/downgrade")
            packages[name] = fields[1]
        else:
            raise RuntimeError("cannot inspect package versions")
    active = system.run("systemctl", "is-active", "--quiet", "docker", required=False)
    if active.returncode not in (0, 3, 4):
        raise RuntimeError("cannot inspect Docker service")
    docker_active = active.returncode == 0
    running = False
    if docker_active:
        running = bool(system.run("/opt/system/bin/docker", "-H", "unix:///var/run/docker.sock",
                                  "ps", "--quiet").stdout.strip())
    processes = system.run("ps", "-eo", "pid=,args=").stdout.splitlines()
    busy = any(re.search(r"(?:^|[ /])(?:kmb(?:\.cli)?|kmb/cli\.py|run-benchmark\.py|"
                         r"agent-worker\.py|probe_subject_isolation\.py|calibrate-scorers\.py)"
                         r"(?:\s|$)", line)
               for line in processes)
    return {"os": "openKylin 3.0 Live", "arch": "aarch64", "uuid": expected_uuid,
            "mounted": bool(mounts), "accounts": accounts, "missing_configs": missing,
            "packages": packages, "docker_active": docker_active,
            "running_containers": running, "runner_process_present": busy,
            "docker_overlay_mounts": docker_overlay_mounts,
            "restore_blocked": bool(running or busy or docker_overlay_mounts),
            "read_only": True, "model_calls": 0}


def check_idle(state: dict) -> None:
    if (state["running_containers"] or state["runner_process_present"]
            or state["docker_overlay_mounts"]):
        raise RuntimeError("active containers/runner or Docker overlay mounts detected; "
                           "no restore mutation allowed")


def managed_configs(system: System) -> None:
    missing = check_configs(system)  # Check all conflicts before creating any file.
    for name in missing:
        path = system.safe(name)
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        with path.open("x") as stream:
            stream.write(CONFIGS[name])
        path.chmod(0o644)


def audit_dir(system: System) -> Path:
    path = system.safe(ROOT + "/.restore-events")
    if path.exists() and (not path.is_dir() or path.stat().st_uid != system.trusted_uid
                         or stat.S_IMODE(path.stat().st_mode) != 0o700):
        raise RuntimeError("invalid restore audit directory")
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def verify_bundle(path: Path, expected: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RuntimeError("a lowercase SHA256 is required for the archive")
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("archive must be an ordinary file")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise RuntimeError("archive SHA256 mismatch")


def extract_new_project(archive: Path, destination: Path, expected: str) -> None:
    """Validate every member before writing; never extract over an existing tree."""
    verify_bundle(archive, expected)
    if destination.is_symlink() or (destination.exists() and
                                    (not destination.is_dir() or any(destination.iterdir()))):
        raise RuntimeError("project destination is not empty; preserve the existing project")
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        names = set()
        total = 0
        if not members or len(members) > 10000:
            raise RuntimeError("invalid archive member count")
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or not path.parts
                    or str(path) in names or "\\" in member.name
                    or not (member.isfile() or member.isdir())
                    or any(part in {".env", ".env.local", ".git", ".runtime", "runs", "reports"}
                           for part in path.parts)):
                raise RuntimeError("unsafe or private archive member")
            names.add(str(path))
            total += member.size
            if total > 64 * 1024**2:
                raise RuntimeError("project archive exceeds expanded size limit")
        if not {"pyproject.toml", "uv.lock"} <= names:
            raise RuntimeError("project archive lacks its dependency lock")
        staging = Path(tempfile.mkdtemp(prefix=".project-stage-", dir=destination.parent))
        try:
            for member in members:
                target = staging / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.extractfile(member) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(0o700 if member.mode & 0o111 else 0o600)
            # Existing empty directory only: no files or historical results can be replaced.
            os.replace(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)  # Only the temporary directory made by this invocation.


def install_uv(archive: Path, destination: Path, expected: str) -> None:
    verify_bundle(archive, expected)
    with tarfile.open(archive, "r:gz") as bundle:
        matches = [m for m in bundle.getmembers()
                   if m.name == "uv-aarch64-unknown-linux-gnu/uv"]
        if len(matches) != 1 or not matches[0].isfile() or not 0 < matches[0].size < 128 * 1024**2:
            raise RuntimeError("uv archive has no unique ordinary ARM64 uv executable")
        with bundle.extractfile(matches[0]) as stream:
            wanted = stream.read()
    if destination.is_symlink():
        raise RuntimeError("uv binary is a symlink")
    if destination.exists():
        if not destination.is_file() or destination.read_bytes() != wanted:
            raise RuntimeError("existing uv differs; no automatic replacement")
        return
    with destination.open("xb") as stream:
        stream.write(wanted)
    destination.chmod(0o700)


def package_proxy(value: str | None) -> list[str]:
    if value is None:
        return []
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise RuntimeError("invalid loopback package proxy") from None
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or port is None or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment):
        raise RuntimeError("package proxy must be a credential-free loopback HTTP endpoint")
    return ["HTTP_PROXY=" + value, "HTTPS_PROXY=" + value]


def validate_package_preview(output: str) -> None:
    # New installs end in "[arm64]" too; only an old version before '(' is an upgrade.
    if any(line.startswith("Remv ") or re.match(r"^Inst \S+ \[", line)
           for line in output.splitlines()):
        raise RuntimeError("package preview contains removals/upgrades; preserve the guest")


def docker_root_matches(system: System, reported: object) -> bool:
    if not isinstance(reported, str) or ".." in PurePosixPath(reported).parts:
        return False
    expected = system.safe(ROOT + "/docker").resolve()
    canonical = "/" + str(expected.relative_to(system.root.resolve()))
    # Only the configured path and its already verified openKylin /mnt alias are accepted.
    return (reported in {ROOT + "/docker", canonical}
            and system.path(reported).resolve() == expected)


def restore(system: System, stage: str, expected_uuid: str, *, execute: bool = False,
            project_archive: Path | None = None, project_sha256: str | None = None,
            uv_archive: Path | None = None, uv_sha256: str | None = None,
            allow_downloads: bool = False, proxy: str | None = None) -> dict:
    state = inspect(system, expected_uuid)
    if not execute or stage == "inspect":
        return {**state, "requested_stage": stage, "execute": False}
    if os.geteuid() != system.trusted_uid:
        raise RuntimeError("explicit mutation requires guest root")
    check_idle(state)
    if stage != "mount" and not state["mounted"]:
        raise RuntimeError("run the explicit mount stage first")
    if stage == "mount":
        if not state["mounted"]:
            system.safe(ROOT).mkdir(mode=0o755, exist_ok=True)
            system.run("mount", "-t", "ext4", DEVICE, ROOT)
            if not inspect(system, expected_uuid)["mounted"]:
                raise RuntimeError("mounted disk could not be verified")
    elif stage == "accounts":
        for name in ("home", "project", "runtime", "reports", "cache", "transfer", "bin"):
            path = system.safe(ROOT + "/" + name)
            if (not path.is_dir() or path.stat().st_uid != UID
                    or stat.S_IMODE(path.stat().st_mode) != 0o700):
                raise RuntimeError("existing data ownership/mode differs; not rewriting it")
        if not state["accounts"]["group"]:
            system.run("groupadd", "--gid", str(UID), "kmb")
        if not state["accounts"]["passwd"]:
            system.run("useradd", "--uid", str(UID), "--gid", str(UID), "--no-create-home",
                       "--home-dir", ROOT + "/home", "--shell", "/bin/bash", "kmb")
    elif stage == "packages":
        if state["docker_active"] and (state["missing_configs"] or
                                       any(v is None for v in state["packages"].values())):
            raise RuntimeError("active Docker cannot be reconfigured or reinstalled")
        if any(v is None for v in state["packages"].values()):
            policy = system.path("/usr/sbin/policy-rc.d")
            if policy.exists() or policy.is_symlink():
                raise RuntimeError("preserve existing package service policy")
            directory = Path(tempfile.mkdtemp(prefix="apt-", dir=audit_dir(system)))
            (directory / "official.list").write_text(
                "deb https://archive.openkylin.top/openkylin huanghe main cross pty\n")
            (directory / "lists/partial").mkdir(parents=True)
            archives = system.safe(ROOT + "/apt-archives")
            archives.mkdir(mode=0o755, exist_ok=True)
            system.safe(ROOT + "/apt-archives/partial").mkdir(exist_ok=True)
            apt = ["apt-get", "-o", "Dir::Etc::sourcelist=" + str(directory / "official.list"),
                   "-o", "Dir::Etc::sourceparts=-", "-o", "Dir::State::lists=" + str(directory / "lists"),
                   "-o", "Dir::Cache::archives=" + str(archives), "-o", "Acquire::Retries=2",
                   "-o", "Acquire::https::Timeout=30", "-o", "Acquire::Languages=none"]
            packages = [f"{name}={version}" for name, version in PACKAGES.items()]
            system.run(*apt, "update", timeout=300)
            preview = system.run(*apt, "--simulate", "--no-install-recommends", "install", *packages)
            validate_package_preview(preview.stdout)
            managed_configs(system)
            for name in ("docker", "containerd"):
                path = system.safe(ROOT + "/" + name)
                if path.exists() and (not path.is_dir() or
                                      path.stat().st_uid != system.trusted_uid):
                    raise RuntimeError("untrusted existing container storage")
                path.mkdir(mode=0o700, exist_ok=True)
            with policy.open("x") as stream:
                stream.write("#!/bin/sh\nexit 101\n")
            policy.chmod(0o755)
            policy_identity = policy.stat().st_ino
            try:
                system.run(*apt, "-y", "--no-remove", "--no-install-recommends",
                           "install", *packages, timeout=600)
            finally:
                if policy.is_symlink() or policy.stat().st_ino != policy_identity:
                    raise RuntimeError("package service policy changed; preserving it")
                policy.unlink()
    elif stage == "docker":
        if not all(state["accounts"].values()) or any(v is None for v in state["packages"].values()):
            raise RuntimeError("restore the runner account and fixed packages first")
        if state["docker_active"] and state["missing_configs"]:
            raise RuntimeError("active Docker configuration differs; never stopping it automatically")
        for name in ("docker", "containerd"):
            path = system.safe(ROOT + "/" + name)
            if not path.is_dir() or path.stat().st_uid != system.trusted_uid:
                raise RuntimeError("existing container storage directory is not trusted")
        managed_configs(system)
        if "docker" not in system.run("id", "-nG", "kmb").stdout.split():
            system.run("usermod", "-aG", "docker", "kmb")
        if not state["docker_active"]:
            system.run("systemctl", "daemon-reload")
            system.run("systemctl", "start", "containerd", "docker")
        info = json.loads(system.run("/opt/system/bin/docker", "-H", "unix:///var/run/docker.sock",
                                     "info", "--format", "{{json .}}").stdout)
        if not docker_root_matches(system, info.get("DockerRootDir")):
            raise RuntimeError("actual Docker data root differs")
    elif stage == "swap":
        swap = system.safe(ROOT + "/swapfile")
        if (not swap.is_file() or swap.stat().st_size != 2 * 1024**3
                or swap.stat().st_uid != system.trusted_uid or swap.stat().st_nlink != 1
                or stat.S_IMODE(swap.stat().st_mode) != 0o600
                or system.run("blkid", "-p", "-s", "TYPE", "-o", "value", str(swap)).stdout.strip() != "swap"):
            raise RuntimeError("expected existing 2 GiB swapfile not found; never reinitializing it")
        active = system.run("swapon", "--show", "--noheadings", "--raw", "--output", "NAME").stdout.splitlines()
        if not any(Path(name).resolve() == swap.resolve() for name in active):
            system.run("swapon", str(swap))
    elif stage == "memory":
        # Explicit stage only: this is the optional Live inference service, not Docker.
        unit = system.run("systemctl", "show", "-p", "LoadState", "--value",
                          "kytensor.service").stdout.strip()
        if unit not in {"loaded", "masked"}:
            raise RuntimeError("expected Live kytensor service not found")
        system.run("systemctl", "stop", "kytensor.service")
        system.run("systemctl", "mask", "--runtime", "kytensor.service")
    elif stage == "project":
        if project_archive is None or project_sha256 is None:
            raise RuntimeError("fresh project install needs --project-archive and --project-sha256")
        if not all(state["accounts"].values()):
            raise RuntimeError("restore runner identity first")
        destination = system.safe(ROOT + "/project")
        extract_new_project(project_archive, destination, project_sha256)
        for path in (destination, *destination.rglob("*")):
            os.chown(path, UID, UID)
    elif stage == "dependencies":
        if not all(state["accounts"].values()) or uv_archive is None or uv_sha256 is None:
            raise RuntimeError("dependency restore needs runner identity and a SHA256-pinned uv archive")
        proxy_env = package_proxy(proxy)
        if proxy_env and not allow_downloads:
            raise RuntimeError("network proxy requires explicit --allow-downloads")
        for name in ("pyproject.toml", "uv.lock"):
            if not system.safe(ROOT + "/project/" + name).is_file():
                raise RuntimeError("project dependency manifest is missing")
        binary = system.safe(ROOT + "/bin/uv")
        if not binary.parent.is_dir():
            raise RuntimeError("restore the dedicated data directories first")
        install_uv(uv_archive, binary, uv_sha256)
        os.chown(binary, UID, UID)
        runner = ["runuser", "-u", "kmb", "--", "env", "-i", "HOME=" + ROOT + "/home",
                  "PATH=/mnt/kmb/bin:/opt/system/bin:/usr/bin:/bin",
                  "UV_CACHE_DIR=/mnt/kmb/cache/uv", "UV_PYTHON_DOWNLOADS=never", *proxy_env]
        version = system.run(*runner, str(binary), "--version").stdout.strip()
        if not re.match(r"^uv 0\.11\.15(?: |$)", version):
            raise RuntimeError("uv version differs from the pinned 0.11.15 release")
        command = [*runner, str(binary), "sync", "--project", ROOT + "/project", "--frozen",
                   "--no-dev", "--python", "/usr/bin/python3"]
        if not allow_downloads:
            command.append("--offline")
        system.run(*command, timeout=600)
    elif stage == "validate":
        if not all(state["accounts"].values()):
            raise RuntimeError("runner account is unavailable")
        system.run("runuser", "-u", "kmb", "--", "env", "-i", "HOME=" + ROOT + "/home",
                   "PATH=/mnt/kmb/bin:/opt/system/bin:/usr/bin:/bin",
                   ROOT + "/project/.venv/bin/kmb", "dataset", "validate")
    record = {"stage": stage, "uuid": expected_uuid, "model_calls": 0,
              "completed_at": datetime.datetime.now(datetime.UTC).isoformat(),
              "note": "stage completion only; no benchmark or cold-restart success asserted"}
    if inspect(system, expected_uuid)["mounted"]:
        with (audit_dir(system) / (uuid.uuid4().hex + ".json")).open("x") as stream:
            stream.write(json.dumps(record, indent=2) + "\n")
    return record


@contextlib.contextmanager
def restore_lock():
    path = Path("/run/lock/kmb-openkylin-restore.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise RuntimeError("restore lock is not a private root-owned regular file")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another restore process is active") from None
        yield


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES, nargs="?", default="inspect")
    parser.add_argument("--expected-uuid", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--project-archive", type=Path)
    parser.add_argument("--project-sha256")
    parser.add_argument("--uv-archive", type=Path)
    parser.add_argument("--uv-sha256")
    parser.add_argument("--allow-downloads", action="store_true")
    parser.add_argument("--package-proxy")
    args = parser.parse_args()
    system = System()
    if args.execute and args.stage != "inspect":
        # Reject the host/wrong guest before even creating the cooperative restore lock.
        inspect(system, args.expected_uuid)
        if os.geteuid() != 0:
            raise RuntimeError("explicit mutation requires guest root")
    lock = restore_lock() if args.execute and args.stage != "inspect" else contextlib.nullcontext()
    with lock:
        print(json.dumps(restore(system, args.stage, args.expected_uuid, execute=args.execute,
                                project_archive=args.project_archive,
                                project_sha256=args.project_sha256,
                                uv_archive=args.uv_archive, uv_sha256=args.uv_sha256,
                                allow_downloads=args.allow_downloads,
                                proxy=args.package_proxy), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(str(exc)) from None
