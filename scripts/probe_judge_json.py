"""One explicitly configured model call on frozen evidence; no raw output logs.

This diagnostic does not run an agent, repair JSON, retry, or write a score.
Use a new output path so earlier failures remain available for comparison.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

import httpx

from kmb.dataset import load_dataset
from kmb.provider import JudgeProvider, ProviderError
from kmb.scorers import _parse_output, _payload
from kmb.storage import load_evidence, write_json

SAFE_UPSTREAM_CODES = {
    "no_available_account", "no_available_model", "rate_limit_exceeded",
    "insufficient_quota", "authentication_error", "invalid_api_key",
    "invalid_request_error", "upstream_error", "server_error",
}


async def probe(evidence_path: Path, dataset_root: Path) -> dict:
    provider = JudgeProvider.from_env()
    evidence = load_evidence(evidence_path)
    rubric = {task.id: rubric for task, rubric in load_dataset(dataset_root)}[evidence.task_id]
    payload = _payload(evidence, rubric.criteria)
    body = provider._request_body(payload)
    started = time.monotonic()
    report = {
        "operation": "judge_format_diagnostic", "model_calls": 1,
        "run_id": evidence.run_id, "task_id": evidence.task_id,
        "evidence_hash": evidence.evidence_hash,
        "request_sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
        "model": provider.public_identity(), "formal_benchmark": False,
    }
    suffix = "/responses" if provider.api_style == "responses" else "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False, follow_redirects=False) as client:
            response = await client.post(provider.base_url + suffix, json=body, headers={
                "Authorization": "Bearer " + provider._api_key})
    except httpx.HTTPError:
        report.update(status="failed", error="provider_transport_error")
    else:
        report["http_status"] = response.status_code
        try:
            data = response.json()
        except ValueError:
            data = None
        if not 200 <= response.status_code <= 299:
            report.update(status="failed", error="provider_http_error")
            error = data.get("error") if isinstance(data, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            if isinstance(code, str) and code in SAFE_UPSTREAM_CODES:
                report["upstream_error_code"] = code
        else:
            returned, usage = provider._response_metadata(data)
            report.update(model=provider.public_identity(returned), usage=usage,
                          diagnostics=provider._diagnostics(data))
            try:
                output, _, _ = provider._decode(data)
                _parse_output(output, evidence, rubric.criteria, set(payload["allowed_evidence_refs"]))
            except ProviderError as error:
                report.update(status="failed", error=error.code)
            else:
                report.update(status="valid", strict_json_and_criteria=True)
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def main() -> int:
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
    return 0 if result["status"] == "valid" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ProviderError, ValueError, KeyError, TypeError, OSError) as error:
        # Do not echo exceptions which may contain payloads, paths, or configuration.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
