"""Calibration plumbing and adversarial controls, using no real model or agent."""
import asyncio
import json
import runpy
from pathlib import Path

import httpx
import pytest

from kmb.calibration import (
    control_cases,
    run_calibration,
    suite_hash,
    summarize,
    validate_calibration,
)
from kmb.provider import JudgeProvider
from kmb.storage import load_evidence_tree, load_scores

DATASET = Path(__file__).parents[1] / "data"


def test_control_suite_is_deterministic_frozen_and_distinct_from_agent_performance():
    first, second = control_cases(), control_cases()
    assert len(first) == 8
    assert suite_hash(first) == suite_hash(second)
    assert len({case.id for case in first}) == 8
    for index, case in enumerate(first, start=1):
        case.evidence.verify()
        assert case.evidence.task_id == case.evidence.run_id == f"control-{index:03d}"
        assert case.rubric.task_id == case.evidence.task_id
        assert case.evidence.agent == "designed-control"
        assert case.evidence.provenance == "simulated"
        assert case.evidence.environment["not_agent_performance"] == "true"
        assert case.document()["expectation_source"] == "designed_control_expectation_not_human_review"
    first[0].expected["route"] = "pass"
    assert suite_hash(first) != suite_hash(second)


def test_no_model_mode_retains_unknown_and_failures_instead_of_fake_judging(tmp_path, monkeypatch):
    monkeypatch.setattr(JudgeProvider, "from_env", lambda: pytest.fail("ambient model access"))
    output = tmp_path / "calibration"
    result = asyncio.run(run_calibration(output, DATASET))
    assert result["formal_agent_benchmark"] is False
    assert result["model_requests_enabled"] is False
    assert result["configured_model"] is None
    assert result["provenance"] == "simulated"
    assert result["protocol_unchanged"] and result["suite_unchanged"]
    assert result["variants"]["A"]["expected_match"] == 7
    assert result["variants"]["A"]["critical_controls_all_match"] is True
    assert result["variants"]["B"]["error_count"] == 8
    assert result["variants"]["B"]["critical_controls_all_match"] is False
    assert result["variants"]["B"]["model_cases_with_returned_identity"] == 0
    assert result["variants"]["C"]["error_count"] == 2
    assert len(load_evidence_tree(output / "controls")) == 8
    assert len(load_scores(output / "scores")) == 24
    before = {str(path.relative_to(output)): path.read_bytes()
              for path in output.rglob("*") if path.is_file()}
    with pytest.raises(FileExistsError):
        asyncio.run(run_calibration(output, DATASET))
    after = {str(path.relative_to(output)): path.read_bytes()
             for path in output.rglob("*") if path.is_file()}
    assert before == after


def mock_judge(output: Path, *, wrong_pass=False, malformed_case=None, observed=None):
    """Test-only programmed judge; never part of the production calibration path."""
    cases = {case.evidence.task_id: case for case in control_cases()}

    def handler(request):
        assert (output / "suite.json").exists()
        frozen = load_evidence_tree(output / "controls")
        assert len(frozen) == len(cases)  # Freeze all expectations before the first model request.
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert "expected_criteria" not in payload
        assert "expectation_source" not in payload
        assert "designed_control_expectation" not in json.dumps(payload)
        serialized = json.dumps(payload, ensure_ascii=False)
        for control in cases.values():
            assert control.id not in serialized
            assert control.description not in serialized
        case = cases[payload["task_id"]]
        if observed is not None:
            observed.append(payload["task_id"])
        if case.id == malformed_case:
            content = "Deliberately invalid test output; never repair this to a pass."
        else:
            criteria = []
            for criterion in payload["rubric"]:
                verdict = case.expected[criterion["criterion_id"]]
                if wrong_pass and (criterion["criterion_id"] == "route"
                                   or case.id == "incomplete-missing-directory"):
                    verdict = "pass"
                criteria.append({"criterion_id": criterion["criterion_id"], "verdict": verdict,
                                 "reason": "Programmed mock response for plumbing tests only.",
                                 "evidence_refs": payload["allowed_evidence_refs"][:1]
                                 if verdict != "undetermined" else []})
            content = json.dumps({"task_id": payload["task_id"], "criteria": criteria})
        return httpx.Response(200, json={"model": "test-mock-judge", "usage": {
            "prompt_tokens": 12, "completion_tokens": 4}, "choices": [{
                "message": {"content": content}, "finish_reason": "stop"}]})

    return JudgeProvider("https://mock-only.test/private/v1", "mock-secret-key", "test-mock-judge",
                         transport=httpx.MockTransport(handler))


