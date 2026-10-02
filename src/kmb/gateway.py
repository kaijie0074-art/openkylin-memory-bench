"""Per-trial authenticated model gateway; the upstream credential stays on the host."""
from __future__ import annotations

import asyncio
import hmac
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self
from urllib.parse import urlsplit, urlunsplit

import httpx

from kmb.models import EvidenceEvent, ModelIdentity, Usage
from kmb.provider import JudgeProvider, _tokens

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
REQUEST_FIELDS = {
    "model", "messages", "stream", "stream_options", "n", "tools", "tool_choice",
    "parallel_tool_calls", "temperature", "top_p", "presence_penalty", "frequency_penalty",
    "max_tokens", "max_completion_tokens", "stop", "seed", "response_format", "reasoning_effort",
    "verbosity", "logprobs", "top_logprobs", "logit_bias",
}
INFERENCE_FIELDS = REQUEST_FIELDS - {"model", "messages", "tools", "stream", "stream_options"}
SENSITIVE_FIELDS = {"authorization", "api_key", "apikey", "access_token", "token", "secret"}
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")


class GatewayError(RuntimeError):
    def __init__(self, code: str, status: int = 502, *, upstream_status: int | None = None):
        self.code = code
        self.status = status
        self.upstream_status = upstream_status
        super().__init__(code)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def handle_error(self, request, client_address):
        # Never emit request-dependent tracebacks or headers to stderr.
        pass


