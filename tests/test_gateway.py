"""Local HTTP requests and a mock upstream only; no paid model requests."""
import asyncio
import json
import socket
import time

import httpx
import pytest

from kmb.gateway import MAX_REQUEST_BYTES, TrialGateway
from kmb.provider import JudgeProvider


@pytest.fixture(autouse=True)
def local_http_server_name(monkeypatch):
    # HTTPServer reverse-resolves 0.0.0.0 via the host name. On this Mac that
    # lookup can take 30 seconds and exhaust the trial before the first request.
    # Isolate server naming only; real socket binding and gateway clocks stay intact.
    original = socket.getfqdn
    monkeypatch.setattr(socket, "getfqdn", lambda name="":
                        "kmb-test.local" if name == "0.0.0.0" else original(name))


def completion(*, calls=0, usage=None, content="done"):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"id": f"call-{index}", "type": "function",
             "function": {"name": "write_file", "arguments": '{"path":"answer.txt"}'}}
            for index in range(calls)]
    value = {"id": "test-response", "object": "chat.completion", "created": 123,
             "model": "fixed-returned", "choices": [{"index": 0, "message": message,
             "finish_reason": "tool_calls" if calls else "stop"}]}
    if usage is not None:
        value["usage"] = usage
    return value


def provider(handler):
    return JudgeProvider("https://upstream.example/private/v1", "upstream-secret", "fixed-model",
                         transport=httpx.MockTransport(handler))


def local(gateway):
    config = gateway.container_config()
    return config["base_url"].replace("host.docker.internal", "127.0.0.1"), {
        "Authorization": "Bearer " + config["api_key"]}


def request_body(**kwargs):
    return {"model": "fixed-model", "messages": [{"role": "user", "content": "do task"}], **kwargs}


def test_trial_auth_and_only_fixed_model_and_routes():
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json=completion())

    with TrialGateway(provider(upstream), 20, 30) as gateway:
        url, headers = local(gateway)
        assert httpx.get(url + "/models", trust_env=False).status_code == 401
        assert httpx.get(url + "/models", headers={"Authorization": "Bearer wrong"},
                         trust_env=False).status_code == 401
        models = httpx.get(url + "/models", headers=headers, trust_env=False)
        assert [item["id"] for item in models.json()["data"]] == ["fixed-model"]
        assert httpx.get(url + "/other", headers=headers, trust_env=False).status_code == 404
        assert httpx.post(url + "/responses", headers=headers, json={}, trust_env=False).status_code == 404
        bad = httpx.post(url + "/chat/completions", headers=headers,
                         json=request_body(model="other"), trust_env=False)
        assert bad.status_code == 400 and bad.json()["error"]["code"] == "model_not_allowed"
        assert calls == []


def test_exact_upstream_credential_echo_is_rejected_instead_of_rewritten():
    secret_echo = {}

    def upstream(request):
        assert request.headers["authorization"] == "Bearer upstream-secret"
        assert str(request.url) == "https://upstream.example/private/v1/chat/completions"
        body = json.loads(request.content)
        assert body["stream"] is False
        content = " ".join(["upstream-secret", secret_echo["trial"],
                            "https://upstream.example/private/v1", "https://public.test/path?q=private"])
        result = completion(content=content)
        result["authorization"] = "secret field"
        return httpx.Response(200, json=result)

    with TrialGateway(provider(upstream), 20, 30) as gateway:
        config = gateway.container_config()
        secret_echo["trial"] = config["api_key"]
        assert config["api_key"] != "upstream-secret" and config["budget_enforced"] is True
        url, headers = local(gateway)
        response = httpx.post(url + "/chat/completions", headers=headers, trust_env=False,
                              json=request_body(messages=[{"role": "user", "content":
                                  "upstream-secret https://upstream.example/private/v1"}]))
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "upstream_credential_in_response"
        serialized = json.dumps([event.model_dump() for event in gateway.evidence_events()])
        serialized += response.text + gateway.model_identity().model_dump_json()
        for forbidden in ("upstream-secret", config["api_key"], "/private/v1", "q=private", "secret field"):
            assert forbidden not in serialized
        assert {event.kind for event in gateway.evidence_events()} == {"model_request", "model_response"}
        assert gateway.usage().input_tokens is None