def test_mock_calibration_shares_frozen_evidence_and_detects_both_kinds_of_false_pass(tmp_path):
    output = tmp_path / "mock-calibration"
    observed = []
    result = asyncio.run(run_calibration(output, DATASET, mock_judge(
        output, wrong_pass=True, observed=observed)))
    assert len(observed) == 10  # Eight B calls and two C semantic calls, no agent execution.
    variant = result["variants"]["B"]
    assert variant["critical_false_passes"] == 1
    assert variant["critical_unsupported_passes"] == 1
    assert variant["expected_match"] == 6
    assert variant["critical_controls_all_match"] is False
    assert variant["error_count"] == 0
    assert variant["model_cases_required"] == variant["model_cases_with_returned_identity"] == 8
    assert variant["usage"] == {"input_tokens": 96, "output_tokens": 32}
    assert result["variants"]["C"]["expected_match"] == 8
    for case in control_cases():
        frozen = next(e for e in load_evidence_tree(output / "controls")
                      if e.run_id == case.evidence.run_id)
        assert frozen.evidence_hash == case.evidence.evidence_hash
    serialized = json.dumps(result)
    assert "mock-secret-key" not in serialized and "/private/v1" not in serialized
    assert len(result["configured_model"]["parameters"]["endpoint_sha256"]) == 64


def test_invalid_model_response_is_preserved_as_error_not_substituted_by_rules(tmp_path):
    output = tmp_path / "invalid-model"
    result = asyncio.run(run_calibration(output, DATASET, mock_judge(
        output, malformed_case="spoken-success-wrong-json")))
    variant = result["variants"]["B"]
    assert variant["error_count"] == 1
    assert variant["errors"][0]["error"] == "invalid_judge_json"
    assert variant["critical_false_passes"] == 0
    assert variant["critical_controls_all_match"] is False
    target = next(case.evidence.task_id for case in control_cases()
                  if case.id == "spoken-success-wrong-json")
    saved = next(item for item in load_scores(output / "scores")
                 if item.scorer == "B" and item.task_id == target)
    assert saved.status == "error" and saved.verdict == "undetermined"
    assert saved.components["judge"]["receipt"]["diagnostics"]["content_chars"] > 0
    assert saved.usage.input_tokens == 12


def test_judge_payload_contains_rubric_targets_but_not_control_names_or_expected_decisions():
    from kmb.scorers import _payload

    cases = control_cases()
    for case in cases:
        payload = _payload(case.evidence, case.rubric.criteria)
        serialized = json.dumps(payload, ensure_ascii=False)
        assert payload["task_id"] == case.evidence.task_id
        assert "case_id" not in payload and "description" not in payload
        assert "expected_criteria" not in serialized
        assert "expectation_source" not in serialized
        for control in cases:
            assert control.id not in serialized
            assert control.description not in serialized
        # The task's target is required for judging; the case's desired verdict is not.
        assert {row["criterion_id"]: row["expected"] for row in payload["rubric"]} == {
            criterion.id: criterion.expected for criterion in case.rubric.criteria}
        document = case.document()
        assert document["case_id"] == case.id
        assert document["description"] == case.description
        assert document["expected_criteria"] == case.expected


def test_summary_rejects_mixed_or_missing_control_records(tmp_path):
    output = tmp_path / "source"
    asyncio.run(run_calibration(output, DATASET))
    records = load_scores(output / "scores")
    with pytest.raises(ValueError, match="one A/B/C"):
        summarize(control_cases(), records[:-1])
    with pytest.raises(ValueError, match="duplicate calibration score"):
        summarize(control_cases(), records + records[:1])
    records[0].evidence_hash = "not-the-control-hash"
    with pytest.raises(ValueError, match="frozen control evidence"):
        summarize(control_cases(), records)


def test_protocol_change_during_calibration_is_marked_ineligible_for_gate(tmp_path, monkeypatch):
    values = iter(["before-code-hash", "after-code-hash"])
    monkeypatch.setattr("kmb.calibration.experiment_fingerprint", lambda _: next(values))
    result = asyncio.run(run_calibration(tmp_path / "changed", DATASET))
    assert result["protocol_unchanged"] is False
    assert result["experiment_fingerprint"] == "before-code-hash"
    assert result["experiment_fingerprint_after"] == "after-code-hash"


