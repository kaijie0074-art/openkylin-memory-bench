#!/usr/bin/env python3
"""Prepare one isolated, native ARM64 openKylin desktop VM with installed Lima.

No downloads, package installations, host directory shares, profile imports, or
destructive VM commands. ISO installation remains an observable graphical step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
STATE = PROJECT / ".runtime" / "openkylin-vm"
LIMA_STATE = STATE / "lima"
INSTANCE = "kylin"
ISO_NAME = "openKylin-Desktop-V3.0-20260905-arm64.iso"
ISO = STATE / ISO_NAME
ISO_BYTES = 7_348_721_664
OFFICIAL_MD5 = "62180a4fe3a9236a6ed1a93f9af7b52d"
CHECKSUM_SOURCE = "https://www.openkylin.top/downloads/os-en.html"
CONFIG = STATE / "openkylin.yaml"
VERIFIED = STATE / "image-verified.json"
SOURCE = "https://releases.openkylin.top/3.0/" + ISO_NAME
GIB = 1024 ** 3


def command(*args: str, timeout: int = 120) -> int:
    env = {**os.environ, "LIMA_HOME": str(LIMA_STATE)}
    # No model, SSH-agent, or proxy credentials are needed for an offline installer.
    for key in list(env):
        if key.startswith(("KMB_", "OPENAI_", "ANTHROPIC_")) or key in {
            "SSH_AUTH_SOCK", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "http_proxy", "https_proxy", "all_proxy",
        }:
            env.pop(key, None)
    return subprocess.run(["limactl", "--tty=false", *args], env=env,
                          check=False, timeout=timeout).returncode


def prepare() -> None:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("This configuration requires Apple Silicon macOS.")
    if shutil.which("limactl") is None:
        raise RuntimeError("Install Lima first; this script never installs host packages.")
    STATE.mkdir(parents=True, exist_ok=True)
    LIMA_STATE.mkdir(exist_ok=True)
    config = {
        "vmType": "vz", "arch": "aarch64", "os": "Linux",
        "images": [{"location": str(ISO), "arch": "aarch64"}],
        "cpus": 2, "memory": "6GiB", "disk": "12GiB",
        "plain": True, "mounts": [], "provision": [], "portForwards": [],
        "containerd": {"system": False, "user": False},
        "ssh": {"overVsock": False, "loadDotSSHPubKeys": False, "forwardAgent": False,
                "forwardX11": False, "forwardX11Trusted": False},
        "user": {"name": "kmb", "comment": "Synthetic benchmark VM", "uid": 1000,
                 "home": "/home/kmb", "shell": "/bin/bash"},
        "propagateProxyEnv": False, "video": {"display": "vz"},
        "audio": {"device": "none"},
        "vmOpts": {"vz": {"diskImageFormat": "raw",
                           "rosetta": {"enabled": False, "binfmt": False}}},
    }
    if CONFIG.exists():
        if json.loads(CONFIG.read_text()) != config:
            raise RuntimeError("Existing VM configuration differs; preserve and inspect it manually.")
    else:
        CONFIG.write_text(json.dumps(config, indent=2) + "\n")
    if command("validate", str(CONFIG)) != 0:
        raise RuntimeError("Lima rejected the configuration.")
    print(json.dumps({"config": str(CONFIG), "state": str(LIMA_STATE),
                      "virtual_disk_gib": 12, "memory_gib": 6, "cpus": 2,
                      "host_free_gib": round(shutil.disk_usage(STATE).free / GIB, 2),
                      "iso_complete": ISO.exists() and ISO.stat().st_size == ISO_BYTES,
                      "desktop_boot_verified": False}, indent=2))


def verify(expected_md5: str | None) -> None:
    if not ISO.is_file() or ISO.is_symlink() or ISO.stat().st_size != ISO_BYTES:
        raise RuntimeError(f"ISO incomplete or invalid; expected {ISO_BYTES} bytes.")
    if expected_md5 is None and VERIFIED.exists():
        expected_md5 = json.loads(VERIFIED.read_text()).get("official_expected_md5")
    if expected_md5 is None or not re.fullmatch(r"[0-9a-fA-F]{32}", expected_md5):
        raise RuntimeError("Provide --expected-md5 from the official image checksum page.")
    md5, sha = hashlib.md5(usedforsecurity=False), hashlib.sha256()
    with ISO.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            md5.update(chunk)
            sha.update(chunk)
    if md5.hexdigest() != expected_md5.lower():
        raise RuntimeError("ISO does not match the official MD5; no VM started.")
    record = {"filename": ISO_NAME, "bytes": ISO_BYTES, "official_source": SOURCE,
              "checksum_source": CHECKSUM_SOURCE, "checksum_source_image_id": 132,
              "official_expected_md5": expected_md5.lower(), "md5": md5.hexdigest(),
              "sha256": sha.hexdigest(), "checksum_verified": True}
    # Re-verifying the same bytes must preserve the recorded mirror provenance.
    # Never transfer provenance from a different image or an unverified record.
    try:
        previous = json.loads(VERIFIED.read_text())
    except (OSError, ValueError):
        previous = {}
    if (isinstance(previous, dict) and previous.get("checksum_verified") is True
            and all(previous.get(key) == record[key]
                    for key in ("filename", "bytes", "md5", "sha256"))):
        for key in ("download_sources", "download_provenance_file"):
            if key in previous:
                record[key] = previous[key]
    VERIFIED.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))


def link_install_media(*, replace_created_copy: bool = False) -> None:
    """Lima 2.1.2 consumes instance/image, renaming ISO media to instance/iso.

    The create command may copy/clone the ISO while preparing the disk. Only a
    duplicate just created by this call may be replaced by a hard link; preexisting
    media stays untouched. The VZ driver attaches the resulting ISO read-only.
    """
    instance_dir = LIMA_STATE / INSTANCE
    for name in ("image", "iso"):
        existing = instance_dir / name
        if existing.exists():
            if existing.is_symlink():
                raise RuntimeError("Existing installation media differs; refusing to replace it.")
            if not existing.samefile(ISO):
                if not replace_created_copy or existing.stat().st_size != ISO_BYTES:
                    raise RuntimeError("Existing installation media differs; refusing to replace it.")
                if (instance_dir / "ha.pid").exists():
                    raise RuntimeError("Never replace installation media while the VM is running.")
                existing.unlink()  # Only this invocation's Lima-created synthetic media copy.
                os.link(ISO, existing)
            return
    if (instance_dir / "disk").exists():
        raise RuntimeError("Existing disk has no matching Live ISO; inspect before booting.")
    os.link(ISO, instance_dir / "image")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "start", "status", "stop"))
    parser.add_argument("--expected-md5", default=OFFICIAL_MD5)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action == "verify":
        verify(args.expected_md5)
    elif args.action == "start":
        prepare()
        verify(args.expected_md5)
        if shutil.disk_usage(STATE).free < 20 * GIB:
            raise RuntimeError("Less than 20 GiB free; preserve host space before installation.")
        creating = not (LIMA_STATE / INSTANCE / "lima.yaml").exists()
        if creating and command("create", "--name=" + INSTANCE, str(CONFIG)) != 0:
            return 1
        link_install_media(replace_created_copy=creating)
        # The stock ISO may lack cloud-init/SSH. A readiness timeout is not proof
        # of failed boot; inspect the visible desktop and this dedicated VM log.
        return command("start", "--timeout=45s", INSTANCE, timeout=75)
    elif args.action == "status":
        return command("list", "--json")
    else:
        return command("stop", INSTANCE)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(str(exc)) from None
