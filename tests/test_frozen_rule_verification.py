"""Fabricated policy fixtures only; no test record is genuine agent evidence."""
from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import json
import shutil
import socket
import subprocess
from pathlib import Path

import httpx
import pytest

from kmb import cli, provider, storage
from kmb.calibration import control_cases, suite_hash
from kmb.dataset import load_dataset
from kmb.models import EvidenceBundle, ModelIdentity, ScoreResult
from kmb.review import engineering_protocol_hash, experiment_fingerprint, frozen_conditions
from kmb.scorers import score

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("frozen_rules", PROJECT / "scripts/verify-frozen-rules.py")
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def argv(args):
    return [part for name in ("dataset", "evidence", "scores", "protocol", "output")
            for part in (f"--{name}", str(getattr(args, name)))]


@pytest.fixture
def frozen(tmp_path):
    dataset = tmp_path / "dataset"
    for name in ("tasks", "rubrics"):
        shutil.copytree(PROJECT / "data" / name, dataset / name)
    runs, scores = tmp_path / "runs", tmp_path / "scores"
    runs.mkdir()
    fingerprint = experiment_fingerprint(dataset)
    tasks = {split: sorted(t.id for t, _ in load_dataset(dataset, split))
             for split in ("selection", "holdout")}
    judge = ModelIdentity(requested="fabricated-judge", returned="fabricated-judge",
                          provider="https://fixture.invalid",
                          parameters={"api_style": "chat_completions", "tools": False})
    conditions = {}
    for agent in ("openclaw", "hermes"):
        model = ModelIdentity(requested="fabricated-subject", returned="fabricated-subject",
                              provider="https://fixture.invalid",
                              parameters={"transport": "chat_completions", "endpoint_sha256": "0" * 64,
                                          "max_tool_actions": 20, "timeout_seconds": 600})
        environment = {"image_id": f"fixture-{agent}"}
        conditions.update(frozen_conditions([{"agent": agent, "agent_version": "test-only",
                                              "model": model.model_dump(), "environment": environment}]))
        for task, rubric in load_dataset(dataset, "holdout"):
            for repeat in range(3):
                bundle = EvidenceBundle(run_id=f"fixture-{agent}-{task.id}-{repeat}",
                    task_id=task.id, ability=task.ability, family=task.family, split="holdout",
                    provenance="real", agent=agent, agent_version="test-only", model=model,
                    environment=environment, status="budget_exhausted" if agent == "hermes"
                    and task.id == tasks["holdout"][-1] and repeat == 2 else "completed").freeze()
                storage.save_evidence(bundle, runs / agent / task.id / str(repeat))
                original = asyncio.run(score(bundle, rubric, "A", provider=None))
                for variant in "ABC":
                    item = original.model_copy(deep=True, update={"scorer": variant})
                    if variant != "A":
                        item.model = judge
                    storage.save_score(item, scores)
    control_hash = suite_hash(control_cases())
    protocol = {"schema_version": "1", "protocol_type": "engineering_validation",
        "human_validation": "not_performed", "selected_scorer": None,
        "experiment_fingerprint": fingerprint,
        "selection_matrix": {"split": "selection", "tasks": tasks["selection"],
                             "agents": ["openclaw", "hermes"], "repeats": 1},
        "holdout_matrix": {"split": "holdout", "tasks": tasks["holdout"],
                           "agents": ["openclaw", "hermes"], "repeats": 3},
        "selection_evidence_hashes": {f"fixture-selection-{i}": "0" * 64 for i in range(36)},
        "selection_scoring_records_hash": "0" * 64,
        "execution_conditions": conditions,
        "scorer_conditions": {variant: {"mode": "rules" if variant == "A" else "model",
                                       "scorer_version": "1",
                                       "judge": None if variant == "A" else judge.model_dump()}
                              for variant in "ABC"},
        "controls": {"suite_hash": control_hash, "variants": {
            variant: {"selected": variant, "suite_hash": control_hash,
                      "experiment_fingerprint": fingerprint, "critical_false_passes": 0,
                      "critical_expected_matches": 1, "critical_criteria": 1} for variant in "ABC"}},
        "budget": {"seconds_per_case": 600, "tool_actions_per_case": 20}}
    protocol_file = tmp_path / "protocol.json"
    storage.write_json(protocol_file, protocol)
    storage.write_json(runs / "manifest.json", {**protocol["holdout_matrix"], "provenance": "real",
                       "selection": None, "selection_hash": None,
                       "protocol_hash": engineering_protocol_hash(protocol)})
    return argparse.Namespace(dataset=dataset, evidence=runs, scores=scores,
                              protocol=protocol_file, output=tmp_path / "verification")


