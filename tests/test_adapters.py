from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from kmb import adapters
from kmb.environment import docker_prefix
from kmb.models import TaskSpec


@pytest.fixture
def task():
    return TaskSpec(id="private-case-id", ability="update", family="private-family",
                    split="holdout", initial_files={"initial.txt": "hello"},
                    sessions=[{"id": "first", "prompt": "Remember synthetic value A."},
                              {"id": "last", "prompt": "Write its value.", "final": True}])


@pytest.fixture
def worker():
    path = Path(__file__).parents[1] / "scripts" / "agent-worker.py"
    spec = importlib.util.spec_from_file_location("kmb_agent_worker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_docker_arguments_do_not_mount_repository_or_put_token_in_argv(tmp_path, monkeypatch):
    monkeypatch.setenv("KMB_DOCKER_CONTEXT", "colima-kmb")
    command = adapters.docker_command("hermes", tmp_path, "sha256:" + "a" * 64, "subject-id")
    assert command[:3] == ["docker", "--context", "colima-kmb"]
    assert command.count("--mount") == 1
    assert f"type=bind,src={tmp_path},dst=/trial" in command
    assert "--pull=never" in command and "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "/var/run/docker.sock" not in " ".join(command)
    assert "KMB_AGENT_API_KEY" in command
    assert "--privileged" not in command


def test_reject_latest_and_invalid_agent(tmp_path):
    with pytest.raises(ValueError):
        adapters.docker_command("hermes", tmp_path, "anything:latest", "test")
    with pytest.raises(ValueError):
        adapters.docker_command("hermes", tmp_path, "anything", "test")
    with pytest.raises(ValueError):
        adapters.docker_command("other", tmp_path, "valid:v1", "test")


def test_runtime_rejects_latest_before_inspecting_or_resolving_to_digest(task, tmp_path, monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda _: "/fake/docker")

    async def fake(*args, **kwargs):
        pytest.fail("invalid image selection must not reach Docker")

    monkeypatch.setattr(adapters, "_process", fake)
    result = asyncio.run(adapters.run_task(task, "hermes", tmp_path, {"image": "anything:latest"}))
    assert result.status == "infrastructure_error"
    assert "pinned local image" in result.error


def test_colima_resolves_actual_host_and_explicit_override(monkeypatch, tmp_path):
    monkeypatch.setenv("KMB_DOCKER_CONTEXT", "colima-kmb")
    monkeypatch.delenv("KMB_DOCKER_HOST_IP", raising=False)
    seen = []

    async def fake(command, **kwargs):
        seen.append(command)
        return {"returncode": 0, "stdout": "192.168.5.2 host.lima.internal\n",
                "stderr": "", "timed_out": False}

    monkeypatch.setattr(adapters, "_process", fake)
    address = asyncio.run(adapters.gateway_host_ip("http://host.docker.internal:1234/v1"))
    assert address == "192.168.5.2"
    assert seen == [["colima", "ssh", "--profile", "kmb", "--", "getent", "hosts", "host.lima.internal"]]
    command = adapters.docker_command("hermes", tmp_path, "valid:v1", "test", address)
    assert command[command.index("--add-host") + 1] == "host.docker.internal:192.168.5.2"
    monkeypatch.setenv("KMB_DOCKER_HOST_IP", "192.168.20.2")
    assert asyncio.run(adapters.gateway_host_ip("http://host.docker.internal/v1")) == "192.168.20.2"
    assert len(seen) == 1


def test_colima_gateway_resolution_fails_closed(monkeypatch):
    monkeypatch.setenv("KMB_DOCKER_CONTEXT", "colima-kmb")
    monkeypatch.delenv("KMB_DOCKER_HOST_IP", raising=False)

    async def fake(*args, **kwargs):
        return {"returncode": 1, "stdout": "", "stderr": "", "timed_out": False}

    monkeypatch.setattr(adapters, "_process", fake)
    with pytest.raises(RuntimeError, match="colima_gateway_host_unresolved"):
        asyncio.run(adapters.gateway_host_ip("http://host.docker.internal/v1"))
    monkeypatch.setenv("KMB_DOCKER_HOST_IP", "host-gateway")
    with pytest.raises(ValueError, match="ipv4"):
        asyncio.run(adapters.gateway_host_ip("http://host.docker.internal/v1"))


def test_stop_confirmation_accepts_successful_absence_not_inspect_failure(monkeypatch):
    async def fake(command, **kwargs):
        return {"returncode": 0 if "ps" in command else 1, "stdout": "", "stderr": "",
                "timed_out": False}

    monkeypatch.setattr(adapters, "_process", fake)
    assert asyncio.run(adapters._stop_container("kmb-test")) is True


@pytest.mark.parametrize("url", ["https://secret@example.org/v1", "file:///tmp/test",
                               "https://example.org/v1?token=secret", "https://example.org/#token"])
def test_reject_credential_bearing_or_non_http_urls(url):
    with pytest.raises(ValueError):
        adapters.agent_config({"base_url": url})


def test_explicit_gateway_is_distinct_from_environment_assertion(monkeypatch):
    monkeypatch.setenv("KMB_AGENT_BUDGET_ENFORCED", "true")
    assert adapters.agent_config()["budget_enforced"] is False
    assert adapters.agent_config({"budget_enforced": True})["budget_enforced"] is True
    assert adapters.agent_config({"budget_enforced": "true"})["budget_enforced"] is False


def test_redaction_nested_text_and_credential_fields():
    observed = {"api_key": "unrelated-secret", "sessions": [{"stdout": "token=known-secret"}],
                "Authorization": "Bearer other-secret", "normal": "Bearer abcd1234"}
    clean = adapters.redact(observed, ("known-secret",))
    assert "known-secret" not in json.dumps(clean)
    assert "other-secret" not in json.dumps(clean)
    assert clean["api_key"] == "[REDACTED]"
    assert clean["normal"] == "Bearer [REDACTED]"
    assert adapters.redact({"known-secret.txt": "a", "[REDACTED].txt": "b"},
                           ("known-secret",)) == {"[REDACTED].txt": "b"}


def test_snapshot_omits_sensitive_paths_without_recording_them(tmp_path):
    (tmp_path / "trial-secret.txt").write_text("private token in filename")
    (tmp_path / "[REDACTED].txt").write_text("distinct legitimate filename")
    files, complete, skipped = adapters.snapshot_text(tmp_path, ("trial-secret",))
    assert files == {"[REDACTED].txt": "distinct legitimate filename"}
    assert not complete and skipped == [{"reason": "credential_in_path"}]
    assert "trial-secret" not in json.dumps([files, skipped])


def test_snapshot_never_follows_symlinks_and_marks_binary_incomplete(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "private.txt").write_text("not-public")
    (workspace / "steal.txt").symlink_to(tmp_path / "private.txt")
    (workspace / "binary.bin").write_bytes(b"\x00\xff")
    (workspace / "good.txt").write_text("visible")
    files, complete, skipped = adapters.snapshot_text(workspace)
    assert files == {"good.txt": "visible"}
    assert complete is False and len(skipped) == 2
    root_link = tmp_path / "link"
    root_link.symlink_to(workspace)
    assert adapters.snapshot_text(root_link)[0] == {}


def test_text_snapshot_counts_empty_files_and_directories_toward_enumeration_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(adapters, "MAX_COLLECTION_ENTRIES", 4)
    for index in range(5):
        (tmp_path / f"empty-{index}").touch()
    files, complete, skipped = adapters.snapshot_text(tmp_path)
    assert len(files) == 4 and not complete
    assert {"reason": "enumeration_limit"} in skipped
    inventory, complete = adapters.snapshot_inventory(tmp_path)
    assert len(inventory) == 4 and not complete


def test_text_snapshot_has_time_and_total_byte_bounds(tmp_path, monkeypatch):
    (tmp_path / "a").write_text("1234")
    (tmp_path / "b").write_text("5678")
    monkeypatch.setattr(adapters, "MAX_TOTAL_BYTES", 5)
    files, complete, _ = adapters.snapshot_text(tmp_path)
    assert len(files) == 1 and not complete
    monkeypatch.setattr(adapters, "MAX_COLLECTION_SECONDS", 0)
    files, complete, skipped = adapters.snapshot_text(tmp_path)
    assert files == {} and not complete and skipped == [{"reason": "enumeration_limit"}]


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_process_output_is_bounded_and_never_claimed_complete(monkeypatch, stream):
    import sys
    monkeypatch.setattr(adapters, "MAX_PROCESS_OUTPUT_BYTES", 1024)
    code = f"import sys; sys.{stream}.write('x'*1000000); sys.{stream}.flush()"
    observed = asyncio.run(adapters._process([sys.executable, "-c", code], timeout=5))
    assert observed["output_limit_exceeded"] and not observed["output_complete"]
    assert not observed["timed_out"]
    assert len(observed["stdout"].encode()) + len(observed["stderr"].encode()) <= 1024


def test_process_timeout_kills_its_group_and_keeps_partial_output():
    import sys
    code = "import time; print('fixture-started',flush=True); time.sleep(10)"
    # Allow the Python child to start under VM/video host load before testing its timeout.
    observed = asyncio.run(adapters._process([sys.executable, "-c", code], timeout=1.0))
    assert observed["timed_out"] and not observed["output_complete"]
    assert "fixture-started" in observed["stdout"]


def test_missing_docker_is_real_unrun_infrastructure_error(task, tmp_path, monkeypatch):
    monkeypatch.setattr(adapters.shutil, "which", lambda _: None)
    result = asyncio.run(adapters.run_task(task, "hermes", tmp_path / "evidence"))
    assert result.provenance == "real" and result.status == "infrastructure_error"
    assert result.error == "docker_cli_unavailable"
    assert result.files_complete is False
    result.verify()


def test_public_payload_is_allowlisted_and_archives_redacted_native(task, tmp_path, monkeypatch):
    monkeypatch.setenv("KMB_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(adapters.shutil, "which", lambda _: "/fake/docker")
    calls = []

    async def fake_process(command, **kwargs):
        calls.append((command, kwargs))
        if "inspect" in command:
            return {"returncode": 0, "stdout": json.dumps("sha256:" + "a" * 64),
                    "stderr": "", "timed_out": False}
        mount = command[command.index("--mount") + 1]
        trial = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
        (trial / "workspace" / "result.txt").write_text("A")
        (trial / "workspace" / "trial-secret.txt").write_text("credential name fixture")
        (trial / "subject").mkdir()
        (trial / "subject" / "trial-secret.md").write_text("native credential name fixture")
        data = {"worker_protocol": 1, "status": "completed", "agent_version": "pinned",
                "sessions": [{"session_id": "random-native", "native": {"stdout": "trial-secret"},
                    "parsed": [{"type": "tool_result", "tool_name": "write_file", "success": True}]}]}
        return {"returncode": 0, "stdout": json.dumps(data), "stderr": "", "timed_out": False}

    monkeypatch.setattr(adapters, "_process", fake_process)
    config = {"base_url": "http://host.docker.internal:1234/v1", "model": "m",
              "api_key": "trial-secret", "budget_enforced": True}
    result = asyncio.run(adapters.run_task(task, "hermes", tmp_path / "results", config))
    command, args = calls[-1]
    payload_text = json.dumps(args["payload"])
    assert "private-case-id" not in payload_text
    assert "private-family" not in payload_text and "holdout" not in payload_text
    assert "trial-secret" not in " ".join(command)
    assert args["payload"]["public_input"] == task.public_input()
    assert result.files_after["result.txt"] == "A"
    assert result.files_complete is False
    assert result.environment["action_budget"] == "trusted_gateway"
    assert result.environment["actions_complete"] == "false"
    assert not any(event.kind == "tool_result" for event in result.events)
    report = next(event for event in result.events if event.kind == "native_tool_report")
    assert report.data["execution_verified"] is False
    from kmb.models import Criterion
    from kmb.scorers import _rule
    criterion = Criterion(id="write", kind="action_contains", description="actual tool execution",
                          expected={"tool_name": "write_file", "success": True})
    assert _rule(result, criterion).verdict == "undetermined"
    assert "trial-secret" not in result.model_dump_json()
    for output in (tmp_path / "results").rglob("*"):
        assert "trial-secret" not in str(output)
        if output.is_file():
            assert "trial-secret" not in output.read_text()
    assert not list((tmp_path / "runtime").glob("subject-*"))
    result.verify()


def test_timeout_without_stop_confirmation_retains_state_and_no_snapshot(task, tmp_path, monkeypatch):
    monkeypatch.setenv("KMB_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(adapters.shutil, "which", lambda _: "/fake/docker")

    async def fake(command, **kwargs):
        if "inspect" in command:
            return {"returncode": 0, "stdout": json.dumps("sha256:" + "a" * 64),
                    "stderr": "", "timed_out": False}
        return {"returncode": 1, "stdout": "", "stderr": "", "timed_out": "run" in command}

    monkeypatch.setattr(adapters, "_process", fake)
    result = asyncio.run(adapters.run_task(task, "hermes", tmp_path / "output"))
    assert result.status == "execution_unknown"
    assert not result.files_complete and result.files_after == {}
    assert len(list((tmp_path / "runtime").glob("subject-*"))) == 1


def test_output_overflow_stops_subject_and_records_infrastructure_failure(task, tmp_path, monkeypatch):
    monkeypatch.setenv("KMB_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(adapters.shutil, "which", lambda _: "/fake/docker")
    calls = []

    async def fake(command, **kwargs):
        calls.append(command)
        if "inspect" in command:
            return {"returncode": 0, "stdout": json.dumps("sha256:" + "a" * 64),
                    "stderr": "", "timed_out": False}
        return {"returncode": 0, "stdout": "incomplete-native-prefix" if "run" in command else "",
                "stderr": "", "timed_out": False, "output_limit_exceeded": "run" in command}

    monkeypatch.setattr(adapters, "_process", fake)
    evidence = asyncio.run(adapters.run_task(task, "hermes", tmp_path / "evidence"))
    assert evidence.status == "infrastructure_error"
    assert evidence.error == "native_output_limit_exceeded"
    assert any("rm" in command for command in calls)
    result = next(event.data for event in evidence.events if event.kind == "adapter_result")
    assert result["native_output_complete"] is False


def test_worker_requires_gateway_budget_before_any_model_call(worker, task, tmp_path, monkeypatch):
    import io
    monkeypatch.setattr(worker, "configured", lambda *args: {})
    monkeypatch.setattr(worker, "probe", lambda *args: {"cli_available": True,
        "required_flags": {"--query-file": True}, "agent_version": "test"})
    monkeypatch.setattr(worker.sys, "argv", ["worker", "hermes"])
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO(json.dumps({
        "mode": "benchmark", "public_input": task.public_input(), "gateway_budget_enforced": False})))
    for name, value in {"KMB_AGENT_MODEL": "m", "KMB_AGENT_BASE_URL": "http://gateway/v1",
                        "KMB_AGENT_API_KEY": "trial-token", "KMB_AGENT_TRANSPORT": "chat_completions"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(worker, "native", lambda *a, **k: pytest.fail("model call must not run"))
    assert worker.main()["error"] == "tool_action_budget_unverified"


def test_openclaw_uses_current_memory_schema_and_isolated_paths(worker, tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "SUBJECT", tmp_path / "subject")
    monkeypatch.setattr(worker, "WORKSPACE", tmp_path / "workspace")
    configured = worker.configured("openclaw", "m", "http://gateway/v1", "trial-token")
    config = json.loads(Path(configured["OPENCLAW_CONFIG_PATH"]).read_text())
    assert config["memory"]["search"] == {"enabled": False}
    assert "memorySearch" not in config["agents"]["defaults"]
    assert config["agents"]["defaults"]["workspace"] == str(tmp_path / "workspace")


def test_worker_sessions_use_only_current_prompt_and_no_resume(worker, task, tmp_path, monkeypatch):
    import io
    monkeypatch.setattr(worker, "configured", lambda *args: {})
    monkeypatch.setattr(worker, "export_databases", list)
    monkeypatch.setattr(worker, "probe", lambda *args: {"cli_available": True,
        "required_flags": {"--query-file": True}, "agent_version": "test"})
    monkeypatch.setattr(worker.sys, "argv", ["worker", "hermes"])
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO(json.dumps({
        "mode": "benchmark", "public_input": task.public_input(), "gateway_budget_enforced": True})))
    for name, value in {"KMB_AGENT_MODEL": "m", "KMB_AGENT_BASE_URL": "http://gateway/v1",
                        "KMB_AGENT_API_KEY": "trial-token", "KMB_AGENT_TRANSPORT": "chat_completions"}.items():
        monkeypatch.setenv(name, value)
    observed = []

    def fake(command, env, *, timeout):
        prompt = Path(command[command.index("--query-file") + 1]).read_text()
        observed.append((command, prompt))
        settle = {"type": "kmb_settle", "protocol": 1, "native_cleanup": "returned",
                  "title_threads": {"status": "drained", "remaining": 0},
                  "memory_queue": {"status": "not_present"}}
        return {"returncode": 0, "stdout": json.dumps({"type": "result",
                "session_id": f"native-{len(observed)}", "exit_code": 0}) + "\n" + json.dumps(settle),
                "stderr": "", "timed_out": False}

    monkeypatch.setattr(worker, "native", fake)
    result = worker.main()
    assert result["status"] == "completed"
    assert [prompt for _, prompt in observed] == [s.prompt for s in task.sessions]
    assert all("--resume" not in command and "--continue" not in command for command, _ in observed)
    assert all("--hermes-cli" in command for command, _ in observed)
    assert result["sessions"][0]["session_id"] != result["sessions"][1]["session_id"]


def test_hermes_waits_title_before_native_cleanup(worker, capsys):
    import threading
    import time
    from types import SimpleNamespace

    released = threading.Event()
    title_done = threading.Event()
    thread = threading.Thread(target=lambda: (released.wait(1), title_done.set()), daemon=True)
    thread.start()
    calls = []

    def wait_for_title_upgrades(*, timeout):
        calls.append("wait")
        assert 0 < timeout <= 2
        released.set()
        thread.join(timeout)

    manager = SimpleNamespace(shutdown_drain_state={"status": "drained", "active_tasks": 0,
        "abandoned_writes": 0, "abandoned_prefetches": 0, "api_key": "must-not-emit"})
    cli = SimpleNamespace(agent=SimpleNamespace(_memory_manager=manager))

    def finalize(_):
        assert title_done.is_set(), "auxiliary clients must remain open until title finishes"
        calls.append("cleanup")

    worker.hermes_finalize(finalize, cli, SimpleNamespace(_UPGRADE_THREADS=[thread],
        wait_for_title_upgrades=wait_for_title_upgrades), time.monotonic() + 2)
    event = json.loads(capsys.readouterr().out)
    assert calls == ["wait", "cleanup"]
    assert event["title_threads"] == {"status": "drained", "observed": 1, "remaining": 0}
    assert event["native_cleanup"] == "returned"
    assert "api_key" not in event["memory_queue"]
    assert event["background_consolidation"] == "unverified"


def test_hermes_settle_pending_or_missing_is_not_success(worker):
    result = {"type": "result", "session_id": "s1", "exit_code": 0}
    settle = {"type": "kmb_settle", "protocol": 1, "native_cleanup": "returned",
              "title_threads": {"status": "pending", "remaining": 1},
              "memory_queue": {"status": "not_present"}}
    for records in ([result], [result, settle]):
        parsed = worker.extract("hermes", {"returncode": 0,
            "stdout": "\n".join(json.dumps(record) for record in records)}, None)
        assert parsed["terminal_success"] is True
        assert parsed["settle_success"] is False


def test_hermes_drain_timeout_and_cleanup_failure_remain_visible(worker, capsys):
    import time
    from types import SimpleNamespace

    calls = []
    titles = SimpleNamespace(_UPGRADE_THREADS=[SimpleNamespace(is_alive=lambda: True)],
        wait_for_title_upgrades=lambda **kw: calls.append(kw["timeout"]))
    cli = SimpleNamespace(agent=None)

    def failed_cleanup(_):
        raise RuntimeError("secret-error-message")

    with pytest.raises(RuntimeError):
        worker.hermes_finalize(failed_cleanup, cli, titles, time.monotonic() - 1)
    event = json.loads(capsys.readouterr().out)
    assert calls == [0.0]
    assert event["title_threads"]["status"] == "pending"
    assert event["native_cleanup"] == "not_returned"
    assert event["error_type"] == "RuntimeError"
    assert "secret-error-message" not in json.dumps(event)


@pytest.mark.parametrize("state", [{"status": "timed_out", "active_tasks": 1},
    {"status": "drained", "abandoned_writes": 1}, {"status": "unobserved"}])
def test_hermes_memory_queue_failure_not_hidden_by_successful_native_result(worker, state):
    records = [{"type": "result", "session_id": "s1", "exit_code": 0},
        {"type": "kmb_settle", "protocol": 1, "native_cleanup": "returned",
         "title_threads": {"status": "drained"}, "memory_queue": state}]
    parsed = worker.extract("hermes", {"returncode": 0,
        "stdout": "\n".join(json.dumps(record) for record in records)}, None)
    assert parsed["terminal_success"] is True
    assert parsed["settle_success"] is False


def test_doctor_configuration_presence_only(monkeypatch):
    from kmb import environment
    monkeypatch.setattr(environment.shutil, "which", lambda _: None)
    monkeypatch.setenv("KMB_AGENT_API_KEY", "secret-must-not-print")
    report = environment.doctor()
    assert report["credential_configured"] is True
    assert "secret-must-not-print" not in json.dumps(report)


@pytest.mark.parametrize("terminal_code,terminal_error", [(1, None), (0, "upstream_error")])
def test_hermes_failed_result_with_zero_process_exit_is_not_success(worker, task, monkeypatch,
                                                                  terminal_code, terminal_error):
    import io
    monkeypatch.setattr(worker, "configured", lambda *args: {})
    monkeypatch.setattr(worker, "export_databases", list)
    monkeypatch.setattr(worker, "probe", lambda *args: {"cli_available": True,
        "required_flags": {"--query-file": True}, "agent_version": "test"})
    monkeypatch.setattr(worker.sys, "argv", ["worker", "hermes"])
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO(json.dumps({
        "mode": "benchmark", "public_input": task.public_input(), "gateway_budget_enforced": True})))
    for name, value in {"KMB_AGENT_MODEL": "m", "KMB_AGENT_BASE_URL": "http://gateway/v1",
                        "KMB_AGENT_API_KEY": "trial-token", "KMB_AGENT_TRANSPORT": "chat_completions"}.items():
        monkeypatch.setenv(name, value)

    def fake(*args, **kwargs):
        return {"returncode": 0, "stdout": json.dumps({"type": "result", "session_id": "native-first",
                "exit_code": terminal_code, "error": terminal_error}), "stderr": "", "timed_out": False}

    monkeypatch.setattr(worker, "native", fake)
    result = worker.main()
    assert result["status"] == "infrastructure_error"
    assert result["error"] == "native_terminal_not_successful"
    assert len(result["sessions"]) == 1


def test_context_is_explicit(monkeypatch):
    monkeypatch.setenv("KMB_DOCKER_CONTEXT", "colima-kmb")
    assert docker_prefix() == ["docker", "--context", "colima-kmb"]


@pytest.fixture
def openclaw_native_envelope():
    # Reduced shape of the pinned CLI's real JSON envelope, not an agent invocation.
    return {"payloads": [{"text": "synthetic completion", "mediaUrl": None}], "meta": {
        "agentMeta": {"sessionId": "native-session"}, "aborted": False,
        "replayInvalid": True, "livenessState": "working", "stopReason": "stop",
        "completion": {"stopReason": "stop", "finishReason": "stop"}}}


def test_openclaw_real_success_shape_does_not_misread_replay_or_liveness(worker, openclaw_native_envelope):
    result = worker.extract("openclaw", {"returncode": 0, "timed_out": False,
        "stdout": json.dumps(openclaw_native_envelope)}, "requested-session")
    assert result["terminal_status"] == "completed"
    assert result["terminal_success"] is True
    assert result["session_id"] == "native-session"
    assert result["requested_session_id"] == "requested-session"


@pytest.mark.parametrize("location,value", [
    ("aborted", True), ("stopReason", "error"), ("stopReason", "aborted"),
    ("finishReason", "failed"), ("error", {"message": "upstream unavailable"}),
    ("payload_error", True),
])
def test_openclaw_native_failure_cannot_hide_behind_zero_exit(
    worker, openclaw_native_envelope, location, value,
):
    if location == "payload_error":
        openclaw_native_envelope["payloads"][0]["isError"] = value
    elif location == "finishReason":
        openclaw_native_envelope["meta"]["completion"][location] = value
    else:
        openclaw_native_envelope["meta"][location] = value
    result = worker.extract("openclaw", {"returncode": 0, "timed_out": False,
        "stdout": json.dumps(openclaw_native_envelope)}, "requested-session")
    assert result["terminal_status"] == "infrastructure_error"
    assert result["terminal_success"] is False


@pytest.mark.parametrize("reason", ["length", "max_tokens", "max_output_tokens", "budget_exhausted"])
def test_openclaw_native_generation_limit_is_budget_exhausted(
    worker, openclaw_native_envelope, reason,
):
    openclaw_native_envelope["meta"]["completion"]["finishReason"] = reason
    result = worker.extract("openclaw", {"returncode": 0, "timed_out": False,
        "stdout": json.dumps(openclaw_native_envelope)}, "requested-session")
    assert result["terminal_status"] == "budget_exhausted"
    assert result["terminal_success"] is False


@pytest.mark.parametrize("reason", ["tool_calls", "unrecognized", [], ""])
def test_openclaw_unrecognized_terminal_reason_is_not_success(
    worker, openclaw_native_envelope, reason,
):
    openclaw_native_envelope["meta"]["completion"]["finishReason"] = reason
    result = worker.extract("openclaw", {"returncode": 0, "timed_out": False,
        "stdout": json.dumps(openclaw_native_envelope)}, "requested-session")
    assert result["terminal_status"] == "execution_unknown"
    assert result["terminal_success"] is None


def test_openclaw_session_id_and_rc_zero_alone_do_not_prove_completion(worker):
    result = worker.extract("openclaw", {"returncode": 0, "timed_out": False,
        "stdout": json.dumps({"sessionId": "native-session", "payloads": []})}, "requested-session")
    assert result["terminal_status"] == "execution_unknown"
    assert result["terminal_success"] is None


@pytest.mark.parametrize("native_json", ["[]", "null", '"text"'])
def test_openclaw_invalid_envelope_is_explicitly_unobserved(worker, native_json):
    result = worker.extract("openclaw", {"returncode": 0, "timed_out": False,
        "stdout": native_json}, "requested-session")
    assert result["terminal_observed"] is False


@pytest.mark.parametrize("stop_reason,status", [
    ("aborted", "infrastructure_error"), ("length", "budget_exhausted"),
    ("tool_calls", "execution_unknown"),
])
def test_openclaw_noncompletion_stops_remaining_sessions(
    worker, task, monkeypatch, openclaw_native_envelope, stop_reason, status,
):
    import io

    monkeypatch.setattr(worker, "configured", lambda *args: {})
    monkeypatch.setattr(worker, "export_databases", list)
    monkeypatch.setattr(worker, "probe", lambda *args: {"cli_available": True,
        "required_flags": {"--message": True}, "agent_version": "test"})
    monkeypatch.setattr(worker.sys, "argv", ["worker", "openclaw"])
    monkeypatch.setattr(worker.sys, "stdin", io.StringIO(json.dumps({
        "mode": "benchmark", "public_input": task.public_input(), "gateway_budget_enforced": True})))
    for name, value in {"KMB_AGENT_MODEL": "m", "KMB_AGENT_BASE_URL": "http://gateway/v1",
                        "KMB_AGENT_API_KEY": "trial-token", "KMB_AGENT_TRANSPORT": "chat_completions"}.items():
        monkeypatch.setenv(name, value)
    openclaw_native_envelope["meta"]["stopReason"] = stop_reason
    calls = []

    def fake(*args, **kwargs):
        calls.append(args)
        return {"returncode": 0, "stdout": json.dumps(openclaw_native_envelope),
                "stderr": "", "timed_out": False}

    monkeypatch.setattr(worker, "native", fake)
    result = worker.main()
    assert result["status"] == status
    assert len(result["sessions"]) == len(calls) == 1