def test_public_urls_and_tool_arguments_reach_agent_unchanged_while_logs_are_redacted():
    public_url = "https://example.org/search?q=openkylin&page=2"
    data = completion(calls=1, usage={"prompt_tokens": 1, "completion_tokens": 1})
    data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = json.dumps({
        "path": "links.txt", "content": public_url})
    with TrialGateway(provider(lambda request: httpx.Response(200, json=data)), 20, 30) as gateway:
        url, headers = local(gateway)
        response = httpx.post(url + "/chat/completions", headers=headers,
                              json=request_body(), trust_env=False)
        assert response.status_code == 200 and response.json() == data
        assert "q=openkylin" not in json.dumps([e.model_dump() for e in gateway.evidence_events()])


@pytest.mark.parametrize("location", ["key", "nested_value"])
def test_upstream_credential_cannot_escape_through_metadata(location):
    data = completion()
    if location == "key":
        data["upstream-secret"] = "metadata"
    else:
        data["metadata"] = {"value": ["prefix upstream-secret suffix"]}
    with TrialGateway(provider(lambda request: httpx.Response(200, json=data)), 20, 30) as gateway:
        url, headers = local(gateway)
        response = httpx.post(url + "/chat/completions", headers=headers,
                              json=request_body(), trust_env=False)
        assert response.status_code == 502
        assert "upstream-secret" not in response.text
        assert "upstream-secret" not in json.dumps([e.model_dump() for e in gateway.evidence_events()])


def test_gateway_identity_records_endpoint_budget_and_only_observed_parameter_sets():
    import hashlib
    p = provider(lambda request: httpx.Response(200, json=completion()))
    with TrialGateway(p, 7, 30) as gateway:
        for values in ({"temperature": 0, "seed": 42}, {"seed": 42, "temperature": 0},
                       {"reasoning_effort": "high", "max_completion_tokens": 300}):
            gateway._forward(request_body(**values))
        identity = gateway.model_identity()
        assert identity.provider == "https://upstream.example"
        params = identity.parameters
        assert params["endpoint_sha256"] == hashlib.sha256(p.base_url.encode()).hexdigest()
        assert params["max_tool_actions"] == 7 and params["timeout_seconds"] == 30
        assert params["upstream_timeout_seconds"] == p._timeout_seconds
        observed = params["requested_inference_parameters"]
        assert len(observed) == 2
        assert {"temperature": 0, "seed": 42} in observed
        assert {"reasoning_effort": "high", "max_completion_tokens": 300} in observed
        assert all("top_p" not in values and "messages" not in values and "tools" not in values
                   for values in observed)


def test_gateway_returned_model_identity_uses_provider_validation():
    data = completion()
    data["model"] = "https://untrusted.example/model?credential=sensitive"
    with TrialGateway(provider(lambda request: httpx.Response(200, json=data)), 20, 30) as gateway:
        gateway._forward(request_body())
        identity = gateway.model_identity()
        assert identity.returned is None
        assert identity.parameters["returned_models"] == []


def test_tool_budget_blocks_entire_response_before_forwarding_and_counts_usage():
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json=completion(calls=2, usage={"prompt_tokens": 7, "completion_tokens": 3}))

    with TrialGateway(provider(upstream), 1, 30) as gateway:
        url, headers = local(gateway)
        response = httpx.post(url + "/chat/completions", headers=headers,
                              json=request_body(stream=True), trust_env=False)
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "tool_budget_exhausted"
        assert "write_file" not in response.text and "data:" not in response.text
        assert gateway.budget_exhausted is True
        again = httpx.post(url + "/chat/completions", headers=headers,
                           json=request_body(), trust_env=False)
        assert again.status_code == 429 and len(calls) == 1
        assert gateway.usage().input_tokens == 7
        assert gateway.model_identity().parameters["proposed_tool_calls"] == 2
        assert gateway.evidence_events()[-1].data["forwarded"] is False


def test_stream_conversion_has_tool_indices_finish_usage_and_done():
    sent = []

    def upstream(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=completion(calls=2, usage={"prompt_tokens": 10, "completion_tokens": 4}))

    with TrialGateway(provider(upstream), 2, 30) as gateway:
        url, headers = local(gateway)
        response = httpx.post(url + "/chat/completions", headers=headers, trust_env=False,
                              json=request_body(stream=True, stream_options={"include_usage": True}))
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        chunks = [line[len("data: "):] for line in response.text.splitlines() if line.startswith("data: ")]
        assert chunks[-1] == "[DONE]"
        decoded = [json.loads(chunk) for chunk in chunks[:-1]]
        delta = decoded[0]["choices"][0]["delta"]
        assert delta["role"] == "assistant" and delta["content"] == "done"
        assert [call["index"] for call in delta["tool_calls"]] == [0, 1]
        assert decoded[1]["choices"][0]["finish_reason"] == "tool_calls"
        assert decoded[2]["choices"] == [] and decoded[2]["usage"]["prompt_tokens"] == 10
        assert sent[0]["stream"] is False and "stream_options" not in sent[0]
        assert gateway.budget_exhausted is False


