"""Docker-isolated native agent adapters. Official runs fail closed on unknown budgets.

No personal agent profiles, rubrics, dataset roots or repository mounts are used.
The separate diagnostic entry point never produces completed benchmark evidence.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from kmb.environment import docker_prefix
from kmb.models import EvidenceBundle, EvidenceEvent, ModelIdentity, TaskSpec

AGENTS = {"openclaw": "kmb-openclaw:2026.9.6-terminal-v2", "hermes": "kmb-hermes:2ffa4977baf9-settle-v2"}
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 20_000_000
MAX_COLLECTION_ENTRIES = 10000
MAX_COLLECTION_SECONDS = 20
MAX_PROCESS_OUTPUT_BYTES = 32_000_000
SECRET_KEYS = re.compile(r"(?i)(api.?key|authorization|password|credential|access.?token|secret)")


def redact(value, secrets: tuple[str, ...] = ()):
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if SECRET_KEYS.search(str(k)) else redact(v, secrets)
                for k, v in value.items() if redact(str(k), secrets) == str(k)}
    if isinstance(value, list):
        return [redact(x, secrets) for x in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", value)
    return value


def agent_config(explicit: dict | None = None) -> dict:
    explicit = explicit or {}
    allowed = {"base_url", "model", "api_key", "transport", "image", "budget_enforced"}
    if set(explicit) - allowed:
        raise ValueError("unsupported model configuration fields")
    config = {"base_url": explicit.get("base_url", os.environ.get("KMB_AGENT_BASE_URL", "")),
              "model": explicit.get("model", os.environ.get("KMB_AGENT_MODEL", "")),
              "api_key": explicit.get("api_key", os.environ.get("KMB_AGENT_API_KEY", "")),
              "transport": explicit.get("transport", os.environ.get("KMB_AGENT_TRANSPORT", "chat_completions")),
              "image": explicit.get("image"),
              "budget_enforced": explicit.get("budget_enforced") is True}
    if config["transport"] not in {"chat_completions", "responses"}:
        raise ValueError("transport must be chat_completions or responses")
    for key in ("model", "api_key", "base_url"):
        if not isinstance(config[key], str) or "\x00" in config[key]:
            raise ValueError("invalid model configuration")
    if config["base_url"]:
        parsed = urlsplit(config["base_url"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an HTTP endpoint")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, query or fragment")
    return config


def runtime_root() -> Path:
    configured = os.environ.get("KMB_RUNTIME_ROOT")
    root = Path(configured) if configured else Path(__file__).resolve().parents[2] / ".runtime"
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _bounded_paths(root: Path, omissions: list[dict]):
    """Incremental traversal: bound directories/empty files as well as file contents."""
    pending, visited = [root], 0
    deadline = time.monotonic() + MAX_COLLECTION_SECONDS
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as scan:
                for entry in scan:
                    if visited >= MAX_COLLECTION_ENTRIES or time.monotonic() >= deadline:
                        omissions.append({"reason": "enumeration_limit"})
                        return
                    visited += 1
                    path = Path(entry.path)
                    yield path
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
        except OSError:
            omissions.append({"reason": "enumeration_error"})


def snapshot_text(root: Path, secrets: tuple[str, ...] = ()) -> tuple[dict[str, str], bool, list[dict]]:
    """Trusted host collection after process exit; refuse links/special/binary files."""
    files, skipped, total = {}, [], 0
    if root.is_symlink():
        return {}, False, [{"path": ".", "reason": "root_symlink"}]
    if not root.exists():
        return files, True, skipped
    if not root.is_dir():
        return {}, False, [{"reason": "root_not_directory"}]
    for path in _bounded_paths(root, skipped):
        rel = path.relative_to(root).as_posix()
        if redact(rel, secrets) != rel:
            # Preserve neither the path nor a renamed entry: renaming could collide,
            # and hiding an omission could turn a missing-file rule into a false pass.
            skipped.append({"reason": "credential_in_path"})
            continue
        if path.is_symlink():
            skipped.append({"path": rel, "reason": "symlink"})
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            skipped.append({"path": rel, "reason": "special_file"})
            continue
        try:
            size = path.stat().st_size
            name_bytes = len(rel.encode("utf-8"))
            if size > MAX_FILE_BYTES or total + size + name_bytes > MAX_TOTAL_BYTES:
                skipped.append({"path": rel, "reason": "size_limit"})
                continue
            # O_NOFOLLOW protects the final component; no agent is running at this point.
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            total += len(raw) + name_bytes
            if len(raw) > MAX_FILE_BYTES or b"\x00" in raw:
                raise UnicodeError("binary or over limit")
            files[rel] = raw.decode("utf-8")
        except (OSError, UnicodeError):
            skipped.append({"path": rel, "reason": "unreadable_or_binary"})
    return files, not skipped, skipped


def snapshot_inventory(root: Path, secrets: tuple[str, ...] = ()) -> tuple[dict, bool]:
    """Enumerate entry types without decoding bytes or following links."""
    entries, omissions = {}, []
    if root.is_symlink() or not root.is_dir():
        return entries, False
    for path in _bounded_paths(root, omissions):
        try:
            rel = path.relative_to(root).as_posix()
            if redact(rel, secrets) != rel:
                omissions.append({"reason": "credential_in_path"})
                continue
            if path.is_symlink():
                entries[rel] = "symlink"
                omissions.append({"reason": "symlink"})
            elif path.is_dir():
                entries[rel] = "directory"
            elif path.is_file():
                entries[rel] = "file"
            else:
                entries[rel] = "special"
        except OSError:
            omissions.append({"reason": "unreadable_entry"})
    return entries, not omissions


async def _process(argv: list[str], *, payload: dict | None = None,
                   env: dict | None = None, timeout: float = 60) -> dict:
    proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE,
                                               stdout=asyncio.subprocess.PIPE,
                                               stderr=asyncio.subprocess.PIPE, env=env,
                                               start_new_session=True)
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    captured = 0
    exceeded, timed_out = False, False

    def kill_group():
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    async def read_stream(name, stream):
        nonlocal captured, exceeded
        while chunk := await stream.read(65536):
            room = max(0, MAX_PROCESS_OUTPUT_BYTES - captured)
            buffers[name].extend(chunk[:room])
            captured += min(len(chunk), room)
            if len(chunk) > room and not exceeded:
                exceeded = True
                kill_group()
            # Drain buffered bytes without retaining them after killing the group.
            # Returning here could leave a paused pipe transport preventing wait()
            # from finishing even though the child has already been terminated.

    async def write_input():
        try:
            if data:
                proc.stdin.write(data)
                await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.stdin.close()

    tasks = [asyncio.create_task(read_stream("stdout", proc.stdout)),
             asyncio.create_task(read_stream("stderr", proc.stderr)),
             asyncio.create_task(write_input()), asyncio.create_task(proc.wait())]
    try:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout)
    except TimeoutError:
        timed_out = True
        kill_group()
    except BaseException:
        kill_group()
        raise
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await asyncio.wait_for(proc.wait(), 3)
        except TimeoutError:
            # A detached descendant can retain a pipe after its launcher was killed.
            # Close this owned subprocess transport; never wait without a bound.
            proc._transport.close()
    return {"returncode": proc.returncode,
            **{key: value.decode(errors="replace") for key, value in buffers.items()},
            "timed_out": timed_out, "output_limit_exceeded": exceeded,
            "output_complete": not exceeded and not timed_out}


def _host_ipv4(value: str) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValueError("docker_host_ip_must_be_ipv4") from exc
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise ValueError("docker_host_ip_must_be_reachable_from_guest")
    return str(address)


async def gateway_host_ip(base_url: str) -> str | None:
    """Resolve Mac host through the selected Colima VM, not the bridge gateway."""
    if urlsplit(base_url).hostname != "host.docker.internal":
        return None
    configured = os.environ.get("KMB_DOCKER_HOST_IP")
    if configured:
        return _host_ipv4(configured)
    context = os.environ.get("KMB_DOCKER_CONTEXT", "")
    if context == "colima" or context.startswith("colima-"):
        profile = context.removeprefix("colima-") if context != "colima" else "default"
        response = await _process(["colima", "ssh", "--profile", profile, "--",
                                   "getent", "hosts", "host.lima.internal"], timeout=10)
        if response["returncode"] == 0 and not response["timed_out"]:
            for line in response["stdout"].splitlines():
                parts = line.split()
                if len(parts) >= 2 and "host.lima.internal" in parts[1:]:
                    try:
                        return _host_ipv4(parts[0])
                    except ValueError:
                        continue
        raise RuntimeError("colima_gateway_host_unresolved_set_KMB_DOCKER_HOST_IP")
    return None  # Docker Desktop provides its own special DNS record.


async def _stop_container(name: str) -> bool:
    cleanup = await _process(docker_prefix() + ["rm", "-f", name], timeout=15)
    if cleanup["returncode"] == 0 and not cleanup["timed_out"]:
        return True
    # --rm can have removed a normally terminated container before our cleanup.
    # A successful inventory with no match confirms absence, unlike an inspect error.
    remaining = await _process(docker_prefix() + ["ps", "-aq", "--filter",
                                                 f"name=^/{name}$"], timeout=15)
    return (remaining["returncode"] == 0 and not remaining["timed_out"]
            and not remaining["stdout"].strip())


def _validate_image(image: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/@:-]*", image) or image.endswith(":latest"):
        raise ValueError("a pinned local image is required")
    if ":" not in image.rsplit("/", 1)[-1]:
        raise ValueError("an explicit image tag or digest is required")


def docker_command(agent: str, trial_root: Path, image: str, name: str,
                   host_ip: str | None = None) -> list[str]:
    if agent not in AGENTS:
        raise ValueError("agent must be openclaw or hermes")
    _validate_image(image)
    # Exactly one bind mount: an initially empty disposable public subject directory.
    host_args = ["--add-host", "host.docker.internal:" + _host_ipv4(host_ip)] if host_ip else []
    return docker_prefix() + ["run", "--pull=never", "--name", name, "--rm", "-i",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--pids-limit=128", "--memory=2g", "--cpus=1", "--network=bridge",
        "--tmpfs=/tmp:rw,nosuid,nodev,size=256m", "--user=10001:10001",
        "--mount", f"type=bind,src={trial_root},dst=/trial",
        "--workdir=/trial/workspace", "--env", "KMB_AGENT_BASE_URL", "--env", "KMB_AGENT_MODEL",
        "--env", "KMB_AGENT_API_KEY", "--env", "KMB_AGENT_TRANSPORT", *host_args, image,
        "python3", "/opt/kmb/agent-worker.py", agent]


def _decode(stdout: str) -> dict:
    parsed = json.loads(stdout)
    if not isinstance(parsed, dict) or parsed.get("worker_protocol") != 1:
        raise ValueError("unexpected worker protocol")
    return parsed


async def probe_agent(agent: str, model_config: dict | None = None) -> dict:
    """Inspect pinned image/CLI and documented capabilities; never calls a model."""
    return await _run_isolated(None, agent, None, model_config, mode="probe")


async def run_task(task: TaskSpec, agent: str, output_dir: Path,
                   model_config: dict | None = None) -> EvidenceBundle:
    return await _run_isolated(task, agent, output_dir, model_config, mode="benchmark")


async def run_diagnostic(task: TaskSpec, agent: str, output_dir: Path,
                         model_config: dict | None = None) -> EvidenceBundle:
    """Explicit live integration diagnostic; status never becomes completed.

    Unknown action-count enforcement is allowed only here, under a wall timeout.
    No use in rankings or claims about bounded benchmark performance.
    """
    return await _run_isolated(task, agent, output_dir, model_config, mode="diagnostic")


async def _run_isolated(task, agent, output_dir, model_config, *, mode):
    if agent not in AGENTS:
        raise ValueError("agent must be openclaw or hermes")
    started = time.monotonic()
    config = agent_config(model_config)
    secrets = (config["api_key"],)
    run_id = uuid.uuid4().hex
    image = config["image"] or os.environ.get(f"KMB_{agent.upper()}_IMAGE", AGENTS[agent])
    env = dict(os.environ)
    for key, value in config.items():
        if key not in {"image", "budget_enforced"}:
            env["KMB_AGENT_" + key.upper()] = value
    event_data, before, after, memory = [], {}, {}, {}
    files_complete = False
    inventory, inventory_complete = None, False
    result = {"status": "infrastructure_error", "error": "probe_not_run"}
    native = {}
    trial = None
    launched = False
    container = "kmb-" + run_id
    try:
        _validate_image(image)
        if not shutil.which("docker"):
            raise RuntimeError("docker_cli_unavailable")
        inspected = await _process(docker_prefix() + ["image", "inspect", image,
                                   "--format", "{{json .Id}}"], timeout=15)
        if inspected["returncode"] != 0:
            raise RuntimeError("pinned_image_unavailable_build_explicitly")
        image_id = json.loads(inspected["stdout"])
        if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
            raise RuntimeError("image_identity_unverified")
        host_ip = await gateway_host_ip(config["base_url"]) if mode != "probe" else None
        trial = Path(tempfile.mkdtemp(prefix="subject-", dir=runtime_root()))
        trial.chmod(0o777)  # Only this disposable, synthetic subject is guest-writable.
        workspace = trial / "workspace"
        workspace.mkdir(mode=0o777)
        workspace.chmod(0o777)
        if task:
            for rel, content in task.initial_files.items():
                target = workspace / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                target.chmod(0o666)
            for directory in workspace.rglob("*"):
                if directory.is_dir():
                    directory.chmod(0o777)
        before, before_complete, before_skipped = snapshot_text(workspace, secrets)
        payload = {"mode": mode, "public_input": task.public_input() if task else {},
                   "gateway_budget_enforced": config["budget_enforced"]}
        deadline = task.budget.timeout_seconds + 30 if task else 90
        launched = True
        observed = await _process(docker_command(agent, trial, image_id, container, host_ip),
                                  payload=payload, env=env, timeout=deadline)
        native["worker_stdout.txt"] = observed["stdout"]
        native["worker_stderr.txt"] = observed["stderr"]
        if observed.get("output_limit_exceeded"):
            stopped = await _stop_container(container)
            result = {"status": "infrastructure_error" if stopped else "execution_unknown",
                      "error": "native_output_limit_exceeded", "native_output_complete": False,
                      "container_stop_confirmed": stopped}
        elif observed["timed_out"]:
            # Killing docker CLI is not proof the daemon stopped the container.
            stopped = await _stop_container(container)
            result = {"status": "budget_exhausted" if stopped else "execution_unknown",
                      "error": "wall_timeout", "container_stop_confirmed": stopped}
        elif observed["returncode"] != 0:
            # CLI failure can mean a connection was lost while the daemon kept running.
            stopped = await _stop_container(container)
            result = {"status": "infrastructure_error" if stopped else "execution_unknown",
                      "error": "container_or_worker_failed", "container_stop_confirmed": stopped}
        else:
            result["container_stop_confirmed"] = True
            result = _decode(observed["stdout"])
            result["container_stop_confirmed"] = True
        if result.get("container_stop_confirmed") is False:
            raise RuntimeError("container_stop_unconfirmed_state_retained")
        after, after_complete, skipped = snapshot_text(workspace, secrets)
        inventory, inventory_complete = snapshot_inventory(workspace, secrets)
        files_complete = before_complete and after_complete
        # Snapshot from trusted collector, never from the agent's claimed final answer.
        event_data.append(("file_snapshot", {"collector": "host_after_container_exit",
                                            "complete": files_complete, "skipped": skipped,
                                            "before_skipped": before_skipped}))
        memory_root = trial / "subject"
        memory_all, memory_complete, memory_skipped = snapshot_text(memory_root, secrets)
        memory = {k: v for k, v in memory_all.items()
                  if k.endswith(".md") and ("memor" in k.lower() or k.endswith("USER.md"))}
        memory.update({"workspace/" + k: v for k, v in after.items()
                       if k in {"MEMORY.md", "USER.md"} or k.startswith("memory/")})
        # Native text and JSON are preserved with mandatory redaction. SQLite is exported
        # by the trusted worker; opaque binary DB copies may contain secrets and are omitted.
        for key, value in memory_all.items():
            if key.endswith((".md", ".log", ".jsonl")):
                native["subject/" + key] = value
        result["native_capture_complete"] = memory_complete
        result["native_capture_omissions"] = memory_skipped
        result["image_id"] = image_id
    except (OSError, RuntimeError, ValueError) as exc:
        if launched and "container_stop_confirmed" not in result:
            try:
                result["container_stop_confirmed"] = await _stop_container(container)
            except (OSError, RuntimeError, ValueError):
                result["container_stop_confirmed"] = False
        preserve_state = result.get("container_stop_confirmed") is False
        result = {"status": "execution_unknown" if preserve_state else "infrastructure_error",
                  "error": str(exc)}
        if preserve_state:
            result["container_stop_confirmed"] = False
    except asyncio.CancelledError:
        try:
            result["container_stop_confirmed"] = await asyncio.shield(_stop_container(container))
        except (OSError, RuntimeError, TimeoutError, asyncio.CancelledError):
            result["container_stop_confirmed"] = False
        raise
    finally:
        # A failed forced stop leaves state for inspection, never races a live writer.
        if trial is not None and result.get("container_stop_confirmed") is not False:
            try:
                shutil.rmtree(trial)
            except OSError:
                result["runtime_cleanup"] = "incomplete_synthetic_state_retained"
    result = redact(result, secrets)
    if mode == "probe":
        return result
    status = result.get("status", "execution_unknown")
    if status not in {"completed", "task_failed", "budget_exhausted", "infrastructure_error", "execution_unknown"}:
        status = "execution_unknown"
    if mode == "diagnostic" and status == "completed":
        status = "execution_unknown"
        result["error"] = "diagnostic_only_action_budget_unverified"
    event_data.append(("adapter_result", result))
    for item in result.get("sessions", []):
        event_data.append(("native_session", {**item, "source": "subject_native_cli",
                          "independent_execution_observation": False}))
        if isinstance(item.get("parsed"), list):
            for record in item["parsed"]:
                if record.get("type") == "tool_result":
                    event_data.append(("native_tool_report", {"source": "subject_native_cli",
                        "session_id": item.get("session_id"), "execution_verified": False,
                        "native_record": record,
                        "evidence_scope": "subject report; cannot establish objective execution success"}))
    output_dir = Path(output_dir)
    native_dir = output_dir / "native" / run_id
    native_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, content in native.items():
        if redact(name, secrets) != name:
            manifest.append({"omitted": True, "reason": "credential_in_path"})
            continue
        target = native_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = redact(content, secrets).encode("utf-8")
        target.write_bytes(data)
        manifest.append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "redacted": True})
    (native_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    bundle = EvidenceBundle(run_id=run_id, task_id=task.id, ability=task.ability,
        family=task.family, split=task.split, agent=agent,
        agent_version=result.get("agent_version", "unknown"), provenance="real", status=status,
        error=result.get("error"), environment={"host_system": os.uname().sysname,
            "guest_os_id": str(result.get("os_id", "unknown")),
            "image_id": str(result.get("image_id", "unknown")), "execution_mode": mode,
            "actions_complete": "false", "native_action_provenance": "subject_reported",
            "action_budget": "trusted_gateway" if config["budget_enforced"] else "unverified"},
        model=ModelIdentity(requested=config["model"] or None, returned=None, provider="custom"),
        events=[EvidenceEvent(id=f"e{i}", kind=kind, data=redact(data, secrets))
                for i, (kind, data) in enumerate(event_data)],
        files_before=redact(before, secrets), files_after=redact(after, secrets),
        files_complete=files_complete, file_inventory_after=inventory,
        file_inventory_complete=inventory_complete, memory_observable=bool(memory),
        memory_snapshot=redact(memory, secrets), elapsed_seconds=time.monotonic()-started)
    return bundle.freeze()
