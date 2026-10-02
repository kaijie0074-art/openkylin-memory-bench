import copy
import importlib.util
import json
import random
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "review_triage", Path(__file__).parents[1] / "scripts/prioritize-human-review.py")
triage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(triage)


def inputs():
    document = {"review_id": "review", "reviewer": "owner", "items": []}
    mapping = {"review_id": "review", "reviewers": ["owner", "peer"], "items": {}}
    scores = []
    for n in range(10):
        ident = f"item{n}"
        document["items"].append({"item_id": ident, "source": "real", "status": "completed",
                                  "files_complete": True, "file_inventory_complete": True,
                                  "criteria": [{"id": "c", "kind": "text_equals"}]})
        mapping["items"][ident] = {"run_id": f"run{n}", "task_id": f"task{n}",
                                   "provenance": "real", "evidence_hash": f"hash{n}",
                                   "criterion_ids": ["c"]}
        for variant in "ABC":
            scores.append({"run_id": f"run{n}", "task_id": f"task{n}", "evidence_hash": f"hash{n}",
                           "scorer": variant, "status": "scored",
                           "criteria": [{"criterion_id": "c", "verdict": "pass"}],
                           "reason": "SECRET_AUTOMATIC_REASON"})
    return document, mapping, scores


def test_priorities_random_population_and_blind_output():
    doc, mapping, scores = inputs()
    doc["items"][0]["criteria"][0]["kind"] = "semantic"
    doc["items"][1]["files_complete"] = False
    scores[7]["criteria"][0]["verdict"] = "fail"  # item2, B
    doc["items"][3]["status"] = "budget_exhausted"
    before = copy.deepcopy((doc, mapping, scores))
    public, private = triage.plan_queue(doc, mapping, scores, {"item3"}, seed="x")
    selected = {i["item_id"] for i in public["items"]}
    assert {"item0", "item1", "item2"} <= selected
    assert "item3" not in selected
    assert private["audit_pool_ids"] == list(mapping["items"])
    assert private["audit_ids"] == random.Random("x").sample(list(mapping["items"]), 4)
    assert len(selected) == len(public["items"])
    assert before == (doc, mapping, scores)
    encoded = json.dumps(public)
    for secret in ("SECRET_AUTOMATIC_REASON", "semantic", "disagreement", "verdict", "audit_ids"):
        assert secret not in encoded
    assert private["selection_freeze_eligible"] is False
    assert private["human_labels_created"] == 0


def test_audit_does_not_change_with_scores():
    doc, mapping, scores = inputs()
    _, first = triage.plan_queue(doc, mapping, scores, set())
    scores[1]["criteria"][0]["verdict"] = "fail"
    _, second = triage.plan_queue(doc, mapping, scores, set())
    assert first["audit_ids"] == second["audit_ids"]


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "hash", "criteria", "source", "mapping"])
def test_reject_misassociated_inputs(mutation):
    doc, mapping, scores = inputs()
    if mutation == "missing":
        scores.pop()
    elif mutation == "duplicate":
        scores.append(scores[0])
    elif mutation == "hash":
        scores[0]["evidence_hash"] = "wrong"
    elif mutation == "criteria":
        scores[0]["criteria"] = []
    elif mutation == "source":
        doc["items"][0]["source"] = "simulated"
    else:
        mapping["review_id"] = "different"
    with pytest.raises(ValueError):
        triage.plan_queue(doc, mapping, scores, set())


def test_error_abstention_and_unknown_kind_get_reviewed():
    doc, mapping, scores = inputs()
    scores[0].update(status="error", criteria=[])
    scores[3]["criteria"][0]["verdict"] = "undetermined"
    doc["items"][2]["criteria"][0]["kind"] = "future_kind"
    public, private = triage.plan_queue(doc, mapping, scores, set(), audit_count=0)
    assert {i["item_id"] for i in public["items"]} == {"item0", "item1", "item2"}
    assert "scoring_error" in private["routing"]["item0"]["reasons"]


def test_objective_csv_and_text_checks_are_not_prioritized_as_semantic():
    doc, mapping, scores = inputs()
    for item, kind in zip(doc["items"], ["csv_equals", "text_contains", "text_not_contains"]):
        item["criteria"][0]["kind"] = kind
    public, _ = triage.plan_queue(doc, mapping, scores, set(), audit_count=0)
    assert public["items"] == []


def test_generate_preserves_inputs_labels_and_rejects_overwrite(tmp_path):
    doc, mapping, scores = inputs()
    review = tmp_path / "review"
    source = review / "reviewer-1"
    source.mkdir(parents=True)
    (source / "cards.json").write_text(json.dumps(doc))
    (source / "index.html").write_text("original")
    (review / "private-mapping.json").write_text(json.dumps(mapping))
    score_root = tmp_path / "scores"
    score_root.mkdir()
    for n, score in enumerate(scores):
        (score_root / f"{n}.json").write_text(json.dumps(score))
    old = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    out = tmp_path / "queue"
    result = triage.generate(review, score_root, out, "owner", 4, "x")
    assert result["queued"] == 4 and result["human_labels_created"] == 0
    assert all(p.read_bytes() == content for p, content in old.items())
    assert not (source / "labels").exists()
    assert (out / "private-routing.json").stat().st_mode & 0o777 == 0o600
    assert "SECRET_AUTOMATIC_REASON" not in (out / "index.html").read_text()
    with pytest.raises(ValueError, match="output exists"):
        triage.generate(review, score_root, out, "owner", 4, "x")
    labels = source / "labels"
    labels.mkdir()
    (labels / "wrong-name.json").write_text(json.dumps({
        "review_id": "review", "reviewer": "owner", "item_id": "item0",
        "evidence_hash": "hash0", "source": "human_attested", "criteria": {"c": "pass"},
        "note": "actual fixture reviewer"}))
    with pytest.raises(ValueError, match="invalid existing human label"):
        triage.generate(review, score_root, tmp_path / "another-queue", "owner", 4, "x")
    (labels / "wrong-name.json").rename(labels / "item0.json")
    peer_labels = review / "reviewer-2" / "labels"
    peer_labels.mkdir(parents=True)
    (peer_labels / "item0.json").write_text("not JSON; must never be read")
    new_out = tmp_path / "with-existing-label"
    result = triage.generate(review, score_root, new_out, "owner", 10, "x")
    assert result["already_labelled"] == 1 and result["queued"] == 9
    queued = json.loads((new_out / "queue.json").read_text())["items"]
    assert "item0" not in {item["item_id"] for item in queued}
