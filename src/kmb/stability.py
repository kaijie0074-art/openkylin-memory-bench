"""Repeated runs are grouped observations, never extra independent task families."""
import json
from collections import Counter, defaultdict
from itertools import combinations


def repeated_run_summary(evidence, scores):
    score_map = {(s.run_id, s.scorer): s for s in scores}
    groups = defaultdict(list)
    for run in evidence:
        execution = run.model.model_dump()
        execution["parameters"] = {k: v for k, v in execution["parameters"].items()
                                   if k not in {"upstream_calls", "proposed_tool_calls"}}
        key = (run.task_id, run.agent, run.agent_version, run.provenance,
               run.model.requested, run.model.returned, run.environment.get("image_id"),
               json.dumps(execution, sort_keys=True))
        groups[key].append(run)
    result = []
    for key, runs in sorted(groups.items(), key=lambda pair: repr(pair[0])):
        if len(runs) < 2:
            continue
        variants = {}
        for variant in "ABC":
            outcomes = []
            conditions = set()
            for run in runs:
                record = score_map.get((run.run_id, variant))
                if record is not None:
                    conditions.add(json.dumps({"version": record.scorer_version,
                                               "fingerprint": record.scoring_fingerprint,
                                               "model": record.model.model_dump()}, sort_keys=True))
                outcomes.append(record.verdict if record and record.status == "scored"
                                else "scoring_error" if record and record.status == "error"
                                else "not_scored")
            counts = dict(Counter(outcomes))
            pairs = list(combinations(outcomes, 2))
            known = [v for v in outcomes if v in {"pass", "fail"}]
            variants[variant] = {
                "outcomes": counts, "decided_runs": len(known),
                "configuration_mismatch": len(conditions) > 1,
                "pairwise_verdict_agreement": (sum(a == b for a, b in pairs) / len(pairs)
                                               if len(conditions) <= 1 else None),
                "all_decided_and_identical": (len(conditions) == 1 and len(known) == len(runs)
                                              and len(set(known)) == 1),
                "interpretation": "unknown/error are explicit categories; agreement is not accuracy",
            }
        result.append({"task_id": key[0], "agent": key[1], "agent_version": key[2],
                       "provenance": key[3], "requested_model": key[4], "returned_model": key[5],
                       "image_id": key[6], "repeats": len(runs), "variants": variants,
                       "execution_status_counts": dict(Counter(r.status for r in runs)),
                       "independent_task_count": 1})
    return result
