#!/usr/bin/env python3
"""Initialize only the dedicated blank 12 GiB disk of this openKylin Live lab.

Run as root inside the project's isolated guest. Existing unknown filesystems,
users, mounts and swap files are refused; no host disk is a valid target.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import stat
import subprocess
import uuid
from pathlib import Path

DEVICE = "/dev/vda"
SIZE = 12 * 1024**3
ROOT = Path("/mnt/kmb")
UID = 10001


def run(*args: str, required: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)
    if required and result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip()}")
    return result


def preserve_initialization(root: Path, record: dict) -> None:
    """Keep the first measurement immutable; append a separate reuse event."""
    original = root / "data-initialization.json"
    if original.is_symlink():
        raise RuntimeError("initialization record is a symlink")
    if original.exists():
        previous = json.loads(original.read_text())
        if not isinstance(previous, dict):
            raise RuntimeError("invalid initialization record")
        for key in ("device", "bytes", "label", "filesystem_uuid", "uid", "gid"):
            if previous.get(key) != record[key]:
                raise RuntimeError("initialization record identifies a different data disk")
    else:
        with original.open("x") as stream:
            stream.write(json.dumps(record, indent=2) + "\n")
    history = root / ".data-events"
    if history.is_symlink() or (history.exists() and not history.is_dir()):
        raise RuntimeError("invalid data event directory")
    history.mkdir(mode=0o700, exist_ok=True)
    name = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S")
    with (history / f"{name}-{uuid.uuid4().hex}.json").open("x") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")


def validate_existing(root: Path, record: dict) -> None:
    """An initialized disk can only be checked here; restore owns all later writes."""
    if root.stat().st_uid != 0 or stat.S_IMODE(root.stat().st_mode) != 0o755:
        raise RuntimeError("existing data root ownership/mode differs")
    for name in ("home", "project", "runtime", "reports", "cache", "transfer", "bin"):
        path = root / name
        if (path.is_symlink() or not path.is_dir() or path.stat().st_uid != UID
                or path.stat().st_gid != UID or stat.S_IMODE(path.stat().st_mode) != 0o700):
            raise RuntimeError("existing data ownership/mode differs; no repair performed")
    original = root / "data-initialization.json"
    if (original.is_symlink() or not original.is_file() or original.stat().st_uid != 0
            or stat.S_IMODE(original.stat().st_mode) & 0o022 or original.stat().st_size > 65536):
        raise RuntimeError("original initialization record is unavailable")
    previous = json.loads(original.read_text())
    if not isinstance(previous, dict) or any(previous.get(key) != record[key]
           for key in ("device", "bytes", "label", "filesystem_uuid", "uid", "gid")):
        raise RuntimeError("initialization record identifies a different data disk")


def main(*, initialize_blank: bool = False, expected_uuid: str | None = None) -> None:
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
                   if "=" in line)
    if (os.geteuid() != 0 or release.get("ID", "").strip('"') != "openkylin"
            or "boot=casper" not in Path("/proc/cmdline").read_text()):
        raise RuntimeError("requires root in the dedicated openKylin Live guest")
    node, = json.loads(run("lsblk", "-bJ", "-o", "NAME,TYPE,SIZE,RO,FSTYPE,MOUNTPOINTS",
                          DEVICE).stdout)["blockdevices"]
    if (node["name"] != "vda" or node["type"] != "disk" or node["size"] != SIZE
            or node["ro"] or node.get("children")):
        raise RuntimeError("dedicated blank-disk identity check failed")
    for database in ("passwd", "group"):
        for key in ("kmb", str(UID)):
            found = run("getent", database, key, required=False)
            if found.returncode not in (0, 2):
                raise RuntimeError("cannot inspect user/group identity")
            if found.returncode == 0:
                fields = found.stdout.strip().split(":")
                if fields[0] != "kmb" or int(fields[2]) != UID:
                    raise RuntimeError("dedicated user/group identity is already occupied")
                if database == "passwd" and (int(fields[3]) != UID or fields[5] != str(ROOT / "home")):
                    raise RuntimeError("existing runner account differs")
    if ROOT.is_symlink():
        raise RuntimeError("mount path is a symlink")
    canonical_root = str(ROOT.resolve())
    mounts = [p for p in node.get("mountpoints", []) if p]
    if mounts and mounts != [canonical_root]:
        raise RuntimeError("dedicated disk is mounted elsewhere")
    if ROOT.exists() and any(ROOT.iterdir()) and canonical_root not in mounts:
        raise RuntimeError("refusing to hide existing mount-directory contents")
    initialized = False
    if node["fstype"] is None:
        if not initialize_blank or expected_uuid is not None:
            raise RuntimeError("blank initialization requires explicit --initialize-blank")
        if mounts or run("wipefs", "-n", DEVICE).stdout.strip():
            raise RuntimeError("disk is not blank")
        run("mkfs.ext4", "-m", "1", "-L", "KMB_DATA", DEVICE)
        initialized = True
    elif node["fstype"] != "ext4" or run("blkid", "-s", "LABEL", "-o", "value", DEVICE).stdout.strip() != "KMB_DATA":
        raise RuntimeError("refusing an unknown existing filesystem")
    elif (initialize_blank or not expected_uuid or
          run("blkid", "-s", "UUID", "-o", "value", DEVICE).stdout.strip() != expected_uuid):
        raise RuntimeError("existing data disk requires its explicit --expected-uuid")
    if not initialized:
        record = {"device": DEVICE, "bytes": SIZE, "label": "KMB_DATA", "mount": str(ROOT),
                  "initialized_now": False, "runner": "kmb", "uid": UID, "gid": UID,
                  "filesystem_uuid": expected_uuid, "read_only": True, "mounted": bool(mounts)}
        if mounts:
            mounted = json.loads(run("findmnt", "-J", "-R", "--target", str(ROOT),
                                     "-o", "TARGET,SOURCE").stdout)["filesystems"]
            if (len(mounted) != 1 or mounted[0].get("target") != canonical_root
                    or mounted[0].get("source") != DEVICE or mounted[0].get("children")):
                raise RuntimeError("unexpected data mount")
            validate_existing(ROOT, record)
        else:
            record["next_step"] = "use the explicit UUID-bound restore mount stage"
        print(json.dumps(record))
        return
    ROOT.mkdir(exist_ok=True)
    if not mounts:
        run("mount", "-t", "ext4", DEVICE, str(ROOT))
    if run("findmnt", "-n", "-o", "SOURCE", "--target", str(ROOT)).stdout.strip() != DEVICE:
        raise RuntimeError("mounted device differs")
    mounted = json.loads(run("findmnt", "-J", "-R", "--target", str(ROOT),
                             "-o", "TARGET").stdout)["filesystems"]
    if len(mounted) != 1 or mounted[0].get("target") != canonical_root or mounted[0].get("children"):
        raise RuntimeError("refusing nested mounts in the data directory")
    for name in ("home", "project", "runtime", "reports", "cache", "transfer", "bin"):
        path = ROOT / name
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise RuntimeError("refusing a non-directory in the dedicated data directory")
    os.chown(ROOT, 0, 0)
    ROOT.chmod(0o755)
    if run("getent", "group", "kmb", required=False).returncode:
        run("groupadd", "--gid", str(UID), "kmb")
    if run("getent", "passwd", "kmb", required=False).returncode:
        run("useradd", "--uid", str(UID), "--gid", str(UID), "--create-home",
            "--home-dir", str(ROOT / "home"), "--shell", "/bin/bash", "kmb")
    for name in ("home", "project", "runtime", "reports", "cache", "transfer", "bin"):
        path = ROOT / name
        if path.is_symlink():
            raise RuntimeError("refusing a symlink in the dedicated data directory")
        path.mkdir(exist_ok=True)
        os.chown(path, UID, UID)
        path.chmod(0o700)
    record = {"device": DEVICE, "bytes": SIZE, "label": "KMB_DATA", "mount": str(ROOT),
              "initialized_now": initialized, "runner": "kmb", "uid": UID, "gid": UID,
              "filesystem_uuid": run("blkid", "-s", "UUID", "-o", "value", DEVICE).stdout.strip()}
    preserve_initialization(ROOT, record)
    print(json.dumps(record))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize-blank", action="store_true")
    parser.add_argument("--expected-uuid")
    args = parser.parse_args()
    main(initialize_blank=args.initialize_blank, expected_uuid=args.expected_uuid)
