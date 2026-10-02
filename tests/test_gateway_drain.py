import asyncio
import socket
import threading
import time

import httpx
import pytest

from kmb.gateway import GatewayError, TrialGateway
from kmb.provider import JudgeProvider


@pytest.fixture(autouse=True)
def local_http_server_name(monkeypatch):
    # Mac host-name reverse DNS can stall HTTPServer binding for 30 seconds.
    # Only isolate the server's display name; drain/deadline behavior stays real.
    original = socket.getfqdn
    monkeypatch.setattr(socket, "getfqdn", lambda name="":
                        "kmb-test.local" if name == "0.0.0.0" else original(name))


def test_drain_preserves_admitted_auxiliary_receipt_and_rejects_new_calls():
    started = threading.Event()
    finished = threading.Event()

    async def upstream(request):
        started.set()
        await asyncio.sleep(0.08)
        return httpx.Response(200, json={"model": "fixed", "choices": [
            {"message": {"role": "assistant", "content": "title"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2}})

    provider = JudgeProvider("https://example.test/v1", "test", "fixed",
                             transport=httpx.MockTransport(upstream))
    with TrialGateway(provider, 20, 10) as gateway:
        body = {"model": "fixed", "messages": [{"role": "user", "content": "title"}]}

        def send():
            gateway._forward(body)
            finished.set()

        thread = threading.Thread(target=send)
        thread.start()
        assert started.wait(2)
        assert gateway.drain()
        thread.join(2)
        assert finished.is_set()
        assert gateway.usage().input_tokens == 5
        with pytest.raises(GatewayError, match="trial_draining"):
            gateway._forward(body)
    responses = [e for e in gateway.evidence_events() if e.kind == "model_response"]
    assert len(responses) == 1 and responses[0].data["forwarded"] is True


def test_drain_does_not_extend_expired_wall_budget():
    provider = JudgeProvider("https://example.test/v1", "test", "fixed")
    with TrialGateway(provider, 20, 1) as gateway:
        gateway._deadline = time.monotonic() - 1
        gateway._call_lock.acquire()
        start = time.monotonic()
        try:
            assert gateway.drain() is False
            assert time.monotonic() - start < 0.1
        finally:
            gateway._call_lock.release()
