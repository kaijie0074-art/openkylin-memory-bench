"""Real Inspect path with a mocked native agent and mock upstream; no real model."""
import asyncio
import json
import socket

import httpx
import pytest
from inspect_ai.log import read_eval_log

from kmb.inspect_bridge import _run_real_task, run_inspect
from kmb.models import (
    Budget,
    Criterion,
    EvidenceBundle,
    EvidenceEvent,
    ModelIdentity,
    PrivateRubric,
    SessionSpec,
    TaskSpec,
    Usage,
)
from kmb.provider import JudgeProvider
from kmb.storage import load_evidence_tree


@pytest.fixture(autouse=True)
def local_http_server_name(monkeypatch):
    # These real gateway integration tests bind HTTPServer locally. Mac host-name
    # reverse DNS can take 30 seconds before serving any request. Isolate only
    # that server name; socket binding, trial deadlines, and drain stay real.
    original = socket.getfqdn
    monkeypatch.setattr(socket, "getfqdn", lambda name="":
                        "kmb-test.local" if name == "0.0.0.0" else original(name))


def task_and_rubric():
    task = TaskSpec(id="bridge-test", ability="update", family="bridge-test", split="dev",
                    sessions=[SessionSpec(id="s1", prompt="Remember old"),
                              SessionSpec(id="s2", prompt="Use new", final=True)],
                    budget=Budget(timeout_seconds=20, max_tool_actions=1))
    rubric = PrivateRubric(task_id=task.id, criteria=[Criterion(
        id="result", kind="text_equals", path="result.txt", expected="new", description="result")])
    return task, rubric


def bundle(task, agent, status="completed"):
    return EvidenceBundle(run_id="native-fixture", task_id=task.id, ability=task.ability,
                          family=task.family, split=task.split, agent=agent, provenance="real",
                          status=status, model=ModelIdentity(requested="native-observation"),
                          usage=Usage(input_tokens=999, output_tokens=999),
                          events=[EvidenceEvent(id="e1", kind="native_session",
                                               data={"source": "test-only native adapter substitute"})],
                          files_after={"result.txt": "new"}).freeze()


def provider(upstream):
    return JudgeProvider("https://mock-upstream.test/private/v1", "private-upstream-key", "fixed-model",
                         transport=httpx.MockTransport(upstream))


