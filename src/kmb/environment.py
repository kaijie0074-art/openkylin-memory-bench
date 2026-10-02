"""Read-only, credential-safe host diagnostics. Never starts a Docker daemon."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path


def docker_prefix() -> list[str]:
    context = os.environ.get("KMB_DOCKER_CONTEXT")
    return ["docker", "--context", context] if context else ["docker"]


def doctor() -> dict:
    release = {}
    source = Path("/etc/os-release")
    if source.is_file():
        for line in source.read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                release[key] = value.strip('"')
    docker = {"cli_available": bool(shutil.which("docker")), "daemon_available": False,
              "context": os.environ.get("KMB_DOCKER_CONTEXT", "default"),
              "server_os": None, "server_arch": None}
    if docker["cli_available"]:
        try:
            result = subprocess.run(docker_prefix() + ["info", "--format", "{{json .}}"],
                                    capture_output=True, text=True, timeout=10, check=False)
            if result.returncode == 0:
                info = json.loads(result.stdout)
                docker.update(daemon_available=True, server_os=info.get("OperatingSystem"),
                              server_arch=info.get("Architecture"))
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass  # Raw daemon output can contain paths or configuration; don't echo it.
    return {"system": platform.system(), "machine": platform.machine(),
            "os_id": release.get("ID"), "os_version": release.get("VERSION_ID"),
            "is_openkylin": release.get("ID", "").lower() == "openkylin",
            "docker": docker,
            "host_cli_available": {name: bool(shutil.which(name))
                                   for name in ("openclaw", "hermes")},
            "proxy_configured": any(os.environ.get(key) for key in ("KMB_BASE_URL", "KMB_AGENT_BASE_URL")),
            "model_configured": any(os.environ.get(key) for key in ("KMB_MODEL", "KMB_AGENT_MODEL")),
            "credential_configured": any(os.environ.get(key) for key in ("KMB_API_KEY", "KMB_AGENT_API_KEY")),
            "note": "Host CLI presence is not a container or openKylin validation."}
