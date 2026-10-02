"""Operator-side review triage; never changes frozen evidence, scores or human labels."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import random
from pathlib import Path

OBJECTIVE = {"json_equals", "csv_equals", "text_equals", "text_contains", "text_not_contains",
             "file_absent", "file_exists", "action_contains"}


def plan_queue(document, mapping, scores, labelled, *, audit_count=4, seed="triage-v1"):
    """Return a blind queue and private routing details, not a human consensus."""
    if audit_count < 0:
        raise ValueError("audit count must be nonnegative")
    if document["review_id"] != mapping["review_id"]:
        raise ValueError("review ID mismatch")
    ids = [item["item_id"] for item in document["items"]]
    if len(set(ids)) != len(ids) or set(ids) != set(mapping["items"]):
        raise ValueError("cards must exactly match the review mapping")
    if not set(labelled) <= set(ids):
        raise ValueError("unknown labelled item")
    runs = {entry["run_id"] for entry in mapping["items"].values()}
    if len(runs) != len(ids):
        raise ValueError("duplicate mapped run")
    by_run = {}
    for score in scores:
        key = (score["run_id"], score["scorer"])
        if key in by_run or key[0] not in runs or key[1] not in "ABC" or len(key[1]) != 1:
            raise ValueError("duplicate, extra or invalid scorer/run")
        by_run[key] = score
    if set(by_run) != {(run, variant) for run in runs for variant in "ABC"}:
        raise ValueError("complete A/B/C scoring batch required")
    routing = {}
    for position, item in enumerate(document["items"], 1):
        entry = mapping["items"][item["item_id"]]
        if item["source"] != "real" or entry["provenance"] != "real":
            raise ValueError("triage requires real evidence")
        criteria = {c["id"] for c in item["criteria"]}
        if criteria != set(entry["criterion_ids"]):
            raise ValueError("card criterion mismatch")
        variants = [by_run[(entry["run_id"], v)] for v in "ABC"]
        for score in variants:
            if (score["evidence_hash"] != entry["evidence_hash"]
                    or score["task_id"] != entry["task_id"]):
                raise ValueError("score/evidence association mismatch")
            rows = score["criteria"]
            if (score["status"] not in {"scored", "error"}
                    or any(c["verdict"] not in {"pass", "fail", "undetermined"} for c in rows)
                    or len({c["criterion_id"] for c in rows}) != len(rows)
                    or (score["status"] == "scored" and {c["criterion_id"] for c in rows} != criteria)):
                raise ValueError("invalid score criteria/status")
        reasons = []
        if any(c["kind"] not in OBJECTIVE for c in item["criteria"]):
            reasons.append("semantic_or_unrecognized_criterion")
        if any(c["kind"] == "action_contains" for c in item["criteria"]):
            reasons.append("action_evidence_requires_coverage_review")
        if not item.get("files_complete") or not item.get("file_inventory_complete"):
            reasons.append("incomplete_file_evidence")
        if item["status"] != "completed":
            reasons.append("execution_not_completed")
        if any(s["status"] != "scored" for s in variants):
            reasons.append("scoring_error")
        if any(c["verdict"] == "undetermined" for s in variants for c in s["criteria"]):
            reasons.append("scoring_abstention")
        signatures = {tuple(sorted((c["criterion_id"], c["verdict"]) for c in s["criteria"]))
                      for s in variants}
        if len(signatures) > 1 or any(s.get("conflicts") for s in variants):
            reasons.append("scorer_disagreement_or_conflict")
        routing[item["item_id"]] = {"position": position, "reasons": reasons,
                                    "already_labelled": item["item_id"] in labelled}
    priority = [i for i in ids if routing[i]["reasons"] and i not in labelled]
    pool = ids.copy()
    rng = random.Random(seed)
    # Draw from the whole review population, independent of score-based prioritization.
    audit = rng.sample(pool, min(audit_count, len(pool)))
    rng.shuffle(priority)
    selected = list(dict.fromkeys(priority + [i for i in audit if i not in labelled]))
    # Mixing hides the per-card reason, but does not promise perfect anonymity/blinding.
    rng.shuffle(selected)
    public = {"review_id": document["review_id"], "reviewer": document["reviewer"],
              "purpose": "人工校准工作队列；不是完整人审或总体准确率报告。",
              "instructions": "先看要求与原始证据，再口述判断和理由。也可指出标准有歧义或需要补充证据。",
              "items": [{"item_id": i, "original_position": routing[i]["position"]} for i in selected]}
    private = {"seed": seed, "audit_requested": audit_count, "priority_ids": priority,
               "audit_ids": audit, "audit_pool_ids": pool, "routing": routing,
               "deferred_ids": [i for i in ids if i not in selected and i not in labelled],
               "human_labels_created": 0, "selection_freeze_eligible": False,
               "limitation": "Adaptive diagnostic queue plus uniform sample from entire review population. "
                             "Do not pool into a population accuracy estimate or two-reviewer consensus."}
    return public, private


def generate(review: Path, scores_root: Path, output: Path, reviewer: str, audit_count: int, seed: str):
    if output.exists():
        raise ValueError("output exists; preserve old queues")
    mapping_path = review / "private-mapping.json"
    mapping = json.loads(mapping_path.read_text())
    index = mapping["reviewers"].index(reviewer) + 1
    source = review / f"reviewer-{index}"
    cards_path = source / "cards.json"
    document = json.loads(cards_path.read_text())
    if document["reviewer"] != reviewer:
        raise ValueError("reviewer mismatch")
    paths = sorted(scores_root.rglob("*.json"))
    scores = [json.loads(p.read_text()) for p in paths]
    labelled = set()
    label_paths = sorted((source / "labels").glob("*.json"))
    for path in label_paths:
        label = json.loads(path.read_text())
        item_id = label["item_id"]
        entry = mapping["items"][item_id]
        if (path.name != item_id + ".json" or item_id in labelled
                or label["review_id"] != document["review_id"] or label["reviewer"] != reviewer
                or label["evidence_hash"] != entry["evidence_hash"]
                or label["source"] != "human_attested"
                or set(label["criteria"]) != set(entry["criterion_ids"])
                or any(v not in {"pass", "fail", "undetermined"} for v in label["criteria"].values())
                or not label["note"].strip()):
            raise ValueError("invalid existing human label")
        labelled.add(item_id)
    public, private = plan_queue(document, mapping, scores, labelled,
                                 audit_count=audit_count, seed=seed)
    private["input_sha256"] = {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in [mapping_path, cards_path, *paths, *label_paths]}
    private["review_directory"] = str(review.resolve())
    output.mkdir(parents=True, mode=0o700)
    for name, value in [("queue.json", public), ("private-routing.json", private)]:
        path = output / name
        with path.open("x") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        path.chmod(0o600)
    rows = []
    for n, item in enumerate(public["items"], 1):
        url = (source / "index.html").resolve().as_uri() + f"#item-{item['original_position']}"
        rows.append(f'<li><a href="{html.escape(url, quote=True)}">审核第 {n} 项：查看完整任务与证据</a>'
                    f'<p>记录标识：{html.escape(item["item_id"])}</p></li>')
    page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>人工校准工作队列</title><style>body{max-width:850px;margin:40px auto;'
            'padding:24px;font:18px/1.8 system-ui;color:#17283c;background:#f5f7fa}'
            'li{background:white;margin:20px 0;padding:20px}a{color:#1659b0}</style>'
            '<h1>人工校准工作队列</h1><p>' + html.escape(public["purpose"]) + '</p><p>'
            + html.escape(public["instructions"]) + '</p>'
            '<p>AI 负责展示、翻页与记录。队列不含自动评分或逐项入选原因。'
            '页面仅提供导航；原始证据与已保存判断保持不变。</p><ol>'
            + ''.join(rows) + '</ol></html>')
    (output / "index.html").write_text(page)
    return {"queued": len(public["items"]), "already_labelled": len(labelled),
            "human_labels_created": 0, "output": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviewer", default="reviewer-user")
    parser.add_argument("--audit-count", type=int, default=4)
    parser.add_argument("--seed", default="triage-v1")
    args = parser.parse_args()
    print(json.dumps(generate(args.review, args.scores, args.output, args.reviewer,
                              args.audit_count, args.seed), ensure_ascii=False))


if __name__ == "__main__":
    main()