def test_script_defaults_to_no_model_and_rejects_overwrite(tmp_path, monkeypatch, capsys):
    script = runpy.run_path(str(Path(__file__).parents[1] / "scripts/calibrate-scorers.py"))
    monkeypatch.setattr(JudgeProvider, "from_env", lambda: pytest.fail("implicit model call"))
    monkeypatch.setattr("sys.argv", ["calibrate-scorers.py", "--output", str(tmp_path / "script"),
                                     "--dataset", str(DATASET)])
    assert script["main"]() == 0
    assert json.loads(capsys.readouterr().out)["model_requests_enabled"] is False
    with pytest.raises(ValueError, match="already exists"):
        script["main"]()


def test_self_report_and_proposed_tool_call_do_not_count_as_an_observed_action(tmp_path):
    result = asyncio.run(run_calibration(tmp_path / "actions", DATASET))
    for variant in "AC":
        control = next(item for item in result["variants"][variant]["observations"]
                       if item["case_id"] == "spoken-success-wrong-json")
        assert control["actual"]["trusted-write"] == "undetermined"
    body = JudgeProvider("https://mock-only.test/v1", "test-secret", "test-model")._request_body({})
    prompt = body["messages"][0]["content"]
    assert "native_tool_report and native_session are claims" in prompt
    assert "not independent proof" in prompt
    assert "Model tool calls\nare proposals, not executed actions" in prompt


def test_validation_recomputes_and_binds_selected_candidate_without_paths(tmp_path):
    output = tmp_path / "eligible"
    result = asyncio.run(run_calibration(output, DATASET, mock_judge(output)))
    expected_judge = result["variants"]["B"]["models"][0]
    for variant in "ABC":
        binding = validate_calibration(output, DATASET, variant,
                                       None if variant == "A" else expected_judge)
        assert binding["selected"] == variant
        assert binding["suite_hash"] == result["suite_hash"]
        assert len(binding["scoring_records_hash"]) == 64
        assert binding["critical_expected_matches"] == binding["critical_criteria"] == 7
        assert binding["critical_false_passes"] == binding["critical_unsupported_passes"] == 0
        assert str(tmp_path) not in json.dumps(binding)
    with pytest.raises(ValueError, match="incomplete"):
        validate_calibration(output, DATASET, "B")
    changed_judge = json.loads(json.dumps(expected_judge))
    changed_judge["parameters"]["endpoint_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="differs"):
        validate_calibration(output, DATASET, "B", changed_judge)


@pytest.mark.parametrize("tamper", ["summary", "score_fingerprint", "score_reference", "control", "suite"])
def test_validation_rejects_tampered_records_not_just_bad_summary(tmp_path, tamper):
    output = tmp_path / "tampered"
    asyncio.run(run_calibration(output, DATASET))
    if tamper == "summary":
        path = output / "summary.json"
        document = json.loads(path.read_text())
        document["variants"]["A"]["expected_match"] = 8
    elif tamper.startswith("score_"):
        path = next((output / "scores").rglob("A-*.json"))
        document = json.loads(path.read_text())
        if tamper == "score_fingerprint":
            document["scoring_fingerprint"] = "forged-code-hash"
        else:
            document["criteria"][0]["evidence_refs"] = ["event:nonexistent"]
    elif tamper == "control":
        path = next((output / "controls").rglob("evidence.json"))
        document = json.loads(path.read_text())
        document["files_after"]["unexpected.txt"] = "tampered"
    else:
        path = output / "suite.json"
        document = json.loads(path.read_text())
        document["cases"][0]["expected_criteria"]["route"] = "pass"
    path.chmod(0o644)
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        validate_calibration(output, DATASET, "A")


def test_zero_false_passes_with_missing_model_is_not_a_model_calibration(tmp_path):
    output = tmp_path / "no-model"
    result = asyncio.run(run_calibration(output, DATASET))
    assert result["variants"]["B"]["critical_false_passes"] == 0
    assert validate_calibration(output, DATASET, "A")["selected"] == "A"
    for variant in "BC":
        with pytest.raises(ValueError, match="failed or abstained"):
            validate_calibration(output, DATASET, variant, {"requested": "test-model"})


def test_records_recomputed_with_a_critical_false_pass_cannot_be_made_eligible_by_summary(tmp_path):
    output = tmp_path / "false-pass"
    result = asyncio.run(run_calibration(output, DATASET, mock_judge(output, wrong_pass=True)))
    expected_judge = result["variants"]["B"]["models"][0]
    with pytest.raises(ValueError, match="critical calibration"):
        validate_calibration(output, DATASET, "B", expected_judge)
    # Correct A and C are independently eligible; a bad B is not hidden or substituted.
    assert validate_calibration(output, DATASET, "A")["selected"] == "A"
    assert validate_calibration(output, DATASET, "C", expected_judge)["selected"] == "C"
