"""Run isolated designed scorer controls; default mode makes no model requests.

Pass --with-model only when ready for configured KMB model calls. All outputs
require a fresh directory. Model failures remain failed; no substitute verdicts.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from kmb.calibration import run_calibration
from kmb.provider import JudgeProvider, ProviderError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dataset", type=Path, default=Path("data"))
    parser.add_argument("--with-model", action="store_true",
                        help="Use only the explicitly configured KMB provider for B and semantic C.")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("calibration output already exists")
    provider = JudgeProvider.from_env() if args.with_model else None
    result = asyncio.run(run_calibration(args.output, args.dataset, provider))
    compact = {key: result[key] for key in ("artifact_role", "provenance", "suite_hash",
                "experiment_fingerprint", "protocol_unchanged", "model_requests_enabled")}
    compact["variants"] = {key: {field: value[field] for field in (
        "expected_match", "coverage", "error_count", "critical_false_passes",
        "critical_unsupported_passes", "critical_controls_all_match")}
        for key, value in result["variants"].items()}
    print(json.dumps(compact, ensure_ascii=False))
    if not result["protocol_unchanged"] or not result["suite_unchanged"]:
        return 1
    if args.with_model and any(item["error_count"] or not item["critical_controls_all_match"]
                               for item in result["variants"].values()):
        return 1
    if args.with_model and any(item["expected_match"] != item["cases"]
                               or item["model_cases_with_returned_identity"] != item["model_cases_required"]
                               for variant, item in result["variants"].items() if variant != "A"):
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ProviderError, ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