def test_unknown_usage_contaminates_aggregate_instead_of_becoming_zero():
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json=completion(usage={"prompt_tokens": 2, "completion_tokens": 1}
                                                    if len(calls) == 1 else None))

    with TrialGateway(provider(upstream), 20, 30) as gateway:
        url, headers = local(gateway)
        for _ in range(2):
            response = httpx.post(url + "/chat/completions", headers=headers,
                                  json=request_body(), trust_env=False)
            assert response.status_code == 200
        assert gateway.usage().input_tokens is None and gateway.usage().output_tokens is None


@pytest.mark.parametrize("failure", ["http", "timeout", "invalid"])
def test_upstream_errors_are_sanitized_and_not_retried(failure):
    calls = []

    def upstream(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("upstream-secret https://upstream.example/private/v1")
        if failure == "invalid":
            return httpx.Response(200, text="upstream-secret invalid JSON")
        return httpx.Response(503, text="upstream-secret provider error")

    with TrialGateway(provider(upstream), 20, 30) as gateway:
        url, headers = local(gateway)
        response = httpx.post(url + "/chat/completions", headers=headers,
                              json=request_body(), trust_env=False)
        assert response.status_code in {502, 504}
        assert "upstream-secret" not in response.text
        assert len(calls) == 1 and gateway.usage().input_tokens is None
        assert gateway.evidence_events()[-1].data["forwarded"] is False
        assert gateway.evidence_events()[-1].data["upstream_http_status"] == (503 if failure == "http" else None)


def test_trial_deadline_and_body_size_are_enforced_before_upstream():
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json=completion())

    with TrialGateway(provider(upstream), 20, 30) as gateway:
        url, headers = local(gateway)
        large = httpx.post(url + "/chat/completions", headers=headers,
                           content=b"x" * (MAX_REQUEST_BYTES + 1), trust_env=False)
        assert large.status_code == 413
        gateway._deadline = time.monotonic() - 1
        expired = httpx.post(url + "/chat/completions", headers=headers,
                             json=request_body(), trust_env=False)
        assert expired.status_code == 408 and calls == []


def test_new_trial_has_new_token_and_context_cannot_be_reused():
    p = provider(lambda request: httpx.Response(200, json=completion()))
    with TrialGateway(p, 20, 30) as first:
        first_token = first.container_config()["api_key"]
    with pytest.raises(RuntimeError, match="not running"):
        first.container_config()
    with pytest.raises(RuntimeError, match="cannot be reused"):
        first.__enter__()
    with TrialGateway(p, 20, 30) as second:
        assert second.container_config()["api_key"] != first_token


def test_gateway_rejects_responses_upstream_instead_of_guessing_conversion():
    p = JudgeProvider("https://upstream.example/v1", "key", "model", "responses")
    with pytest.raises(ValueError, match="requires chat_completions"):
        TrialGateway(p, 20, 30)


def test_absolute_upstream_timeout_cancels_without_forwarding():
    cancelled = []

    async def upstream(request):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return httpx.Response(200, json=completion())

    p = provider(upstream)
    p._timeout_seconds = 0.03
    with TrialGateway(p, 20, 30) as gateway:
        url, headers = local(gateway)
        response = httpx.post(url + "/chat/completions", headers=headers,
                              json=request_body(), trust_env=False)
        assert response.status_code == 504
        assert response.json()["error"]["code"] == "upstream_timeout"
        assert cancelled == [True]
        assert gateway.evidence_events()[-1].data["forwarded"] is False


def test_legacy_function_call_cannot_evade_budget():
    def upstream(request):
        data = completion()
        data["choices"][0]["message"]["function_call"] = {"name": "write_file", "arguments": "{}"}
        return httpx.Response(200, json=data)

    with TrialGateway(provider(upstream), 0, 30) as gateway:
        url, headers = local(gateway)
        response = httpx.post(url + "/chat/completions", headers=headers,
                              json=request_body(), trust_env=False)
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "legacy_function_call_unsupported"
        assert gateway.evidence_events()[-1].data["forwarded"] is False
