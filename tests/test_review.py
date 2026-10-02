"""Synthetic review fixtures exercise policy; no genuine human labels are claimed."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kmb.dataset import load_dataset
from kmb.models import (
    Criterion,
    CriterionScore,
    EvidenceBundle,
    EvidenceEvent,
    ModelIdentity,
    PrivateRubric,
    ScoreResult,
)
from kmb.review import (
    adjudicate,
    comparison,
    consensus,
    engineering_protocol_hash,
    experiment_fingerprint,
    export_review,
    freeze_engineering,
    freeze_selection,
    frozen_conditions,
    record_label,
    rubric_hash,
    selection_manifest_hash,
    validate_engineering_evidence,
    validate_engineering_protocol,
    validate_engineering_scores,
    validate_holdout_conditions,
    validate_holdout_provider,
    validate_holdout_scores,
)
from kmb.scorers import scoring_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def evidence(run_id="unit-run", **overrides):
    values = {
        "run_id": run_id, "task_id": "unit-task", "ability": "update", "family": "unit-family",
        "split": "dev", "agent": "test-fixture", "provenance": "simulated",
        "files_after": {"out.txt": "new"},
        "events": [EvidenceEvent(id="e1", kind="assistant_output", data={"text": "fixture response"})],
    }
    values.update(overrides)
    return EvidenceBundle(**values).freeze()


def rubric():
    return PrivateRubric(task_id="unit-task", criteria=[
        Criterion(id="c1", kind="text_equals", path="out.txt", expected="new", critical=True,
                  description="Synthetic test criterion"),
    ])


def automatic(bundle, variant="A", verdict="pass", **overrides):
    values = {"run_id": bundle.run_id, "task_id": bundle.task_id, "evidence_hash": bundle.evidence_hash,
              "scorer": variant, "verdict": verdict, "criteria": [
                  CriterionScore(criterion_id="c1", verdict=verdict, reason="unit-test fixture",
                                 critical=True, evidence_refs=["file:out.txt"]),
              ]}
    values.update(overrides)
    return ScoreResult(**values)


def human(bundle, verdict="pass"):
    return {"labels": {bundle.run_id: {
        "evidence_hash": bundle.evidence_hash, "task_id": bundle.task_id,
        "split": bundle.split, "provenance": bundle.provenance,
        "criteria": {"c1": verdict}, "verdict": verdict,
    }}, "pending": [], "conflicts": []}


def exported(tmp_path):
    directory = tmp_path / "review"
    bundle = evidence()
    info = export_review([bundle], {"unit-task": rubric()}, directory, ("Alice", "Bob"))
    mapping = json.loads((directory / "private-mapping.json").read_text())
    item = next(iter(mapping["items"]))
    return directory, item, info


def test_export_omits_automatic_scores_and_preserves_simulation_label(tmp_path):
    directory, item, _ = exported(tmp_path)
    first = json.loads((directory / "reviewer-1/cards.json").read_text())
    card = first["items"][0]
    assert card["item_id"] == item and card["source"] == "simulated"
    assert not ({"scores", "verdict", "agent", "run_id"} & set(card))
    assert consensus(directory)["pending"] == [item]
    with pytest.raises(ValueError, match="already exists"):
        export_review([evidence()], {"unit-task": rubric()}, directory)


def test_both_reviews_required_then_disagreement_requires_adjudication(tmp_path):
    directory, item, _ = exported(tmp_path)
    record_label(directory, "Alice", item, {"c1": "pass"}, "Synthetic test label, not a real human judgment")
    with pytest.raises(ValueError, match="both independent"):
        adjudicate(directory, item, {"c1": "pass"}, "Synthetic test adjudication")
    record_label(directory, "Bob", item, {"c1": "fail"}, "Synthetic test counter-label")
    before = consensus(directory)
    assert before["conflicts"] == [item] and before["labels"] == {}
    adjudicate(directory, item, {"c1": "fail"}, "Synthetic test adjudication")
    after = consensus(directory)
    assert not after["conflicts"] and not after["pending"]
    assert after["labels"]["unit-run"]["verdict"] == "fail"
    assert after["labels"]["unit-run"]["independent_agreement"] is False


def test_human_label_requires_complete_criteria_identity_and_reason(tmp_path):
    directory, item, _ = exported(tmp_path)
    with pytest.raises(ValueError, match="unknown reviewer"):
        record_label(directory, "Other", item, {"c1": "pass"}, "x")
    with pytest.raises(ValueError, match="every criterion"):
        record_label(directory, "Alice", item, {}, "x")
    with pytest.raises(ValueError, match="reasoning"):
        record_label(directory, "Alice", item, {"c1": "pass"}, " ")


def test_labels_from_another_review_cannot_be_reused_silently(tmp_path):
    directory, item, _ = exported(tmp_path)
    first = record_label(directory, "Alice", item, {"c1": "pass"}, "Synthetic test label")
    record_label(directory, "Bob", item, {"c1": "pass"}, "Synthetic test label")
    label = json.loads(first.read_text())
    label["review_id"] = "another-review"
    first.write_text(json.dumps(label))
    with pytest.raises(ValueError):
        consensus(directory)


def test_mixed_simulated_real_results_and_duplicate_scores_rejected():
    first = evidence()
    second = evidence("other", provenance="real")  # only a unit-test classification fixture
    with pytest.raises(ValueError, match="separate reports"):
        comparison([first, second], [])
    one = automatic(first)
    with pytest.raises(ValueError, match="duplicate scorer"):
        comparison([first], [one, one])


def test_no_humans_means_no_accuracy_or_selected_winner():
    bundle = evidence()
    result = comparison([bundle], [automatic(bundle)])
    assert result["human_review"] == "not_performed"
    assert all(v["human_agreement"] is None and not v["meets_internal_target"] for v in result["variants"])


def test_critical_false_pass_is_counted_even_if_overall_undetermined():
    bundle = evidence()
    scored = automatic(bundle, verdict="undetermined", criteria=[
        CriterionScore(criterion_id="c1", verdict="pass", critical=True, reason="test"),
        CriterionScore(criterion_id="c2", verdict="undetermined", reason="test"),
    ])
    labels = human(bundle, "fail")
    labels["labels"][bundle.run_id]["criteria"]["c2"] = "undetermined"
    result = comparison([bundle], [scored], labels)["variants"][0]
    assert result["critical_false_passes"] == 1
    assert result["coverage"] == 0
    assert result["human_agreement"] is None


def test_scores_for_wrong_task_rejected_even_with_matching_run_hash():
    bundle = evidence()
    bad = automatic(bundle, task_id="other-task")
    with pytest.raises(ValueError):
        comparison([bundle], [bad])


def test_inconsistent_score_aggregate_is_not_counted_as_pass():
    bundle = evidence()
    with pytest.raises(ValueError):
        bad = automatic(bundle, verdict="pass", criteria=[
            CriterionScore(criterion_id="c1", verdict="fail", critical=True, reason="test"),
        ])
        comparison([bundle], [bad], human(bundle))


def test_unknown_usage_is_not_zero_and_infra_is_excluded():
    valid = evidence()
    broken = evidence("broken", status="infrastructure_error")
    result = comparison([valid, broken], [automatic(valid)], human(valid))
    assert result["valid_runs"] == 1 and result["infrastructure_or_unknown_runs"] == 1
    assert result["variants"][0]["tokens"] is None


def test_fingerprint_includes_data_with_either_supported_root_form(tmp_path):
    tasks = tmp_path / "data/tasks"
    rubrics = tmp_path / "data/rubrics"
    tasks.mkdir(parents=True)
    rubrics.mkdir(parents=True)
    task = tasks / "fixture.json"
    task.write_text('{"version": 1}')
    before = experiment_fingerprint(tmp_path)
    assert before == experiment_fingerprint(tmp_path / "data")
    task.write_text('{"version": 2}')
    assert experiment_fingerprint(tmp_path) != before


def test_freeze_rejects_incomplete_selection_matrix(tmp_path):
    one = evidence(provenance="real", split="selection", agent="openclaw")
    scored = automatic(one)
    result = comparison([one], [scored], human(one))
    with pytest.raises(ValueError, match="complete 18-task"):
        freeze_selection(result, tmp_path / "choice.json", [scored], ROOT / "data")


def complete_selection_fixture():
    # These fabricated objects ONLY test freeze policy; no files leave pytest's tmp_path.
    bundles, scores = [], []
    labels = {"labels": {}, "pending": [], "conflicts": []}
    for task, requirements in load_dataset(ROOT, "selection"):
        for agent in ("openclaw", "hermes"):
            bundle = evidence(f"unit-{task.id}-{agent}", task_id=task.id, ability=task.ability,
                              family=task.family, split="selection", provenance="real", agent=agent,
                              agent_version="unit-test-only",
                              environment={"image_id": "sha256:" + ("a" if agent == "openclaw" else "b") * 64},
                              model=ModelIdentity(requested="fixture", returned="fixture",
                                                  provider="https://example.invalid",
                                                  parameters={"transport": "chat_completions", "endpoint_sha256": hashlib.sha256(b"https://example.invalid/v1").hexdigest()}))
            criteria = [CriterionScore(criterion_id=c.id, verdict="pass", reason="unit-test fixture",
                                       critical=c.critical) for c in requirements.criteria]
            scored = automatic(bundle, criteria=criteria,
                               scoring_fingerprint=scoring_fingerprint(requirements))
            bundles.append(bundle)
            for variant in "ABC":
                copy = scored.model_copy(deep=True, update={"scorer": variant})
                if variant == "B" or (variant == "C" and any(c.kind == "semantic" for c in requirements.criteria)):
                    copy.model = judge_identity()
                scores.append(copy)
            labels["labels"][bundle.run_id] = {
                "evidence_hash": bundle.evidence_hash, "task_id": bundle.task_id,
                "split": "selection", "provenance": "real", "verdict": "pass",
                "criteria": {c.id: "pass" for c in requirements.criteria},
                "rubric_hash": rubric_hash(requirements),
            }
    return bundles, scores, labels


def engineering_fixture(tmp_path):
    # Fully fabricated policy fixture; no synthetic run is reported as agent evidence.
    bundles, scores, _ = complete_selection_fixture()
    by_id = {}
    for bundle in bundles:
        bundle.model.parameters.update({"max_tool_actions": 20, "timeout_seconds": 600})
        bundle.freeze()
        by_id[bundle.run_id] = bundle
    for item in scores:
        item.evidence_hash = by_id[item.run_id].evidence_hash
    protocol = freeze_engineering(bundles, scores, tmp_path / "engineering.json", ROOT,
                                  calibration=calibration_controls(tmp_path))
    return protocol, bundles, scores


def test_engineering_freeze_has_no_human_claim_or_selected_winner(tmp_path):
    protocol, bundles, scores = engineering_fixture(tmp_path)
    assert protocol["protocol_type"] == "engineering_validation"
    assert protocol["human_validation"] == "not_performed"
    assert protocol["selected_scorer"] is None
    assert len(protocol["selection_evidence_hashes"]) == 36
    assert set(protocol["controls"]["variants"]) == {"A", "B", "C"}
    assert validate_engineering_protocol(protocol, ROOT) is protocol
    with pytest.raises(FileExistsError):
        freeze_engineering(bundles, scores, tmp_path / "engineering.json", ROOT,
                           calibration=calibration_controls(tmp_path))


def test_engineering_holdout_matrix_score_and_binding(tmp_path):
    protocol, _, _ = engineering_fixture(tmp_path)
    bundles, scores, manifest = complete_holdout_fixture(protocol)
    manifest["selection_hash"] = None
    manifest["protocol_hash"] = engineering_protocol_hash(protocol)
    assert validate_engineering_scores(bundles, scores, protocol, ROOT, manifest)["eligible"]
    data = comparison(bundles, scores, protocol=protocol, dataset_root=ROOT, holdout_manifest=manifest)
    assert data["human_review"] == "not_performed"
    assert data["independent_validation"]["selected"] is None
    assert len(data["objective_results"]["rows"]) > 0
    bad = dict(manifest, protocol_hash="0" * 64)
    with pytest.raises(ValueError, match="not bound"):
        validate_engineering_evidence(bundles, protocol, ROOT, bad)
    with pytest.raises(ValueError, match="mutually exclusive"):
        comparison(bundles, scores, selection=protocol, protocol=protocol)


def test_engineering_rejects_changed_version_or_missing_run(tmp_path):
    protocol, _, _ = engineering_fixture(tmp_path)
    bundles, scores, manifest = complete_holdout_fixture(protocol)
    manifest["selection_hash"] = None
    manifest["protocol_hash"] = engineering_protocol_hash(protocol)
    with pytest.raises(ValueError, match="complete"):
        validate_engineering_evidence(bundles[:-1], protocol, ROOT, manifest)
    scores[0].scorer_version = "other-version"
    with pytest.raises(ValueError, match="scoring version changed"):
        validate_engineering_scores(bundles, scores, protocol, ROOT, manifest)


def judge_identity(name="unit-judge"):
    return ModelIdentity(requested=name, returned=name, provider="https://judge.invalid",
                         parameters={"api_style": "chat_completions", "tools": False})



def calibration_controls(tmp_path, *, wrong_pass=False):
    # Programmed fixtures test the gate only; no real judge or human result.
    from kmb.calibration import control_cases, run_calibration
    output = tmp_path / ("bad-controls" if wrong_pass else "controls")
    if output.exists():
        return output
    cases = {case.evidence.task_id: case for case in control_cases()}

    class ProgrammedJudge:
        def public_identity(self):
            return judge_identity().model_copy(update={"returned": None}).model_dump()

        async def judge(self, payload):
            case = cases[payload["task_id"]]
            criteria = []
            for criterion in payload["rubric"]:
                verdict = case.expected[criterion["criterion_id"]]
                if wrong_pass and case.id == "spoken-success-wrong-json":
                    verdict = "pass"
                criteria.append({"criterion_id": criterion["criterion_id"], "verdict": verdict,
                                 "reason": "Programmed unit-test-only output",
                                 "evidence_refs": payload["allowed_evidence_refs"][:1] if verdict != "undetermined" else []})
            return {"output": {"task_id": payload["task_id"], "criteria": criteria},
                    "model": judge_identity().model_dump(),
                    "usage": {"input_tokens": 10, "output_tokens": 2}, "elapsed_seconds": 0.01}

    asyncio.run(run_calibration(output, ROOT, ProgrammedJudge()))
    return output


def test_complete_synthetic_selection_fixture_can_freeze_once(tmp_path):
    bundles, scores, labels = complete_selection_fixture()
    result = comparison(bundles, scores, labels)
    output = tmp_path / "unit-test-choice.json"
    choice = freeze_selection(result, output, scores, ROOT / "data", calibration=calibration_controls(tmp_path))
    assert choice["selected"] == "A"
    assert choice["experiment_fingerprint"] == experiment_fingerprint(ROOT / "data")
    with pytest.raises(FileExistsError):
        freeze_selection(result, output, scores, ROOT / "data", calibration=calibration_controls(tmp_path))


@pytest.mark.parametrize("stale_fingerprint", ["", "0" * 64])
def test_freeze_rejects_missing_or_stale_scoring_fingerprint(tmp_path, stale_fingerprint):
    bundles, scores, labels = complete_selection_fixture()
    scores[0] = scores[0].model_copy(update={"scoring_fingerprint": stale_fingerprint})
    result = comparison(bundles, scores, labels)
    output = tmp_path / "rejected-choice.json"
    with pytest.raises(ValueError, match="changed since scoring"):
        freeze_selection(result, output, scores, ROOT / "data", calibration=calibration_controls(tmp_path))
    assert not output.exists()


def execution_row(agent="openclaw"):
    return {"agent": agent, "agent_version": "unit-test-only",
            "model": {"requested": "fixture", "returned": "fixture-returned", "provider": "https://example.invalid",
                      "parameters": {"transport": "chat_completions", "upstream_calls": 3,
                                     "endpoint_sha256": hashlib.sha256(b"https://example.invalid/v1").hexdigest()}},
            "environment": {"image_id": "sha256:" + ("a" if agent == "openclaw" else "b") * 64}}


@pytest.mark.parametrize("field", ["requested", "returned", "transport", "image_id", "agent_version", "provider", "temperature", "endpoint_sha256"])
def test_selection_rejects_mixed_execution_conditions(field):
    first, second = execution_row(), execution_row()
    if field in {"transport", "temperature", "endpoint_sha256"}:
        second["model"]["parameters"][field] = {"transport": "responses", "temperature": 0.7, "endpoint_sha256": "c" * 64}[field]
    elif field in {"requested", "returned", "provider"}:
        second["model"][field] = "different"
    elif field == "image_id":
        second["environment"][field] = "sha256:" + "c" * 64
    else:
        second[field] = "different-version"
    with pytest.raises(ValueError, match="inconsistent execution conditions"):
        frozen_conditions([first, second])


def test_runtime_counters_do_not_change_frozen_execution_conditions():
    first, second = execution_row(), execution_row()
    second["model"]["parameters"]["upstream_calls"] = 8
    assert frozen_conditions([first, second]) == frozen_conditions([first])
    second["model"]["returned"] = None
    with pytest.raises(ValueError, match="observed model"):
        frozen_conditions([first, second])


@pytest.fixture
def holdout_environment(monkeypatch):
    monkeypatch.setenv("KMB_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("KMB_API_KEY", "unit-test-invalid-key")
    monkeypatch.setenv("KMB_MODEL", "fixture")
    monkeypatch.setenv("KMB_API_STYLE", "chat_completions")
    monkeypatch.delenv("KMB_OPENCLAW_IMAGE", raising=False)
    monkeypatch.delenv("KMB_HERMES_IMAGE", raising=False)
    return {"execution_conditions": frozen_conditions([execution_row(), execution_row("hermes")])}


@pytest.mark.parametrize("setting,value", [("KMB_MODEL", "changed"), ("KMB_API_STYLE", "responses")])
def test_holdout_blocks_changed_model_or_protocol_before_subprocess(monkeypatch, holdout_environment, setting, value):
    monkeypatch.setenv(setting, value)

    def unexpected_process(*args, **kwargs):
        pytest.fail("changed model or protocol must be rejected before Docker is consulted")

    monkeypatch.setattr("subprocess.run", unexpected_process)
    with pytest.raises(ValueError, match="model or API protocol changed"):
        validate_holdout_conditions(holdout_environment)


def test_holdout_blocks_changed_image_without_starting_agent(monkeypatch, holdout_environment):
    calls = []

    def fake_inspect(argv, **kwargs):
        calls.append(argv)
        assert "inspect" in argv and "run" not in argv
        return SimpleNamespace(returncode=0, stdout=json.dumps("sha256:" + "c" * 64))

    monkeypatch.setattr("subprocess.run", fake_inspect)
    with pytest.raises(ValueError, match="agent image changed"):
        validate_holdout_conditions(holdout_environment)
    assert len(calls) == 1


def test_holdout_accepts_matching_model_and_local_images_without_model_call(monkeypatch, holdout_environment):
    from kmb.adapters import AGENTS

    calls = []

    def fake_inspect(argv, **kwargs):
        calls.append(argv)
        image = argv[argv.index("inspect") + 1]
        agent = next(name for name, tag in AGENTS.items() if tag == image)
        return SimpleNamespace(returncode=0, stdout=json.dumps(
            holdout_environment["execution_conditions"][agent]["image_id"]))

    monkeypatch.setattr("subprocess.run", fake_inspect)
    validate_holdout_conditions(holdout_environment)
    assert len(calls) == 2


def test_export_binds_human_review_to_rubric(tmp_path):
    directory, item, _ = exported(tmp_path)
    record_label(directory, "Alice", item, {"c1": "pass"}, "Synthetic test judgment")
    record_label(directory, "Bob", item, {"c1": "pass"}, "Synthetic test judgment")
    label = consensus(directory)["labels"]["unit-run"]
    assert label["rubric_hash"] == rubric_hash(rubric())
    changed = rubric().model_copy(deep=True)
    changed.criteria[0].expected = "other"
    assert label["rubric_hash"] != rubric_hash(changed)


def test_freeze_rejects_missing_comparison_variant(tmp_path):
    bundles, scores, labels = complete_selection_fixture()
    scores = [item for item in scores if item.scorer != "B"]
    with pytest.raises(ValueError, match="A/B/C scoring result"):
        freeze_selection(comparison(bundles, scores, labels), tmp_path / "choice.json", scores, ROOT)


def test_freeze_rejects_human_labels_for_changed_rubric(tmp_path):
    bundles, scores, labels = complete_selection_fixture()
    next(iter(labels["labels"].values()))["rubric_hash"] = "0" * 64
    with pytest.raises(ValueError, match="human review rubric changed"):
        freeze_selection(comparison(bundles, scores, labels), tmp_path / "choice.json", scores, ROOT)


@pytest.mark.parametrize("field", ["requested", "returned", "provider", "api_style", "temperature", "version"])
def test_freeze_rejects_mixed_judges_even_when_another_variant_wins(tmp_path, field):
    bundles, scores, labels = complete_selection_fixture()
    changed = next(s for s in scores if s.scorer == "B")
    if field == "version":
        changed.scorer_version = "different"
    elif field == "api_style":
        changed.model.parameters[field] = "responses"
    elif field == "temperature":
        changed.model.parameters[field] = 0.4
    else:
        setattr(changed.model, field, "different-judge")
    with pytest.raises(ValueError, match="inconsistent (judge|scorer)"):
        freeze_selection(comparison(bundles, scores, labels), tmp_path / "rejected.json", scores, ROOT)
    assert not (tmp_path / "rejected.json").exists()


def test_successful_model_judge_with_unobserved_identity_cannot_freeze(tmp_path):
    bundles, scores, labels = complete_selection_fixture()
    next(s for s in scores if s.scorer == "B").model.returned = None
    with pytest.raises(ValueError, match="observed judge identity"):
        freeze_selection(comparison(bundles, scores, labels), tmp_path / "rejected.json", scores, ROOT)


def test_unconfigured_failed_candidate_does_not_block_valid_rule_selection(tmp_path):
    bundles, scores, labels = complete_selection_fixture()
    for s in scores:
        if s.scorer == "B":
            s.status, s.error, s.verdict = "error", "judge_not_configured", "undetermined"
            s.model = ModelIdentity()
    chosen = freeze_selection(comparison(bundles, scores, labels), tmp_path / "choice.json", scores, ROOT, calibration=calibration_controls(tmp_path))
    assert chosen["selected"] == "A"
    assert chosen["scorer_conditions"]["B"]["mode"] == "unavailable"


def frozen_model_choice(tmp_path):
    bundles, scores, labels = complete_selection_fixture()
    for s in scores:
        if s.scorer != "B":
            s.verdict = "fail"
            for c in s.criteria:
                c.verdict = "fail"
    chosen = freeze_selection(comparison(bundles, scores, labels), tmp_path / "selection.json", scores, ROOT, calibration=calibration_controls(tmp_path))
    assert chosen["selected"] == "B"
    return chosen


def complete_holdout_fixture(chosen):
    # Entirely synthetic policy fixtures; these never stand in for real holdout runs.
    bundles, scores = [], []
    for task, requirements in load_dataset(ROOT, "holdout"):
        for agent in ("openclaw", "hermes"):
            condition = chosen["execution_conditions"][agent]
            for repeat in range(3):
                bundle = evidence(f"test-{task.id}-{agent}-{repeat}", task_id=task.id,
                                  ability=task.ability, family=task.family, split="holdout",
                                  provenance="real", agent=agent, agent_version=condition["agent_version"],
                                  environment={"image_id": condition["image_id"]},
                                  model=ModelIdentity(requested=condition["requested"], returned=condition["returned"],
                                                      provider=condition["provider"], parameters=dict(condition["parameters"])))
                bundles.append(bundle)
                for variant in "ABC":
                    criteria = [CriterionScore(criterion_id=c.id, verdict="pass", reason="synthetic unit fixture",
                                               critical=c.critical) for c in requirements.criteria]
                    scored = automatic(bundle, variant=variant, criteria=criteria,
                                       scoring_fingerprint=scoring_fingerprint(requirements))
                    if variant == "B" or (variant == "C" and any(c.kind == "semantic" for c in requirements.criteria)):
                        scored.model = judge_identity()
                    scores.append(scored)
    manifest = {"split": "holdout", "provenance": "real", "repeats": 3,
                "agents": ["openclaw", "hermes"], "tasks": sorted({b.task_id for b in bundles}),
                "selection_hash": selection_manifest_hash(chosen)}
    return bundles, scores, manifest


@pytest.mark.parametrize("change", ["manifest", "matrix", "selected_variant", "returned_model", "parameters", "fingerprint"])
def test_holdout_rejects_unbound_or_changed_scoring_protocol(tmp_path, change):
    chosen = frozen_model_choice(tmp_path)
    bundles, scores, manifest = complete_holdout_fixture(chosen)
    selected_score = next(s for s in scores if s.scorer == "B")
    if change == "manifest":
        manifest["selection_hash"] = "0" * 64
    elif change == "matrix":
        bundles.pop()
    elif change == "selected_variant":
        scores = [s for s in scores if s.scorer != "B"]
    elif change == "returned_model":
        selected_score.model.returned = "changed-model"
    elif change == "parameters":
        selected_score.model.parameters["tools"] = True
    else:
        selected_score.scoring_fingerprint = "0" * 64
    with pytest.raises(ValueError):
        validate_holdout_scores(bundles, scores, chosen, ROOT, manifest)


def test_holdout_report_requires_selection_and_keeps_abc_as_exploratory(tmp_path):
    from kmb.report import render_report

    chosen = frozen_model_choice(tmp_path)
    bundles, scores, manifest = complete_holdout_fixture(chosen)
    with pytest.raises(ValueError, match="requires a frozen selection"):
        render_report(bundles, scores, tmp_path / "unbound")
    assert not (tmp_path / "unbound").exists()
    data = comparison(bundles, scores, selection=chosen, dataset_root=ROOT, holdout_manifest=manifest)
    assert data["independent_validation"]["eligible"] is True
    assert {v["variant"]: v["evaluation_role"] for v in data["variants"]} == {
        "A": "exploratory", "B": "frozen_candidate", "C": "exploratory"}
    page = render_report(bundles, scores, tmp_path / "bound", selection=chosen,
                         dataset_root=ROOT, holdout_manifest=manifest).read_text()
    assert "冻结版本 B" in page and "其他版本仅作探索展示" in page
    assert all(json.dumps(f"openclaw · {v}") in page for v in "ABC")


@pytest.mark.parametrize("status", ["infrastructure_error", "execution_unknown"])
@pytest.mark.parametrize("missing", ["returned", "startup", "request_prefix"])
def test_holdout_failed_execution_with_missing_observations_can_report_incomplete(
    tmp_path, status, missing,
):
    from kmb.report import render_report

    chosen = frozen_model_choice(tmp_path)
    request_sets = [{"temperature": 0.2}, {"temperature": 0.2, "max_tokens": 100}]
    for condition in chosen["execution_conditions"].values():
        condition["parameters"]["requested_inference_parameters"] = request_sets
    bundles, scores, manifest = complete_holdout_fixture(chosen)
    failed = bundles[0]
    failed.status, failed.error = status, "model_gateway_upstream_error"
    failed.model.returned = None
    failed.model.parameters["returned_models"] = []
    if missing == "startup":
        failed.agent_version = "unknown"
        failed.environment["image_id"] = "unknown"
        failed.model.parameters["requested_inference_parameters"] = []
    elif missing == "request_prefix":
        failed.model.parameters["requested_inference_parameters"] = request_sets[:1]
    failed.freeze()
    for scored in scores:
        if scored.run_id == failed.run_id:
            scored.evidence_hash = failed.evidence_hash
            scored.status, scored.error, scored.verdict = "not_scored", status, "undetermined"
            scored.criteria, scored.model = [], ModelIdentity()
    validation = validate_holdout_scores(bundles, scores, chosen, ROOT, manifest)
    assert validation["status"] == "incomplete"
    assert validation["eligible"] is False
    assert validation["execution_errors"] == validation["selected_scoring_errors"] == 1
    data = comparison(bundles, scores, selection=chosen, dataset_root=ROOT, holdout_manifest=manifest)
    assert data["total_runs"] == 108 and data["valid_runs"] == 107
    assert data["infrastructure_or_unknown_runs"] == 1
    page = render_report(bundles, scores, tmp_path / "incomplete-report", selection=chosen,
                         dataset_root=ROOT, holdout_manifest=manifest).read_text()
    assert "验证未完成" in page
    assert failed.model.returned is None  # Never fill missing observations with the expected model.
    failed.verify()


@pytest.mark.parametrize("field", ["requested", "returned", "provider", "image_id", "agent_version",
                                   "endpoint_sha256", "transport", "requested_inference_parameters",
                                   "returned_models"])
def test_incomplete_holdout_still_rejects_every_known_changed_execution_condition(tmp_path, field):
    chosen = frozen_model_choice(tmp_path)
    for condition in chosen["execution_conditions"].values():
        condition["parameters"]["requested_inference_parameters"] = [{"temperature": 0.2}]
    bundles, scores, manifest = complete_holdout_fixture(chosen)
    failed = bundles[0]
    failed.status, failed.error = "infrastructure_error", "model_gateway_upstream_error"
    failed.model.returned = None
    if field in {"requested", "returned", "provider"}:
        setattr(failed.model, field, "different-known-value")
    elif field == "image_id":
        failed.environment[field] = "sha256:" + "f" * 64
    elif field == "agent_version":
        failed.agent_version = "different-version"
    elif field == "requested_inference_parameters":
        failed.model.parameters[field] = [{"temperature": 0.7}]
    elif field == "returned_models":
        failed.model.parameters[field] = [chosen["execution_conditions"][failed.agent]["returned"],
                                          "different-model"]
    else:
        failed.model.parameters[field] = "responses" if field == "transport" else "f" * 64
    failed.freeze()
    with pytest.raises(ValueError, match="observed holdout conditions differ"):
        validate_holdout_scores(bundles, scores, chosen, ROOT, manifest)


@pytest.mark.parametrize("status", ["completed", "task_failed", "budget_exhausted"])
def test_scorable_holdout_still_requires_observed_returned_model(tmp_path, status):
    chosen = frozen_model_choice(tmp_path)
    bundles, scores, manifest = complete_holdout_fixture(chosen)
    bundles[0].status = status
    bundles[0].model.returned = None
    bundles[0].freeze()
    with pytest.raises(ValueError, match="requires observed model"):
        validate_holdout_scores(bundles, scores, chosen, ROOT, manifest)


def test_explicit_holdout_judge_failure_is_retained_without_validation_eligibility(tmp_path):
    chosen = frozen_model_choice(tmp_path)
    bundles, scores, manifest = complete_holdout_fixture(chosen)
    failed = next(s for s in scores if s.scorer == "B")
    failed.status, failed.error, failed.verdict = "error", "provider_http_error", "undetermined"
    failed.model.returned = None
    data = comparison(bundles, scores, selection=chosen, dataset_root=ROOT, holdout_manifest=manifest)
    assert data["independent_validation"]["status"] == "incomplete"
    assert data["independent_validation"]["eligible"] is False
    assert data["independent_validation"]["selected_scoring_errors"] == 1


def test_holdout_changed_provider_is_rejected_before_scoring(tmp_path, monkeypatch):
    import asyncio

    from kmb.cli import score_batch

    chosen = frozen_model_choice(tmp_path)
    bundles, _, manifest = complete_holdout_fixture(chosen)
    monkeypatch.setattr("kmb.cli.load_evidence_tree", lambda _: bundles)
    monkeypatch.setattr("kmb.cli.read_holdout_manifest", lambda _: manifest)
    monkeypatch.setattr("kmb.cli.configured_provider", lambda: SimpleNamespace(
        public_identity=lambda: judge_identity("other-judge").model_dump()))

    def forbidden(*args, **kwargs):
        pytest.fail("a changed judge must be rejected before any scoring call")

    monkeypatch.setattr("kmb.scorers.score", forbidden)
    with pytest.raises(ValueError, match="configuration changed"):
        asyncio.run(score_batch(tmp_path / "evidence", tmp_path / "scores", ROOT, "all", selection=chosen))
    assert not (tmp_path / "scores").exists()


def test_holdout_score_without_selection_stops_before_provider_lookup(tmp_path, monkeypatch):
    import asyncio

    from kmb.cli import score_batch

    monkeypatch.setattr("kmb.cli.load_evidence_tree", lambda _: [evidence(provenance="real", split="holdout")])
    monkeypatch.setattr("kmb.cli.configured_provider", lambda: pytest.fail("must reject before configuration lookup"))
    with pytest.raises(ValueError, match="requires --selection"):
        asyncio.run(score_batch(tmp_path / "evidence", tmp_path / "scores", ROOT, "all"))
    assert not (tmp_path / "scores").exists()


def test_holdout_provider_accepts_exact_frozen_configuration(tmp_path):
    chosen = frozen_model_choice(tmp_path)
    provider = SimpleNamespace(public_identity=lambda: judge_identity().model_dump())
    validate_holdout_provider(chosen, provider)


def test_holdout_score_all_preserves_every_variant_and_binding(tmp_path, monkeypatch):
    import asyncio

    from kmb.cli import score_batch
    from kmb.storage import load_scores

    chosen = frozen_model_choice(tmp_path)
    bundles, scores, manifest = complete_holdout_fixture(chosen)
    expected = {(s.run_id, s.scorer): s for s in scores}
    monkeypatch.setattr("kmb.cli.load_evidence_tree", lambda _: bundles)
    monkeypatch.setattr("kmb.cli.read_holdout_manifest", lambda _: manifest)
    monkeypatch.setattr("kmb.cli.configured_provider", lambda: SimpleNamespace(
        public_identity=lambda: judge_identity().model_dump()))

    async def offline_score(bundle, requirements, variant, provider):
        assert requirements.task_id == bundle.task_id
        return expected[(bundle.run_id, variant)].model_copy(deep=True)

    monkeypatch.setattr("kmb.scorers.score", offline_score)
    output = tmp_path / "synthetic-scores"
    result = asyncio.run(score_batch(tmp_path / "evidence", output, ROOT, "all", selection=chosen))
    saved = load_scores(output)
    assert result["scores"] == 324 and len(saved) == 324
    assert {(s.run_id, s.scorer) for s in saved} == set(expected)
    assert result["independent_validation"]["eligible"] is True
    assert result["independent_validation"]["selected"] == "B"
    assert result["independent_validation"]["selection_hash"] == selection_manifest_hash(chosen)


@pytest.mark.parametrize("criterion_verdict,aggregate", [
    ("fail", "pass"), ("undetermined", "pass"), ("pass", "fail"), ("pass", None),
])
def test_comparison_rejects_human_aggregate_that_contradicts_criteria(criterion_verdict, aggregate):
    bundle = evidence()
    labels = human(bundle)
    labels["labels"][bundle.run_id]["criteria"] = {"c1": criterion_verdict}
    labels["labels"][bundle.run_id]["verdict"] = aggregate
    # Previously fail/pass could produce human_agreement=1 and meets_internal_target=True.
    with pytest.raises(ValueError, match="human aggregate verdict contradicts"):
        comparison([bundle], [automatic(bundle)], labels)


@pytest.mark.parametrize("invalid", ["missing", "extra", "invalid_enum", "declared_set", "wrong_task"])
def test_comparison_validates_human_criterion_set_and_identity(invalid):
    bundle = evidence()
    labels = human(bundle)
    label = labels["labels"][bundle.run_id]
    if invalid == "missing":
        label["criteria"] = {}
    elif invalid == "extra":
        label["criteria"]["not-in-rubric"] = "pass"
    elif invalid == "invalid_enum":
        label["criteria"]["c1"] = "probably_pass"
    elif invalid == "declared_set":
        label["criterion_ids"] = ["not-in-rubric"]
    else:
        label["task_id"] = "different-task"
    with pytest.raises(ValueError, match="human"):
        comparison([bundle], [automatic(bundle)], labels)


def test_human_labels_cannot_omit_a_criterion_from_multi_criterion_score():
    bundle = evidence()
    scored = automatic(bundle, criteria=[
        CriterionScore(criterion_id="c1", verdict="pass", reason="synthetic"),
        CriterionScore(criterion_id="c2", verdict="pass", reason="synthetic"),
    ])
    with pytest.raises(ValueError, match="cover the rubric criterion set exactly"):
        comparison([bundle], [scored], human(bundle))


def test_inconsistent_human_labels_cannot_reach_selection_freeze(tmp_path):
    bundles, scores, labels = complete_selection_fixture()
    first = labels["labels"][bundles[0].run_id]
    first["criteria"][next(iter(first["criteria"]))] = "fail"
    assert first["verdict"] == "pass"
    output = tmp_path / "must-not-freeze.json"
    with pytest.raises(ValueError, match="human aggregate verdict contradicts"):
        result = comparison(bundles, scores, labels, dataset_root=ROOT)
        freeze_selection(result, output, scores, ROOT)
    assert not output.exists()


def test_complete_human_labels_without_automatic_scores_can_be_inspected():
    bundle = evidence()
    labels = human(bundle)
    labels["labels"][bundle.run_id]["criterion_ids"] = ["c1"]
    result = comparison([bundle], [], labels)
    assert result["human_review"] == "available"
    assert all(v["human_agreement"] is None for v in result["variants"])


@pytest.mark.parametrize("url", ["https://elsewhere.invalid/v1", "https://example.invalid/alternate/v1"])
def test_holdout_rejects_changed_endpoint_before_docker(monkeypatch, holdout_environment, url):
    monkeypatch.setenv("KMB_BASE_URL", url)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("must reject before Docker"))
    with pytest.raises(ValueError, match="provider endpoint changed"):
        validate_holdout_conditions(holdout_environment)


def test_selection_rejects_unobserved_provider_and_endpoint():
    first = execution_row()
    first["model"]["provider"] = "configured_host_gateway"
    with pytest.raises(ValueError, match="observed model"):
        frozen_conditions([first])
    first["model"]["provider"] = "https://example.invalid"
    first["model"]["parameters"].pop("endpoint_sha256")
    with pytest.raises(ValueError, match="endpoint fingerprint"):
        frozen_conditions([first])


def test_freeze_requires_actual_control_records(tmp_path):
    bundles, scores, labels = complete_selection_fixture()
    with pytest.raises(ValueError, match="requires preregistered scorer calibration"):
        freeze_selection(comparison(bundles, scores, labels), tmp_path / "choice.json", scores, ROOT)
    assert not (tmp_path / "choice.json").exists()


def test_failed_model_calibration_excludes_candidate_even_with_perfect_human_agreement(tmp_path):
    bundles, scores, labels = complete_selection_fixture()
    controls = calibration_controls(tmp_path, wrong_pass=True)
    chosen = freeze_selection(comparison(bundles, scores, labels), tmp_path / "choice.json", scores,
                              ROOT, calibration=controls)
    assert chosen["selected"] == "A"
    assert "B" in chosen["calibration_rejections"]
    assert chosen["calibration"]["selected"] == "A"


def test_only_human_eligible_candidate_must_still_pass_controls(tmp_path):
    bundles, scores, labels = complete_selection_fixture()
    for scored in scores:
        if scored.scorer != "B":
            scored.verdict = "fail"
            for criterion in scored.criteria:
                criterion.verdict = "fail"
    with pytest.raises(ValueError, match="no eligible scorer passed"):
        freeze_selection(comparison(bundles, scores, labels), tmp_path / "choice.json", scores,
                         ROOT, calibration=calibration_controls(tmp_path, wrong_pass=True))


@pytest.mark.parametrize("change", ["missing", "variant", "suite", "false_pass", "incomplete", "count", "model", "hash"])
def test_holdout_rejects_missing_or_changed_calibration_binding(tmp_path, change):
    from kmb.review import validate_selection_manifest
    chosen = frozen_model_choice(tmp_path)
    if change == "missing":
        chosen.pop("calibration")
    elif change == "variant":
        chosen["calibration"]["selected"] = "A"
    elif change == "suite":
        chosen["calibration"]["suite_hash"] = "0" * 64
    elif change == "false_pass":
        chosen["calibration"]["critical_false_passes"] = 1
    elif change == "count":
        chosen["calibration"]["critical_criteria"] = 1
        chosen["calibration"]["critical_expected_matches"] = 1
    elif change == "model":
        chosen["scorer_conditions"]["B"]["judge"]["requested"] = "uncalibrated-model"
        chosen["scorer_conditions"]["B"]["judge"]["returned"] = "uncalibrated-model"
    elif change == "hash":
        chosen["calibration"]["scoring_records_hash"] = "Z" * 64
    else:
        chosen["calibration"]["critical_expected_matches"] = 0
    with pytest.raises(ValueError, match="calibration binding"):
        validate_selection_manifest(chosen, ROOT)


def test_report_rejects_nonexistent_evidence_reference():
    bundle = evidence()
    scored = automatic(bundle)
    scored.criteria[0].evidence_refs = ["event:missing"]
    with pytest.raises(ValueError, match="unknown evidence reference"):
        comparison([bundle], [scored])


def test_freeze_rejects_altered_critical_flags(tmp_path):
    bundles, scores, labels = complete_selection_fixture()
    critical = next(c for scored in scores for c in scored.criteria if c.critical)
    critical.critical = False
    with pytest.raises(ValueError, match="critical flags differ"):
        freeze_selection(comparison(bundles, scores, labels), tmp_path / "choice.json", scores, ROOT)