def completion(tool_count=0):
    message = {"role": "assistant", "content": "new"}
    if tool_count:
        message["tool_calls"] = [{"id": f"c{index}", "type": "function",
                                  "function": {"name": "write", "arguments": "{}"}}
                                 for index in range(tool_count)]
    return {"id": "test", "model": "fixed-returned", "choices": [{"index": 0,
            "message": message, "finish_reason": "tool_calls" if tool_count else "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2}}


async def call_local(config):
    url = config["base_url"].replace("host.docker.internal", "127.0.0.1")
    async with httpx.AsyncClient(trust_env=False) as client:
        return await client.post(url + "/chat/completions", headers={
            "Authorization": "Bearer " + config["api_key"]}, json={
                "model": config["model"], "messages": [{"role": "user", "content": "test input"}]})


def test_inspect_real_route_merges_gateway_evidence_without_secret_logs(monkeypatch, tmp_path):
    task, rubric = task_and_rubric()
    p = provider(lambda request: httpx.Response(200, json=completion()))
    monkeypatch.setattr(JudgeProvider, "from_env", lambda: p)
    seen_tokens = []

    async def fake_native(task, agent, output_dir, model_config=None):
        assert model_config["budget_enforced"] is True
        assert model_config["api_key"] != "private-upstream-key"
        seen_tokens.append(model_config["api_key"])
        assert (await call_local(model_config)).status_code == 200
        return bundle(task, agent)

    monkeypatch.setattr("kmb.adapters.run_task", fake_native)
    run_inspect([(task, rubric)], tmp_path / "unused-dataset", tmp_path / "run", agent="openclaw")
    items = load_evidence_tree(tmp_path / "run" / "evidence")
    assert len(items) == 1
    observed = items[0]
    assert observed.status == "completed" and observed.provenance == "real"
    assert observed.model.requested == "fixed-model" and observed.model.returned == "fixed-returned"
    assert observed.usage == Usage(input_tokens=5, output_tokens=2)  # not 1004 / 1001
    assert {event.kind for event in observed.events} >= {
        "native_session", "model_request", "model_response", "model_gateway_summary"}
    assert len({event.id for event in observed.events}) == len(observed.events)
    assert observed.environment["tool_budget_scope"] == "model_proposed_tool_calls"
    observed.verify()
    logs = list((tmp_path / "run" / "inspect").glob("*.eval"))
    assert logs
    for logfile in logs:
        decoded = read_eval_log(str(logfile)).model_dump_json()
        assert "private-upstream-key" not in decoded and seen_tokens[0] not in decoded
    for artifact in (tmp_path / "run").rglob("*"):
        if artifact.is_file():
            raw = artifact.read_bytes()
            assert b"private-upstream-key" not in raw
            assert seen_tokens[0].encode() not in raw
            # The safe origin is an execution condition; endpoint paths stay private.
            assert b"/private/v1" not in raw


@pytest.mark.parametrize("native_status,expected", [("task_failed", "budget_exhausted"),
                                                    ("execution_unknown", "execution_unknown")])
def test_gateway_budget_overrides_task_failure_but_not_unconfirmed_execution(
    monkeypatch, tmp_path, native_status, expected,
):
    task, _ = task_and_rubric()
    p = provider(lambda request: httpx.Response(200, json=completion(tool_count=2)))

    async def fake_native(task, agent, output_dir, model_config=None):
        assert (await call_local(model_config)).status_code == 429
        return bundle(task, agent, native_status)

    monkeypatch.setattr("kmb.adapters.run_task", fake_native)
    observed = asyncio.run(_run_real_task(task, "hermes", tmp_path, p))
    assert observed.status == expected
    assert observed.events[-1].data["tool_budget_exhausted"] is True
    assert observed.usage.input_tokens == 5
    observed.verify()


@pytest.mark.parametrize("native_status,expected", [("task_failed", "infrastructure_error"),
                                                    ("completed", "infrastructure_error"),
                                                    ("execution_unknown", "execution_unknown")])
def test_gateway_failure_is_infrastructure_not_agent_failure(monkeypatch, tmp_path,
                                                            native_status, expected):
    task, _ = task_and_rubric()
    p = provider(lambda request: httpx.Response(503, text="private-upstream-key"))

    async def fake_native(task, agent, output_dir, model_config=None):
        assert (await call_local(model_config)).status_code == 502
        return bundle(task, agent, native_status)

    monkeypatch.setattr("kmb.adapters.run_task", fake_native)
    observed = asyncio.run(_run_real_task(task, "hermes", tmp_path, p))
    assert observed.status == expected
    if expected == "infrastructure_error":
        assert observed.error == "model_gateway_upstream_error"
    assert observed.usage.input_tokens is None


@pytest.mark.parametrize("statuses,expected", [([200, 503], "infrastructure_error"),
                                              ([503, 200], "completed")])
def test_only_successful_response_after_gateway_error_establishes_recovery(
    monkeypatch, tmp_path, statuses, expected,
):
    task, _ = task_and_rubric()
    calls = []

    def upstream(request):
        status = statuses[len(calls)]
        calls.append(request)
        return httpx.Response(status, json=completion() if status == 200 else {"error": "unavailable"})

    async def fake_native(task, agent, output_dir, model_config=None):
        for status in statuses:
            observed = await call_local(model_config)
            assert observed.status_code == (200 if status == 200 else 502)
        return bundle(task, agent, "completed")

    monkeypatch.setattr("kmb.adapters.run_task", fake_native)
    observed = asyncio.run(_run_real_task(task, "hermes", tmp_path, provider(upstream)))
    assert observed.status == expected
    assert observed.events[-1].data["upstream_error_count"] == 1
    observed.verify()


@pytest.mark.parametrize("native_status", ["budget_exhausted", "task_failed", "execution_unknown"])
def test_recovered_upstream_error_does_not_overwrite_later_native_terminal(
    monkeypatch, tmp_path, native_status,
):
    task, _ = task_and_rubric()
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(503, json={"error": "unavailable"}) if len(calls) == 1 else (
            httpx.Response(200, json=completion()))

    async def fake_native(task, agent, output_dir, model_config=None):
        assert (await call_local(model_config)).status_code == 502
        assert (await call_local(model_config)).status_code == 200
        result = bundle(task, agent, native_status).model_copy(update={
            "error": "native_generation_budget_exhausted" if native_status == "budget_exhausted"
                     else "native_terminal_reason"})
        return result.freeze()

    monkeypatch.setattr("kmb.adapters.run_task", fake_native)
    observed = asyncio.run(_run_real_task(task, "hermes", tmp_path, provider(upstream)))
    assert observed.status == native_status
    assert observed.error == ("native_generation_budget_exhausted" if native_status == "budget_exhausted"
                              else "native_terminal_reason")
    assert observed.events[-1].data["upstream_error_count"] == 1
    assert observed.usage.input_tokens is None  # A recovered failed attempt is still potentially billed.
    observed.verify()


def test_completed_native_run_without_model_call_is_not_verified(monkeypatch, tmp_path):
    task, _ = task_and_rubric()
    p = provider(lambda request: pytest.fail("unexpected upstream request"))

    async def fake_native(task, agent, output_dir, model_config=None):
        return bundle(task, agent)

    monkeypatch.setattr("kmb.adapters.run_task", fake_native)
    observed = asyncio.run(_run_real_task(task, "hermes", tmp_path, p))
    assert observed.status == "infrastructure_error"
    assert observed.error == "no_gateway_model_calls"


def test_simulation_never_loads_proxy_configuration(monkeypatch, tmp_path):
    task, rubric = task_and_rubric()
    simulation = tmp_path / "dataset" / "simulation"
    simulation.mkdir(parents=True)
    (simulation / f"{task.id}.json").write_text(json.dumps({"provenance": "simulated",
        "task_id": task.id, "files_after": {"result.txt": "new"}, "final_response": "synthetic"}))
    monkeypatch.setattr(JudgeProvider, "from_env", lambda: pytest.fail("simulation read provider config"))
    run_inspect([(task, rubric)], simulation.parent, tmp_path / "run")
    observed = load_evidence_tree(tmp_path / "run" / "evidence")[0]
    assert observed.provenance == "simulated"
    assert all(event.kind != "model_request" for event in observed.events)


def test_unsettled_gateway_cannot_report_completed_even_with_prior_success(monkeypatch, tmp_path):
    task, _ = task_and_rubric()
    p = provider(lambda request: httpx.Response(200, json=completion()))

    async def fake_native(task, agent, output_dir, model_config=None):
        assert (await call_local(model_config)).status_code == 200
        return bundle(task, agent)

    monkeypatch.setattr('kmb.adapters.run_task', fake_native)
    monkeypatch.setattr('kmb.gateway.TrialGateway.drain', lambda self: False)
    observed = asyncio.run(_run_real_task(task, 'hermes', tmp_path, p))
    assert observed.status == 'execution_unknown'
    assert observed.error == 'model_gateway_drain_unconfirmed'
    assert observed.events[-1].data['accepted_requests_settled'] is False