def rewrite(path, update):
    value = json.loads(path.read_text())
    update(value)
    path.chmod(0o600)
    storage.write_json(path, value)


def test_complete_offline_verification_never_configures_provider_or_runs_agent(frozen, monkeypatch, capsys):
    def forbidden(*a, **k):
        pytest.fail("offline verification must not read a provider, call a model, run Docker or save scores")
    for key in ("KMB_BASE_URL", "KMB_API_KEY", "KMB_MODEL"):
        monkeypatch.setenv(key, "synthetic-unused-setting")
    monkeypatch.setattr(provider.JudgeProvider, "from_env", forbidden)
    monkeypatch.setattr(provider.JudgeProvider, "judge", forbidden)
    monkeypatch.setattr(cli, "configured_provider", forbidden)
    monkeypatch.setattr(storage, "save_score", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    assert verifier.main(argv(frozen)) == 0
    report = json.loads((frozen.output / "verification.json").read_text())
    assert report["schema_version"] == verifier.SCHEMA
    assert report["purpose"] == "offline_rule_verification" and report["new_experiment"] is False
    assert report["model_calls"] == report["agent_runs"] == 0
    assert report["original_evidence_count"] == report["verified_rule_count"] == 108
    assert report["original_score_count"] == 324 and report["matching_rule_count"] == 108
    assert report["execution_status_counts"] == {"completed": 107, "budget_exhausted": 1}
    assert report["input_hashes_unchanged"] is True
    assert len(list((frozen.output / "records").glob("*.json"))) == 108
    with pytest.raises(ValueError):
        storage.load_scores(frozen.output)  # Independent schema cannot masquerade as formal scores.
    assert json.loads(capsys.readouterr().out)["status"] == "verified"


@pytest.mark.parametrize("kind", ["existing", "inside-evidence", "inside-scores", "inside-dataset",
                                 "symlink-output", "symlink-parent", "symlink-dotdot", "input-overlap", "symlink-input",
                                 "symlink-evidence-file"])
def test_unsafe_paths_rejected_before_output(frozen, tmp_path, kind, capsys):
    if kind == "existing":
        frozen.output.mkdir()
    elif kind.startswith("inside-"):
        frozen.output = getattr(frozen, kind.removeprefix("inside-")) / "new-output"
    elif kind == "symlink-output":
        frozen.output.symlink_to(frozen.evidence, target_is_directory=True)
    elif kind in {"symlink-parent", "symlink-dotdot"}:
        alias = tmp_path / "alias"
        alias.symlink_to(frozen.evidence, target_is_directory=True)
        frozen.output = alias / ".." / "new-output" if kind == "symlink-dotdot" else alias / "new-output"
    elif kind == "input-overlap":
        frozen.dataset = tmp_path
    elif kind == "symlink-input":
        alias = tmp_path / "alias"
        alias.symlink_to(frozen.dataset, target_is_directory=True)
        frozen.dataset = alias
    else:
        path = next(frozen.evidence.rglob("evidence.json"))
        original = path.with_name("original.json")
        path.rename(original)
        path.symlink_to(original)
    assert verifier.main(argv(frozen)) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "rejected"
    assert not (frozen.output / "verification.json").exists()


@pytest.mark.parametrize("kind", ["changed-evidence", "simulated", "missing-evidence", "missing-score", "missing-manifest",
    "duplicate-score", "protocol", "manifest", "dataset", "scoring-fingerprint", "critical",
    "judge", "score-version"])
def test_invalid_frozen_inputs_rejected(frozen, kind, capsys):
    evidence = next(frozen.evidence.rglob("evidence.json"))
    scoring = next(frozen.scores.rglob("A-*.json"))
    if kind == "changed-evidence":
        rewrite(evidence, lambda value: value.update(files_after={"changed.txt": "altered"}))
    elif kind == "simulated":
        bundle = storage.load_evidence(evidence)
        bundle.provenance = "simulated"
        bundle.freeze()
        evidence.chmod(0o600)
        storage.write_json(evidence, bundle.model_dump())
    elif kind == "missing-evidence":
        evidence.unlink()
    elif kind == "missing-score":
        scoring.unlink()
    elif kind == "missing-manifest":
        (frozen.evidence / "manifest.json").unlink()
    elif kind == "duplicate-score":
        shutil.copyfile(scoring, scoring.with_name("A-duplicate.json"))
    elif kind == "protocol":
        rewrite(frozen.protocol, lambda value: value.update(experiment_fingerprint="0" * 64))
    elif kind == "manifest":
        rewrite(frozen.evidence / "manifest.json", lambda value: value.update(protocol_hash="0" * 64))
    elif kind == "dataset":
        rubric = next((frozen.dataset / "rubrics").glob("*.json"))
        rewrite(rubric, lambda value: value["criteria"][0].update(description="changed fixture"))
    elif kind == "scoring-fingerprint":
        rewrite(scoring, lambda value: value.update(scoring_fingerprint="0" * 64))
    elif kind == "critical":
        rewrite(scoring, lambda value: value["criteria"][0].update(critical=not value["criteria"][0]["critical"]))
    elif kind == "judge":
        rewrite(next(frozen.scores.rglob("B-*.json")), lambda value: value["model"].update(returned="changed"))
    else:
        rewrite(scoring, lambda value: value.update(scorer_version="changed"))
    assert verifier.main(argv(frozen)) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "rejected"
    assert not frozen.output.exists()


def test_changed_reason_published_as_difference_and_nonzero_exit(frozen, capsys):
    original = next(frozen.scores.rglob("A-*.json"))
    rewrite(original, lambda value: value["criteria"][0].update(reason="altered historical reason"))
    assert verifier.main(argv(frozen)) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "different" and report["different_rule_count"] == 1
    record = json.loads((frozen.output / "records" / f"{report['different_runs'][0]}.json").read_text())
    assert record["differences"][0]["field"] == "criteria[0].reason"
    assert json.loads(original.read_text())["criteria"][0]["reason"] == "altered historical reason"


@pytest.mark.parametrize("kind,field", [("defaulted", "status"), ("coerced", "usage.input_tokens")])
def test_stored_A_fields_cannot_disappear_through_model_normalization(frozen, capsys, kind, field):
    path = next(frozen.scores.rglob("A-*.json"))
    if kind == "defaulted":
        rewrite(path, lambda value: value.pop("status"))
    else:
        rewrite(path, lambda value: value["usage"].update(input_tokens="0"))
    assert verifier.main(argv(frozen)) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["different_rule_count"] == 1
    record = json.loads((frozen.output / "records" / f"{report['different_runs'][0]}.json").read_text())
    assert record["differences"][0]["field"] == field


@pytest.mark.parametrize("variant", list("ABC"))
def test_duplicate_fields_in_any_original_score_are_rejected(frozen, capsys, variant):
    path = next(frozen.scores.rglob(f"{variant}-*.json"))
    path.write_text(path.read_text().replace('{\n', '{\n  "status": "scored",\n', 1))
    assert verifier.main(argv(frozen)) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "duplicate_json_field"
    assert not frozen.output.exists()


def test_B_C_structure_is_not_claimed_as_authenticated_or_recomputed(frozen, capsys):
    for variant in "BC":
        rewrite(next(frozen.scores.rglob(f"{variant}-*.json")),
                lambda value: value["criteria"][0].update(reason="structurally valid changed fixture reason"))
    assert verifier.main(argv(frozen)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["verification_scope"] == "A_recomputed_stable_fields_and_ABC_structural_binding"
    assert report["B_C_verification"] == {
        "status": "not_recomputed", "authentication": "not_authenticated_without_release_checksums"}


def test_only_top_level_elapsed_seconds_ignored(frozen, capsys):
    path = next(frozen.scores.rglob("A-*.json"))
    rewrite(path, lambda value: value.update(elapsed_seconds=99999))
    assert verifier.main(argv(frozen)) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["comparison"]["ignored_fields"] == ["elapsed_seconds"]
    original = ScoreResult(run_id="fixture", task_id="task", evidence_hash="x", scorer="A",
                           components={"nested": {"elapsed_seconds": 1}})
    recomputed = original.model_copy(deep=True, update={"elapsed_seconds": 99999})
    assert verifier.stable_score(original) == verifier.stable_score(recomputed)
    recomputed.components["nested"]["elapsed_seconds"] = 2
    assert verifier.differences(verifier.stable_score(original), verifier.stable_score(recomputed)) == [
        {"field": "components.nested.elapsed_seconds", "original": 1, "recomputed": 2}]


@pytest.mark.parametrize("field", sorted(set(ScoreResult.model_fields) - {"elapsed_seconds"}))
def test_all_score_fields_are_compared(field):
    original = verifier.stable_score(ScoreResult(run_id="fixture", task_id="task", evidence_hash="x", scorer="A"))
    recomputed = copy.deepcopy(original)
    recomputed[field] = {"changed": "fixture"} if not isinstance(original[field], dict) else "changed"
    assert verifier.differences(original, recomputed)[0]["field"] == field


def test_mid_run_input_mutation_rejected_before_report(frozen, monkeypatch, capsys):
    import kmb.scorers
    original_score = kmb.scorers.score
    changed = False
    async def mutate(bundle, rubric, variant, provider):
        nonlocal changed
        if not changed:
            changed = True
            (frozen.scores / "added-during-verification.txt").write_text("synthetic mutation")
        return await original_score(bundle, rubric, variant, provider)
    monkeypatch.setattr(kmb.scorers, "score", mutate)
    assert verifier.main(argv(frozen)) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "input_changed_during_verification"
    assert not frozen.output.exists()


def test_formal_A_only_engineering_gate_still_rejects(frozen):
    protocol = json.loads(frozen.protocol.read_text())
    with pytest.raises(ValueError, match="requires all three scoring variants"):
        asyncio.run(cli.score_batch(frozen.evidence, frozen.output, frozen.dataset, "A",
                                    use_provider=False, protocol=protocol))
    assert not frozen.output.exists()


@pytest.mark.parametrize("action", ["add", "remove"])
def test_core_module_inventory_changes_cannot_keep_same_snapshot(tmp_path, action):
    core = tmp_path / "core"
    core.mkdir()
    module = core / "fixture.py"
    module.write_text("# synthetic frozen module\n")
    lock = tmp_path / "uv.lock"
    lock.write_text("# synthetic frozen lock\n")
    inputs = [module, lock]
    before = verifier.snapshot({}, inputs)
    if action == "add":
        (core / "new.py").write_text("# newly added fixture module\n")
    else:
        module.unlink()
    assert verifier.snapshot({}, inputs) != before


def test_output_cannot_be_created_inside_frozen_core(frozen, tmp_path, monkeypatch, capsys):
    from kmb import review
    source = tmp_path / "isolated-core-fixture"
    core = source / "src/kmb"
    core.mkdir(parents=True)
    (core / "review.py").write_text("# fabricated core path only\n")
    (source / "uv.lock").write_text("# fabricated lock\n")
    monkeypatch.setattr(review, "__file__", str(core / "review.py"))
    frozen.output = core / "verification"
    assert verifier.main(argv(frozen)) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "output_overlaps_frozen_core"
    assert not frozen.output.exists()
