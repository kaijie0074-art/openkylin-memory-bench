"""Image-resident supervisor. Inputs arrive through stdin, never a repository mount.

Each native CLI process receives only its current prompt. Future sessions remain in
supervisor memory. Output is one JSON document; raw native stdout/stderr are embedded.
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path("/trial")
SUBJECT = ROOT / "subject"
WORKSPACE = ROOT / "workspace"
HERMES_AUX_TASKS = (
    "vision", "compression", "skills_hub", "approval", "review", "mcp", "title_generation",
    "memory_query_rewrite", "tts_audio_tags", "triage_specifier", "kanban_decomposer",
    "profile_describer", "goal_judge", "curator", "monitor", "background_review",
    "moa_reference", "moa_aggregator",
)
HERMES_CONFIG_PROBE = """
import json, os, sys
from hermes_cli.config import DEFAULT_CONFIG, load_config, validate_config_structure
config = load_config()
issues = [{"severity": issue.severity, "message": issue.message}
          for issue in validate_config_structure(config)]
model = config.get("model", {})
endpoint = os.environ.get("KMB_AGENT_BASE_URL") or "http://127.0.0.1:9/v1"
name = os.environ.get("KMB_AGENT_MODEL") or "probe-placeholder"
main_matches = (isinstance(model, dict) and model.get("provider") == "custom"
                and model.get("base_url") == endpoint and model.get("default") == name
                and model.get("api_mode") == "chat_completions")
aux_names = [key for key, value in DEFAULT_CONFIG.get("auxiliary", {}).items()
             if isinstance(value, dict) and "provider" in value]
unmatched = [key for key in aux_names if any(
    config.get("auxiliary", {}).get(key, {}).get(field) != expected
    for field, expected in {"provider": "main", "model": name, "base_url": endpoint,
                            "api_mode": "chat_completions"}.items())]
fallback_disabled = config.get("fallback_model") == []
valid = main_matches and not unmatched and fallback_disabled and not any(
    issue["severity"] == "error" for issue in issues)
print(json.dumps({"valid": valid, "validator": "hermes_native_config_loader_and_structure",
                  "issues": issues, "main_route_matches": main_matches,
                  "fallback_disabled": fallback_disabled, "unmatched_auxiliary": unmatched}))
