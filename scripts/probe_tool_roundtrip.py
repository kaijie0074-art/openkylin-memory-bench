"""Explicit, synthetic two-call provider integration probe; no secret discovery."""
import json
import os
import time
from pathlib import Path

import httpx


def main():
    output = Path("reports/local-proxy-tool-probe.json")
    if output.exists():
        raise RuntimeError("probe_output_exists")
    model = os.environ["KMB_MODEL"]
    headers = {"Authorization": "Bearer " + os.environ["KMB_API_KEY"]}
    endpoint = os.environ["KMB_BASE_URL"].rstrip("/") + "/chat/completions"
    messages = [{"role": "user", "content": (
        "Call echo_probe exactly once with value memory-bench-check. "
        "After receiving its result reply only TOOL_OK.")}]
    tool = {"type": "function", "function": {
        "name": "echo_probe", "description": "Echo a harmless synthetic string",
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                       "required": ["value"], "additionalProperties": False}}}
    start = time.monotonic()
    with httpx.Client(timeout=60, trust_env=False, follow_redirects=False) as client:
        def request():
            response = client.post(endpoint, headers=headers, json={
                "model": model, "messages": messages, "tools": [tool], "stream": False})
            if response.status_code != 200:
                raise RuntimeError("provider_http_" + str(response.status_code))
            return response.json()

        first = request()
        message = first["choices"][0]["message"]
        calls = message.get("tool_calls", [])
        if len(calls) != 1:
            raise RuntimeError("expected_one_tool_call")
        call = calls[0]
        if (call["function"]["name"] != "echo_probe"
                or json.loads(call["function"]["arguments"]) != {"value": "memory-bench-check"}):
            raise RuntimeError("unexpected_tool_arguments")
        # A local echo is the only tool executed. No shell or filesystem tool.
        messages.extend([message, {"role": "tool", "tool_call_id": call["id"],
                                   "content": "memory-bench-check"}])
        second = request()
    if second["choices"][0]["message"].get("content", "").strip() != "TOOL_OK":
        raise RuntimeError("tool_result_not_acknowledged")
    if first.get("model") != model or second.get("model") != model:
        raise RuntimeError("returned_model_mismatch")
    report = {"status": "ready", "requested_model": model,
              "returned_models": [first["model"], second["model"]],
              "tool_roundtrip": True, "model_calls": 2,
              "usage": [first.get("usage"), second.get("usage")],
              "elapsed_seconds": round(time.monotonic() - start, 3),
              "formal_benchmark": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, OSError) as error:
        # No request bodies, headers, tokens or unbounded HTTP exception output.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
