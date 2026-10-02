"""One synthetic frozen-evidence request, logging event shapes rather than text."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import Counter
from pathlib import Path

import httpx

from kmb.dataset import load_dataset
from kmb.provider import JudgeProvider
from kmb.scorers import _payload
from kmb.storage import load_evidence, write_json

EVENT_TYPES = {
    "response.created", "response.in_progress", "response.completed", "response.failed",
    "response.incomplete", "response.queued", "error", "response.output_item.added",
    "response.output_item.done", "response.content_part.added", "response.content_part.done",
    "response.output_text.delta", "response.output_text.done", "response.refusal.delta",
    "response.refusal.done", "response.reasoning_summary_part.added",
    "response.reasoning_summary_part.done", "response.reasoning_summary_text.delta",
    "response.reasoning_summary_text.done", "response.reasoning_text.delta",
    "response.reasoning_text.done", "response.function_call_arguments.delta",
    "response.function_call_arguments.done",
}


def safe_error(error, provider):
    if not isinstance(error, dict):
        return None
    result = {}
    for field in ("code", "type", "message"):
        value = error.get(field)
        if not isinstance(value, str):
            continue
        # Only error messages may be quoted; never output text, headers or request URLs.
        value = value.replace(provider._api_key, "[REDACTED]")
        value = value.replace(provider.base_url, "[REDACTED_URL]")
        value = re.sub(r"https?://\S+", "[REDACTED_URL]", value)
        value = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", value)
        value = re.sub(r"(?i)(?:api[_-]?key|token|authorization|password|secret)\s*[:=]\s*\S+",
                       "[REDACTED_CREDENTIAL]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", value)
        value = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                       "[REDACTED_JWT]", value)
        value = re.sub(r"[A-Za-z0-9_-]{40,}", "[REDACTED_LONG_VALUE]", value)
        value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]+", "[REDACTED_EMAIL]", value)
        result[field] = value[:500]
    return result


def message_shape(item):
    if not isinstance(item, dict) or item.get("type") != "message":
        return None
    channel = item.get("channel")
    channel = channel if isinstance(channel, str) and channel in {
        "final", "commentary", "analysis"} else None
    content = item.get("content", [])
    text_lengths = [len(part["text"]) for part in content if isinstance(part, dict)
                    and part.get("type") == "output_text" and isinstance(part.get("text"), str)]
    return {"channel": channel, "text_lengths": text_lengths}


async def probe(evidence_path: Path, dataset_root: Path):
    configured = JudgeProvider.from_env()
    provider = JudgeProvider(configured.base_url, configured._api_key, configured.model, "responses")
    evidence = load_evidence(evidence_path)
    rubric = {task.id: rubric for task, rubric in load_dataset(dataset_root)}[evidence.task_id]
    body = provider._request_body(_payload(evidence, rubric.criteria))
    body["stream"] = True
    # Match the Chat translator's system -> developer conversion exactly.
    body["input"][0]["role"] = "developer"
    report = {"operation": "responses_event_diagnostic", "formal_benchmark": False,
              "model_calls": 1, "model": provider.public_identity(),
              "run_id": evidence.run_id, "evidence_hash": evidence.evidence_hash,
              "events": [], "errors": []}
    counts = Counter()
    started = time.monotonic()
    try:
        async with (
            httpx.AsyncClient(timeout=60, trust_env=False, follow_redirects=False) as client,
            client.stream("POST", provider.base_url + "/responses", json=body,
                          headers={"Authorization": "Bearer " + provider._api_key}) as response,
        ):
            report["http_status"] = response.status_code
            if response.status_code != 200:
                raw = await response.aread()
                try:
                    data = json.loads(raw)
                except ValueError:
                    data = {}
                report["errors"].append(safe_error(data.get("error"), provider))
            else:
                async for line in response.aiter_lines():
                    if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                        continue
                    try:
                        data = json.loads(line[5:])
                    except ValueError:
                        counts["invalid_event_json"] += 1
                        continue
                    event_type = data.get("type")
                    event_type = event_type if event_type in EVENT_TYPES else "other"
                    counts[event_type] += 1
                    event = {"type": event_type}
                    if event_type == "response.output_text.delta":
                        event["delta_chars"] = len(data.get("delta", ""))
                        if isinstance(data.get("output_index"), int):
                            event["output_index"] = data["output_index"]
                    shape = message_shape(data.get("item"))
                    if shape is not None:
                        event["message"] = shape
                    completed = data.get("response", {})
                    if isinstance(completed, dict):
                        if event_type == "response.completed":
                            event["messages"] = [shape for item in completed.get("output", [])
                                                 if (shape := message_shape(item)) is not None]
                            returned, usage = provider._response_metadata(completed)
                            report.update(model=provider.public_identity(returned), usage=usage)
                        error = safe_error(completed.get("error"), provider)
                        if error:
                            report["errors"].append(error)
                    error = safe_error(data.get("error"), provider)
                    if error:
                        report["errors"].append(error)
                    report["events"].append(event)
    except httpx.HTTPError:
        report["transport_error"] = True
    report["event_counts"] = dict(counts)
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("diagnostic_output_exists")
    result = asyncio.run(probe(args.evidence, args.dataset))
    write_json(args.output, result, exclusive=True)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 — do not print request/response-bearing exceptions.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