sys.exit(0 if valid else 1)
"""


def native(command, env, *, timeout):
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env, start_new_session=True,
                            cwd=WORKSPACE)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return {"stdout": stdout, "stderr": stderr, "returncode": proc.returncode,
                "timed_out": False}
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
        return {"stdout": stdout, "stderr": stderr, "returncode": proc.returncode,
                "timed_out": True}


def hermes_finalize(finalize, cli, titles, deadline):
    """Observe pinned Hermes lifecycle without changing its prompts or memory policy.

    The chat CLI emits its result before cleanup and does not join auto-title daemon
    threads. Join them while their auxiliary clients and session DB are still open.
    The outer process supervisor remains the authoritative wall-clock limit.
    """
    event = {"type": "kmb_settle", "protocol": 1, "native_cleanup": "not_returned",
             "title_threads": {"status": "unobserved"},
             "memory_queue": {"status": "unobserved"},
             "background_consolidation": "unverified"}
    manager = getattr(getattr(cli, "agent", None), "_memory_manager", None)
    try:
        before = list(titles._UPGRADE_THREADS)
        titles.wait_for_title_upgrades(timeout=max(0.0, deadline - time.monotonic()))
        remaining = sum(thread.is_alive() for thread in list(titles._UPGRADE_THREADS))
        event["title_threads"] = {"status": "pending" if remaining else "drained",
                                  "observed": len(before), "remaining": remaining}
        finalize(cli)
        event["native_cleanup"] = "returned"
        if manager is None:
            event["memory_queue"] = {"status": "not_present"}
        else:
            state = getattr(manager, "shutdown_drain_state", {})
            # Only copy the known numeric/status fields, never provider configuration.
            event["memory_queue"] = {
                key: state[key] for key in ("status", "abandoned_writes",
                    "abandoned_prefetches", "active_tasks") if key in state}
            event["memory_queue"].setdefault("status", "unobserved")
    except BaseException as exc:
        event["error_type"] = type(exc).__name__
        raise
    finally:
        print(json.dumps(event), flush=True)


def hermes_cli():
    """Run the installed official entry point with a scoped lifecycle observer."""
    import cli as facade
    from agent import title_generator
    from hermes_cli.main import main as official_main

    deadline = float(os.environ["KMB_AGENT_DEADLINE_MONOTONIC"])
    original = facade._finalize_single_query
    facade._finalize_single_query = lambda cli: hermes_finalize(
        original, cli, title_generator, deadline)
    # Native watchdog uses os._exit(0); supervisor killpg reports timeout honestly.
    os.environ["HERMES_EXIT_WATCHDOG_S"] = "0"
    sys.argv = ["hermes", *sys.argv[2:]]
    try:
        return official_main()
    finally:
        facade._finalize_single_query = original


def release():
    values = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip('"')
    return values.get("ID", "unknown")


def configured(agent, model, endpoint, token):
    """Write only synthetic profile settings. The token belongs to the trial gateway."""
    env = dict(os.environ)
    SUBJECT.mkdir(exist_ok=True)
    profile = SUBJECT / agent
    profile.mkdir(exist_ok=True)
    env.update(XDG_CACHE_HOME=str(profile / "cache"), XDG_CONFIG_HOME=str(profile / "config"),
               XDG_DATA_HOME=str(profile / "data"))
    if agent == "openclaw":
        config = {"models": {"mode": "replace", "providers": {"kmb": {
            "baseUrl": endpoint, "apiKey": token, "api": "openai-completions",
            "models": [{"id": model, "name": model, "input": ["text"],
                        "contextWindow": 128000, "maxTokens": 8192}]}}},
            "agents": {"defaults": {"workspace": str(WORKSPACE),
                "model": {"primary": "kmb/" + model}, "heartbeat": {"every": "0m"}}},
            "memory": {"search": {"enabled": False}}}
        config_path = profile / "openclaw.json"
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)
        env.update(OPENCLAW_STATE_DIR=str(profile), OPENCLAW_CONFIG_PATH=str(config_path),
                   OPENCLAW_WORKSPACE_DIR=str(WORKSPACE), OPENCLAW_OFFLINE="1")
    else:
        # JSON is valid YAML; avoids adding parser dependencies to the supervisor.
        config = {"model": {"provider": "custom", "default": model,
                            "base_url": endpoint, "context_length": 128000,
                            "api_mode": "chat_completions"},
            "terminal": {"backend": "local", "cwd": str(WORKSPACE)},
            "fallback_model": [], "memory": {"memory_enabled": True,
                "user_profile_enabled": True},
            "auxiliary": {name: {"provider": "main", "model": model,
                                    "base_url": endpoint, "api_key": token,
                                    "api_mode": "chat_completions"}
                          for name in HERMES_AUX_TASKS}}
        (profile / "config.yaml").write_text(json.dumps(config))
        env.update(HERMES_HOME=str(profile), OPENAI_BASE_URL=endpoint, OPENAI_API_KEY=token,
                   HERMES_INFERENCE_MODEL=model)
    return env


def probe(agent, env):
    help_cmd = [agent, "agent" if agent == "openclaw" else "chat", "--help"]
    help_result = native(help_cmd, env, timeout=40)
    version = native([agent, "--version"], env, timeout=20)
    content = help_result["stdout"] + help_result["stderr"]
    flags = (["--message", "--session-id", "--model", "--local", "--json"]
             if agent == "openclaw" else ["--query-file", "--model", "--provider", "--format", "--toolsets"])
    validation_command = (["openclaw", "config", "validate", "--json"] if agent == "openclaw"
                          else ["python3", "-c", HERMES_CONFIG_PROBE])
    validation = native(validation_command, env, timeout=20)
    return {"cli_available": help_result["returncode"] == 0,
            "required_flags": {flag: flag in content for flag in flags},
            "native_action_budget_verified": False,
            "agent_version": (version["stdout"].strip() or "unknown")[:200],
            "help_stdout": help_result["stdout"], "help_stderr": help_result["stderr"],
            "config_validation": validation,
            "os_id": release()}


def openclaw_terminal(envelope, observed):
    """Use the native terminal envelope, never the assistant's completion claim.

    replayInvalid and livenessState are not terminal flags: the pinned CLI can
    report replayInvalid=true/working even alongside a normal final stop.
    """
    meta = envelope.get("meta")
    meta = meta if isinstance(meta, dict) else {}
    agent_meta = meta.get("agentMeta")
    agent_meta = agent_meta if isinstance(agent_meta, dict) else {}
    completion = meta.get("completion")
    completion = completion if isinstance(completion, dict) else {}
    actual = envelope.get("sessionId") or agent_meta.get("sessionId")
    actual = actual if isinstance(actual, str) and actual.strip() else None
    raw_reasons = [item.get(key) for item in (envelope, meta, completion, agent_meta)
                   for key in ("stopReason", "finishReason") if item.get(key) is not None]
    reasons = {value.strip().lower() for value in raw_reasons if isinstance(value, str)}
    normal = {"stop", "end_turn", "stop_sequence", "complete", "completed", "success"}
    budget = {"length", "max_tokens", "max_output_tokens", "max_completion_tokens",
              "max_turns", "max_iterations", "token_limit", "tool_budget_exhausted",
              "budget_exhausted", "timeout", "timed_out"}
    failed = {"error", "failed", "failure", "api_error", "aborted", "abort",
              "cancelled", "canceled", "content_filter"}
    payloads = envelope.get("payloads", [])
    explicit_error = any(item.get("error") for item in (envelope, meta, completion, agent_meta))
    explicit_error = explicit_error or (isinstance(payloads, list) and any(
        isinstance(payload, dict) and (payload.get("isError") is True or payload.get("error"))
        for payload in payloads))
    abort_values = [item.get("aborted") for item in (envelope, meta) if "aborted" in item]
    if observed.get("timed_out"):
        status, reason = "budget_exhausted", "native_cli_timeout"
    elif observed.get("returncode") != 0 or explicit_error or reasons & failed:
        status, reason = "infrastructure_error", "native_terminal_failed"
    elif reasons & budget:
        status, reason = "budget_exhausted", "native_generation_budget_exhausted"
    elif any(value is True for value in abort_values):
        status, reason = "infrastructure_error", "native_terminal_aborted"
    elif (not actual or not raw_reasons or any(not isinstance(value, str) for value in raw_reasons)
          or any(not isinstance(value, bool) for value in abort_values) or not reasons <= normal):
        status, reason = "execution_unknown", "native_terminal_reason_unrecognized"
    else:
        status, reason = "completed", None
    return {"session_id": actual, "terminal_observed": bool(raw_reasons or abort_values or explicit_error),
            "terminal_success": True if status == "completed" else None if status == "execution_unknown" else False,
            "terminal_status": status, "terminal_reason": reason}


def extract(agent, observed, requested_session):
    if agent == "openclaw":
        try:
            envelope = json.loads(observed["stdout"])
        except (ValueError, TypeError):
            return {"session_id": None, "terminal_observed": False, "parsed": None}
        if not isinstance(envelope, dict):
            return {"session_id": None, "terminal_observed": False, "parsed": envelope}
        return {**openclaw_terminal(envelope, observed),
                "requested_session_id": requested_session, "parsed": envelope}
    records = []
    invalid = 0
    for line in observed["stdout"].splitlines():
        try:
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
        except ValueError:
            invalid += 1
    finals = [x for x in records if x.get("type") == "result"]
    final = finals[-1] if finals else {}
    session_id = final.get("session_id")
    code = final.get("exit_code")
    success = code == 0 and not final.get("error") if isinstance(code, int) else None
    settles = [x for x in records if x.get("type") == "kmb_settle" and x.get("protocol") == 1]
    settle = settles[-1] if settles else {}
    queue = settle.get("memory_queue", {})
    settle_success = (settle.get("native_cleanup") == "returned"
                      and settle.get("title_threads", {}).get("status") == "drained"
                      and queue.get("status") in {"drained", "not_present"}
                      and all(queue.get(key, 0) == 0 for key in
                              ("abandoned_writes", "abandoned_prefetches", "active_tasks")))
    return {"session_id": session_id, "terminal_observed": bool(finals),
            "terminal_success": success, "native_exit_code": code,
            "parsed": records, "non_json_lines": invalid,
            "settle_observed": bool(settles), "settle_success": settle_success,
            "settle": settle}


def export_databases():
    """After CLI exit, read all native tables; never modify a live agent database.

    Text serialization is redacted by the trusted host before saving. Binary database
    originals are not archived because they can contain raw credentials.
    """
    exports = []
    for db in sorted(SUBJECT.rglob("*")):
        if db.is_symlink() or db.suffix not in {".db", ".sqlite"} or not db.is_file():
            continue
        if db.stat().st_size > 40_000_000:
            exports.append({"path": str(db.relative_to(SUBJECT)), "error": "size_limit"})
            continue
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            data = {}
            for table in tables:
                name = table["name"]
                quoted = '"' + name.replace('"', '""') + '"'
                rows = conn.execute("SELECT * FROM " + quoted + " LIMIT 10001").fetchall()
                if len(rows) > 10000:
                    data[name] = {"error": "row_limit", "truncated": True}
                else:
                    data[name] = [{k: (v.hex() if isinstance(v, bytes) else v)
                                   for k, v in dict(row).items()} for row in rows]
            conn.close()
            exports.append({"path": str(db.relative_to(SUBJECT)), "tables": data})
        except sqlite3.Error as exc:
            exports.append({"path": str(db.relative_to(SUBJECT)), "error": type(exc).__name__})
    return exports


def main():
    # This filesystem contains only disposable synthetic state. Host-side collectors
    # must be able to remove it after the non-root guest exits (different host UID).
    previous_umask = os.umask(0)
    try:
        return _main()
    finally:
        os.umask(previous_umask)


def _main():
    agent = sys.argv[1]
    if agent not in {"openclaw", "hermes"}:
        raise ValueError("unknown agent")
    payload = json.load(sys.stdin)
    mode = payload.get("mode")
    if mode not in {"probe", "benchmark", "diagnostic"}:
        raise ValueError("unknown mode")
    model = os.environ.get("KMB_AGENT_MODEL", "")
    endpoint = os.environ.get("KMB_AGENT_BASE_URL", "")
    token = os.environ.get("KMB_AGENT_API_KEY", "")
    env = configured(agent, model or "probe-placeholder", endpoint or "http://127.0.0.1:9/v1", token)
    capabilities = probe(agent, env)
    result = {"worker_protocol": 1, "status": "infrastructure_error", **capabilities,
              "sessions": [], "mode": mode, "settle": "not_observed",
              "memory_condition": ("workspace Markdown; semantic embedding search disabled; local CLI"
                                   if agent == "openclaw" else "native memory and session_search; fresh CLI sessions")}
    if not capabilities["cli_available"] or not all(capabilities["required_flags"].values()):
        result["error"] = "cli_probe_failed"
        return result
    validation = capabilities.get("config_validation")
    if validation and (validation["returncode"] != 0 or validation["timed_out"]):
        result["error"] = "native_config_validation_failed"
        return result
    if mode == "probe":
        result.update(status="probe_passed", error=None)
        return result
    if not all((model, endpoint, token)):
        result["error"] = "explicit_trial_gateway_configuration_required"
        return result
    if os.environ.get("KMB_AGENT_TRANSPORT") != "chat_completions":
        result["error"] = "v1_requires_verified_chat_completions_gateway"
        return result
    if mode == "benchmark" and payload.get("gateway_budget_enforced") is not True:
        result["error"] = "tool_action_budget_unverified"
        return result
    public = payload["public_input"]
    deadline = time.monotonic() + public["budget"]["timeout_seconds"]
    seen_sessions = set()
    result["status"] = "completed"
    for session in public["sessions"]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result.update(status="budget_exhausted", error="wall_timeout")
            break
        requested = uuid.uuid4().hex
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir="/tmp", suffix=".txt") as prompt:
            prompt.write(session["prompt"])
            prompt.flush()
            if agent == "openclaw":
                command = ["openclaw", "agent", "--local", "--session-id", requested,
                           "--message", session["prompt"], "--model", "kmb/" + model, "--json"]
            else:
                command = [sys.executable, str(Path(__file__).resolve()), "--hermes-cli",
                           "chat", "--query-file", prompt.name,
                           "--model", model, "--provider", "custom", "--format", "stream-json",
                           "--toolsets", "terminal,file,memory,session_search"]
            session_env = {**env, "KMB_AGENT_DEADLINE_MONOTONIC": str(deadline)}
            observed = native(command, session_env, timeout=remaining)
        parsed = extract(agent, observed, requested)
        result["sessions"].append({"input_session_label": session["id"], **parsed,
                                    "native": observed})
        if observed["timed_out"]:
            result.update(status="budget_exhausted", error="native_cli_timeout")
            break
        if observed["returncode"] != 0:
            result.update(status="infrastructure_error", error="native_cli_failed")
            break
        if not parsed["terminal_observed"] or not parsed["session_id"]:
            result.update(status="execution_unknown", error="native_session_or_terminal_unobserved")
            break
        if agent == "openclaw" and parsed.get("terminal_status") == "budget_exhausted":
            result.update(status="budget_exhausted", error=parsed["terminal_reason"])
            break
        if parsed.get("terminal_success") is not True:
            result.update(status=("infrastructure_error" if parsed.get("terminal_success") is False
                                  else "execution_unknown"), error="native_terminal_not_successful")
            break
        if agent == "hermes" and not parsed.get("settle_success"):
            result.update(status="execution_unknown", error="native_background_settle_unobserved")
            break
        if parsed["session_id"] in seen_sessions:
            result.update(status="execution_unknown", error="session_reused")
            break
        seen_sessions.add(parsed["session_id"])
    result["native_databases"] = export_databases()
    result["settle"] = ("title_threads_and_memory_queue_drained; background_consolidation_unverified"
                        if agent == "hermes" and result["status"] == "completed" else
                        "native_process_exit_observed; background_consolidation_unverified")
    result["budget_enforcement"] = ("trusted_gateway" if payload.get("gateway_budget_enforced")
                                    else "unverified_diagnostic_only")
    return result


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--hermes-cli":
        sys.exit(hermes_cli())
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError) as exc:
        # No exception text: parsers/provider errors may interpolate credentials.
        print(json.dumps({"worker_protocol": 1, "status": "infrastructure_error",
                          "error": "worker_error:" + type(exc).__name__}))