class TrialGateway:
    """A fresh context manager/token for each trial; never reuse between trials.

    A complete upstream response is checked before any bytes are released to the
    agent. Recorded tool calls are proposals, not evidence of executed actions.
    """

    def __init__(self, provider: JudgeProvider, max_tool_actions: int, timeout_seconds: int):
        if provider.api_style != "chat_completions":
            raise ValueError("trial gateway requires chat_completions upstream")
        if max_tool_actions < 0 or timeout_seconds <= 0:
            raise ValueError("invalid trial budget")
        self._provider = provider
        self._max_tool_actions = max_tool_actions
        self._timeout_seconds = timeout_seconds
        self._trial_token = secrets.token_urlsafe(32)
        self._events: list[EvidenceEvent] = []
        self._state_lock = threading.RLock()
        self._call_lock = threading.Lock()
        self._usage = Usage()
        self._calls = 0
        self._tool_calls_seen = 0
        self._budget_exhausted = False
        self._closed = False
        self._draining = False
        self._entered = False
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._deadline = 0.0
        self._returned_models: set[str] = set()
        self._inference_parameters: dict[str, dict[str, Any]] = {}
        self._active_loop: asyncio.AbstractEventLoop | None = None
        self._active_task: asyncio.Task | None = None

    def __enter__(self) -> Self:
        if self._entered:
            raise RuntimeError("trial gateway context cannot be reused")
        self._entered = True
        self._deadline = time.monotonic() + self._timeout_seconds
        self._server = _Server(("0.0.0.0", 0), self._handler_class())
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        with self._state_lock:
            self._closed = True
            if self._active_loop and self._active_task and self._active_loop.is_running():
                self._active_loop.call_soon_threadsafe(self._active_task.cancel)
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1)
        # Active calls are bounded/cancelled above. Wait for evidence accounting.
        if self._call_lock.acquire(timeout=2):
            self._call_lock.release()
        return False

    def drain(self) -> bool:
        """After the subject stops, finish an admitted request within its budget."""
        with self._state_lock:
            self._draining = True
        remaining = max(0.0, self._deadline - time.monotonic())
        settled = self._call_lock.acquire(timeout=min(remaining, self._provider._timeout_seconds))
        if settled:
            self._call_lock.release()
        self._record("gateway_drain", {"accepted_requests_settled": settled,
                                        "new_requests_allowed": False})
        return settled

    def container_config(self) -> dict[str, Any]:
        if self._server is None or self._closed:
            raise RuntimeError("trial gateway is not running")
        return {"base_url": f"http://host.docker.internal:{self._server.server_port}/v1",
                "api_key": self._trial_token, "model": self._provider.model,
                "transport": "chat_completions", "budget_enforced": True}

    @property
    def budget_exhausted(self) -> bool:
        with self._state_lock:
            return self._budget_exhausted

    def evidence_events(self) -> list[EvidenceEvent]:
        with self._state_lock:
            return [event.model_copy(deep=True) for event in self._events]

    def usage(self) -> Usage:
        with self._state_lock:
            return self._usage.model_copy(deep=True)

    def model_identity(self) -> ModelIdentity:
        with self._state_lock:
            returned = sorted(self._returned_models)
            identity = self._provider.public_identity(returned[0] if len(returned) == 1 else None)
            return ModelIdentity(requested=identity["requested"], returned=identity["returned"],
                                 provider=identity["provider"],
                                 parameters={"endpoint_sha256": identity["parameters"].get("endpoint_sha256"),
                                             "transport": "chat_completions", "stream_upstream": False,
                                             "timeout_seconds": self._timeout_seconds,
                                             "upstream_timeout_seconds": self._provider._timeout_seconds,
                                             "requested_inference_parameters": [self._inference_parameters[key]
                                                 for key in sorted(self._inference_parameters)],
                                             "returned_models": returned, "upstream_calls": self._calls,
                                             "proposed_tool_calls": self._tool_calls_seen,
                                             "max_tool_actions": self._max_tool_actions})

    def _contains_upstream_credential(self, value: Any) -> bool:
        """Reject a known upstream credential rather than alter agent-visible data."""
        if isinstance(value, str):
            return bool(self._provider._api_key and self._provider._api_key in value)
        if isinstance(value, dict):
            return any(self._contains_upstream_credential(key)
                       or self._contains_upstream_credential(item) for key, item in value.items())
        if isinstance(value, list):
            return any(self._contains_upstream_credential(item) for item in value)
        return False

    def _redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {self._redact(key): "[REDACTED]" if str(key).lower() in SENSITIVE_FIELDS else self._redact(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if not isinstance(value, str):
            return value
        origin_parts = urlsplit(self._provider.base_url)
        origin = f"{origin_parts.scheme}://{origin_parts.netloc}"
        for secret in (self._provider._api_key, self._trial_token, self._provider.base_url, origin):
            value = value.replace(secret, "[REDACTED]")

        def redact_query(match):
            try:
                parsed = urlsplit(match.group())
                if parsed.query or parsed.fragment or parsed.username or parsed.password:
                    host = parsed.hostname or "redacted"
                    return urlunsplit((parsed.scheme, host, parsed.path, "", "")) + "[REDACTED]"
            except ValueError:
                return "[REDACTED_URL]"
            return match.group()

        return URL_PATTERN.sub(redact_query, value)

    def _record(self, kind: str, data: dict[str, Any]):
        with self._state_lock:
            self._events.append(EvidenceEvent(id=f"gateway-{len(self._events) + 1}", kind=kind,
                                               data=self._redact(data)))

    def _check_live(self):
        if self._closed:
            raise GatewayError("trial_closed", 410)
        if time.monotonic() >= self._deadline:
            raise GatewayError("trial_timeout", 408)
        if self.budget_exhausted:
            raise GatewayError("tool_budget_exhausted", 429)

    def _account_usage(self, raw: Any):
        raw = raw if isinstance(raw, dict) else {}
        values = {"input_tokens": _tokens(raw.get("prompt_tokens")),
                  "output_tokens": _tokens(raw.get("completion_tokens"))}
        with self._state_lock:
            for key, value in values.items():
                previous = getattr(self._usage, key)
                setattr(self._usage, key, value if self._calls == 0 else
                        previous + value if previous is not None and value is not None else None)
            self._calls += 1

    async def _upstream(self, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        with self._state_lock:
            self._active_loop = asyncio.get_running_loop()
            self._active_task = asyncio.current_task()
        try:
            async with httpx.AsyncClient(transport=self._provider._transport, timeout=timeout,  # noqa: SIM117
                                         follow_redirects=False, trust_env=False) as client:
                async with client.stream(
                    "POST", self._provider.base_url + "/chat/completions", json=body,
                    headers={"Authorization": f"Bearer {self._provider._api_key}",
                             "Content-Type": "application/json"},
                ) as response:
                    if not 200 <= response.status_code < 300:
                        # Keep only the numeric status. Proxy error bodies can contain
                        # credentials, account details, or untrusted instructions.
                        raise GatewayError("upstream_http_error",
                                           upstream_status=response.status_code)
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_RESPONSE_BYTES:
                            raise GatewayError("upstream_response_too_large")
                    try:
                        result = json.loads(chunks)
                    except (ValueError, UnicodeDecodeError):
                        raise GatewayError("invalid_upstream_json") from None
                    if not isinstance(result, dict):
                        raise GatewayError("invalid_upstream_json")
                    return result
        except httpx.TimeoutException:
            raise GatewayError("upstream_timeout", 504) from None
        except httpx.HTTPError:
            raise GatewayError("upstream_transport_error") from None
        except asyncio.CancelledError:
            if self._closed:
                raise GatewayError("trial_closed", 410) from None
            raise
        finally:
            with self._state_lock:
                self._active_loop = None
                self._active_task = None

    async def _bounded_upstream(self, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(self._upstream(body, timeout), timeout=timeout)
        except TimeoutError:
            raise GatewayError("upstream_timeout", 504) from None

    def _forward(self, body: dict[str, Any]) -> dict[str, Any]:
        self._check_live()
        if self._draining:
            raise GatewayError("trial_draining", 410)
        if not isinstance(body, dict) or set(body) - REQUEST_FIELDS:
            raise GatewayError("unsupported_request_fields", 400)
        if body.get("model") != self._provider.model:
            raise GatewayError("model_not_allowed", 400)
        if body.get("n", 1) != 1 or not isinstance(body.get("stream", False), bool):
            raise GatewayError("unsupported_request_options", 400)
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages or any(not isinstance(x, dict) for x in messages):
            raise GatewayError("invalid_messages", 400)
        if not self._call_lock.acquire(timeout=max(0.001, self._deadline - time.monotonic())):
            raise GatewayError("trial_timeout", 408)
        try:
            self._check_live()
            if self._draining:
                raise GatewayError("trial_draining", 410)
            outgoing = {key: value for key, value in body.items() if key != "stream_options"}
            outgoing["stream"] = False
            outgoing["model"] = self._provider.model
            inference = {key: outgoing[key] for key in sorted(INFERENCE_FIELDS) if key in outgoing}
            safe_inference = self._redact(inference)
            canonical = json.dumps(safe_inference, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False, allow_nan=False)
            with self._state_lock:
                self._inference_parameters[canonical] = safe_inference
            self._record("model_request", {"request": outgoing, "client_stream": body.get("stream", False)})
            start = time.monotonic()
            try:
                timeout = min(self._provider._timeout_seconds, self._deadline - start)
                result = asyncio.run(self._bounded_upstream(outgoing, max(0.001, timeout)))
            except GatewayError as exc:
                self._account_usage(None)
                self._record("model_response", {"error": exc.code, "forwarded": False,
                                                 "upstream_http_status": exc.upstream_status,
                                                 "elapsed_seconds": time.monotonic() - start})
                raise
            self._account_usage(result.get("usage"))
            if self._contains_upstream_credential(result):
                self._record("model_response", {"error": "upstream_credential_in_response",
                                                 "forwarded": False})
                raise GatewayError("upstream_credential_in_response")
            choices = result.get("choices")
            if (not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict)
                    or not isinstance(choices[0].get("message"), dict)):
                self._record("model_response", {"error": "invalid_upstream_choices", "forwarded": False})
                raise GatewayError("invalid_upstream_choices")
            message = choices[0]["message"]
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list) or any(not isinstance(call, dict) for call in calls):
                self._record("model_response", {"error": "invalid_upstream_tool_calls", "forwarded": False})
                raise GatewayError("invalid_upstream_tool_calls")
            # Reject legacy function_call; it would otherwise evade tool-call accounting.
            if message.get("function_call") is not None:
                self._record("model_response", {"error": "legacy_function_call_unsupported", "forwarded": False})
                raise GatewayError("legacy_function_call_unsupported")
            with self._state_lock:
                returned = self._provider.public_identity(result.get("model"))["returned"]
                if returned is not None:
                    self._returned_models.add(returned)
                self._tool_calls_seen += len(calls)
                if self._tool_calls_seen > self._max_tool_actions:
                    self._budget_exhausted = True
            allowed = not self.budget_exhausted and not self._closed and time.monotonic() < self._deadline
            self._record("model_response", {"response": result, "forwarded": allowed,
                                             "proposed_tool_calls": len(calls),
                                             "elapsed_seconds": time.monotonic() - start})
            self._check_live()
            # Evidence has its own redaction boundary. Broad log redaction must never
            # silently rewrite a public URL or tool argument delivered to the agent.
            return result
        finally:
            self._call_lock.release()

    @staticmethod
    def _sse(response: dict[str, Any]) -> bytes:
        choice = response["choices"][0]
        message = choice["message"]
        common = {"id": response.get("id", "kmb-completion"), "object": "chat.completion.chunk",
                  "created": response.get("created", int(time.time())), "model": response.get("model", "")}
        delta = {"role": "assistant"}
        for key in ("content", "refusal", "reasoning_content"):
            if key in message and message[key] is not None:
                delta[key] = message[key]
        if message.get("tool_calls"):
            delta["tool_calls"] = [{**call, "index": index}
                                   for index, call in enumerate(message["tool_calls"])]
        chunks = [dict(common, choices=[{"index": 0, "delta": delta, "finish_reason": None}]),
                  dict(common, choices=[{"index": 0, "delta": {},
                                         "finish_reason": choice.get("finish_reason", "stop")}])]
        if isinstance(response.get("usage"), dict):
            chunks.append(dict(common, choices=[], usage=response["usage"]))
        return ("".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks)
                + "data: [DONE]\n\n").encode()

    def _handler_class(self):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                self.connection.settimeout(min(10, max(0.001, gateway._deadline - time.monotonic())))

            def log_message(self, format, *args):
                pass

            def _send(self, status: int, body: bytes, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self.wfile.write(body)

            def _json(self, status: int, body: dict[str, Any]):
                self._send(status, json.dumps(body, ensure_ascii=False).encode())

            def _handle(self):
                try:
                    auth = self.headers.get_all("Authorization") or []
                    expected = ("Bearer " + gateway._trial_token).encode()
                    if len(auth) != 1 or not hmac.compare_digest(auth[0].encode(), expected):
                        raise GatewayError("unauthorized_trial", 401)
                    gateway._check_live()
                    if self.command == "GET" and self.path == "/v1/models":
                        self._json(200, {"object": "list", "data": [
                            {"id": gateway._provider.model, "object": "model", "owned_by": "configured"}]})
                        return
                    if self.command != "POST" or self.path != "/v1/chat/completions":
                        raise GatewayError("route_not_allowed", 404)
                    sizes = self.headers.get_all("Content-Length") or []
                    if self.headers.get("Transfer-Encoding") or len(sizes) != 1:
                        raise GatewayError("content_length_required", 411)
                    try:
                        length = int(sizes[0])
                    except ValueError:
                        raise GatewayError("invalid_content_length", 400) from None
                    if length <= 0 or length > MAX_REQUEST_BYTES:
                        raise GatewayError("request_body_too_large", 413)
                    self.connection.settimeout(min(10, max(0.001, gateway._deadline - time.monotonic())))
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise GatewayError("incomplete_request_body", 400)
                    try:
                        body = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        raise GatewayError("invalid_request_json", 400) from None
                    result = gateway._forward(body)
                    if body.get("stream", False):
                        self._send(200, gateway._sse(result), "text/event-stream; charset=utf-8")
                    else:
                        self._json(200, result)
                except GatewayError as exc:
                    self._json(exc.status, {"error": {"message": exc.code, "type": "kmb_gateway_error",
                                                      "code": exc.code}})
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True
                except TimeoutError:
                    self._json(408, {"error": {"code": "request_timeout"}})
                except Exception:  # noqa: BLE001 — never emit request/header-dependent exceptions.
                    self._json(500, {"error": {"code": "gateway_internal_error"}})

            do_GET = _handle
            do_POST = _handle
            do_PUT = _handle
            do_DELETE = _handle
            do_PATCH = _handle
            do_OPTIONS = _handle

        return Handler
