"""External orchestration tests; subprocesses are mocked, no model or Docker calls."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from kmb.dataset import load_dataset
from kmb.models import ScoreResult
from kmb.simulation import simulate
from kmb.storage import load_evidence_tree, save_evidence, save_score

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("batch_runner", PROJECT / "scripts/run-benchmark.py")
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)


@pytest.fixture
def config_file(tmp_path):
    def write(**changes):
        content = {"schema_version": 1, "agent": "simulation", "task": "update-001",
                   "variant": "A", **changes}
        path = tmp_path / "config.json"
        path.write_text(json.dumps(content))
        return path
    return write


@pytest.fixture
def fake_cli(monkeypatch):
    calls = []
    options = {"fail": None, "score_error": False, "mutate": None, "missing_score": False,
               "raise": None, "wrong_matrix": False, "missing_report": False,
               "score_status": "scored", "duplicate_score": False, "change_selection": False,
               "change_protocol": False,
               "interrupt": None, "infra_evidence": False}

    def run(command, **kwargs):
        stage = command[command.index("--dataset") + 2]
        calls.append((stage, command, kwargs))
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stdout"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.DEVNULL
        if options["raise"] == stage:
            raise OSError("secret should never be logged")
        if options["interrupt"] == stage:
            raise KeyboardInterrupt("secret should never be logged")
        if options["fail"] == stage:
            return subprocess.CompletedProcess(command, 17)
        output = Path(command[command.index("--output") + 1])
        if stage == "run":
            dataset = Path(command[command.index("--dataset") + 1])
            task_id = command[command.index("--task") + 1] if "--task" in command else None
            split = command[command.index("--split") + 1]
            pairs = load_dataset(dataset, split)
            if task_id:
                pairs = [(task, rubric) for task, rubric in pairs if task.id == task_id]
            agent = command[command.index("--agent") + 1]
            agents = ["openclaw", "hermes"] if agent == "both" else [agent]
            for agent in agents:
                for _ in range(int(command[command.index("--repeat") + 1])):
                    for task, _ in pairs:
                        evidence = simulate(task, dataset)
                        if agent != "simulation":
                            # Synthetic test injection, never an actual agent execution.
                            evidence.agent = agent
                            evidence.provenance = "real"
                            evidence.freeze()
                        if options["wrong_matrix"]:
                            evidence.agent = "wrong-agent"
                            evidence.freeze()
                        if options["infra_evidence"]:
                            evidence.status = "execution_unknown"
                            evidence.freeze()
                        save_evidence(evidence, output / agent / evidence.run_id)
            if options["change_selection"]:
                Path(command[command.index("--selection") + 1]).write_text("{}")
            if options["change_protocol"]:
                Path(command[command.index("--protocol") + 1]).write_text("{}")
        elif stage == "score":
            evidence_path = Path(command[command.index("--evidence") + 1])
            variant = command[command.index("--variant") + 1]
            for evidence in load_evidence_tree(evidence_path):
                for scorer in "ABC" if variant == "all" else variant:
                    if options["missing_score"]:
                        continue
                    score = ScoreResult(
                        run_id=evidence.run_id, task_id=evidence.task_id,
                        evidence_hash=evidence.evidence_hash, scorer=scorer,
                        status="error" if options["score_error"] else options["score_status"],
                        error="synthetic_judge_failure" if options["score_error"] else None,
                        verdict="fail")
                    save_score(score, output)
                    if options["duplicate_score"]:
                        save_score(score, output)
            if options["mutate"] == "score":
                evidence_file = next(evidence_path.rglob("evidence.json"))
                evidence_file.chmod(0o600)  # Deliberate tampering of this disposable test artifact.
                evidence_file.write_text(evidence_file.read_text() + "\n")
        elif stage == "report" and not options["missing_report"]:
            output.mkdir(parents=True)
            (output / "index.html").write_text("<html>synthetic test report</html>")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(batch.subprocess, "run", run)
    return calls, options


def invocation(config_file, tmp_path, **changes):
    return ["--config", str(config_file(**changes)), "--output", str(tmp_path / "batch")]


def test_dry_run_is_read_only_and_does_not_require_model(config_file, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run cannot spawn processes or build a provider")
    monkeypatch.setattr(batch.subprocess, "run", forbidden)
    monkeypatch.setattr("kmb.provider.JudgeProvider.from_env", forbidden)
    argv = invocation(config_file, tmp_path, agent="both", variant="all")
    assert batch.main([*argv, "--dry-run"]) == 0
    assert batch.main([*argv, "--validate"]) == 0
    assert not (tmp_path / "batch").exists()


def test_serial_pipeline_same_frozen_evidence_and_simulation_no_model(
        config_file, tmp_path, fake_cli, monkeypatch):
    monkeypatch.setenv("KMB_API_KEY", "must-not-be-inherited")
    monkeypatch.setenv("KMB_BASE_URL", "https://example.invalid/private-path")
    monkeypatch.setenv("KMB_MODEL", "unused")
    calls, _ = fake_cli
    assert batch.main(invocation(config_file, tmp_path)) == 0
    assert [call[0] for call in calls] == ["run", "score", "report"]
    expected = str(tmp_path / "batch" / "runs")
    assert calls[0][1][calls[0][1].index("--output") + 1] == expected
    assert all(command[command.index("--evidence") + 1] == expected
               for _, command, _ in calls[1:])
    assert all("KMB_API_KEY" not in kwargs["env"] for _, _, kwargs in calls)
    record = json.loads((tmp_path / "batch/batch-status.json").read_text())
    assert record["status"] == "completed" and len(record["evidence_files"]) == 1
    assert all(s["status"] == "completed" and s["exit_code"] == 0 for s in record["stages"])
    assert record["configuration"]["model_calls_enabled"] is False
    assert (tmp_path / "batch/report/index.html").is_file()


@pytest.mark.parametrize("stage,expected", [("run", ["run"]), ("score", ["run", "score"]),
                                           ("report", ["run", "score", "report"])])
def test_nonzero_stops_without_replay_preserves_stage_code(
        stage, expected, config_file, tmp_path, fake_cli):
    calls, options = fake_cli
    options["fail"] = stage
    assert batch.main(invocation(config_file, tmp_path)) == 1
    assert [call[0] for call in calls] == expected
    record = json.loads((tmp_path / "batch/batch-status.json").read_text())
    assert record["stages"][-1]["exit_code"] == 17
    assert record["stages"][-1]["error_code"] == "cli_nonzero_exit"
    assert record["status"] == "failed" and record["automatic_replay"] is False
    assert batch.main(invocation(config_file, tmp_path)) == 2
    assert [call[0] for call in calls] == expected


@pytest.mark.parametrize("option,code", [
    ("score_error", "scoring_record_error"), ("missing_score", "score_coverage_mismatch"),
    ("wrong_matrix", "evidence_matrix_mismatch"), ("missing_report", "report_missing"),
    ("infra_evidence", "execution_infrastructure_or_unknown")])
def test_zero_exit_does_not_hide_invalid_artifacts(option, code, config_file, tmp_path, fake_cli):
    calls, options = fake_cli
    options[option] = True
    assert batch.main(invocation(config_file, tmp_path)) == 1
    record = json.loads((tmp_path / "batch/batch-status.json").read_text())
    assert record["status"] == "failed" and record["stages"][-1]["exit_code"] == 0
    assert record["stages"][-1]["error_code"] == code
    if option != "missing_report":
        assert "report" not in [call[0] for call in calls]


def test_original_evidence_change_stops_report(config_file, tmp_path, fake_cli):
    calls, options = fake_cli
    options["mutate"] = "score"
    assert batch.main(invocation(config_file, tmp_path)) == 1
    assert [call[0] for call in calls] == ["run", "score"]
    assert list((tmp_path / "batch/runs").rglob("evidence.json"))
    record = json.loads((tmp_path / "batch/batch-status.json").read_text())
    assert record["stages"][-1]["error_code"] == "evidence_drift"


@pytest.mark.parametrize("option,value", [("score_status", "not_scored"),
                                         ("duplicate_score", True)])
def test_abstained_or_duplicate_score_records_cannot_be_success(
        option, value, config_file, tmp_path, fake_cli):
    calls, options = fake_cli
    options[option] = value
    assert batch.main(invocation(config_file, tmp_path)) == 1
    assert [call[0] for call in calls] == ["run", "score"]


def test_spawn_error_records_safe_failure(config_file, tmp_path, fake_cli):
    _, options = fake_cli
    options["raise"] = "score"
    assert batch.main(invocation(config_file, tmp_path)) == 1
    text = (tmp_path / "batch/batch-status.json").read_text()
    assert "secret should never be logged" not in text
    record = json.loads(text)
    assert record["stages"][-1]["exit_code"] is None
    assert record["stages"][-1]["error_type"] == "OSError"
    assert record["stages"][-1]["error_code"] == "stage_io_error"


def test_interruption_does_not_claim_process_cleanup(config_file, tmp_path, fake_cli):
    calls, options = fake_cli
    options["interrupt"] = "run"
    assert batch.main(invocation(config_file, tmp_path)) == 130
    assert [call[0] for call in calls] == ["run"]
    text = (tmp_path / "batch/batch-status.json").read_text()
    assert "secret should never be logged" not in text
    record = json.loads(text)
    assert record["status"] == "interrupted"
    stage = record["stages"][-1]
    assert stage["error_code"] == "interrupted_cleanup_unknown" and stage["exit_code"] is None
    assert stage["execution_cleanup"].startswith("unconfirmed")


def test_real_images_and_safe_provider_identity(config_file, tmp_path, fake_cli, monkeypatch):
    monkeypatch.setenv("KMB_BASE_URL", "https://example.invalid/private-endpoint")
    monkeypatch.setenv("KMB_API_KEY", 'test-secret-with-"quote')
    monkeypatch.setenv("KMB_MODEL", "fixed-model")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated")
    monkeypatch.setenv("KMB_AGENT_API_KEY", "untrusted-direct-key")
    monkeypatch.setenv("PYTHONPATH", "/untrusted/other-protocol")
    monkeypatch.setenv("PYTHONHOME", "/untrusted/other-python")
    calls, _ = fake_cli
    assert batch.main(invocation(config_file, tmp_path, agent="both", variant="all",
                                 images={"openclaw": "local/fixed:v2"})) == 0
    assert all(kwargs["env"]["KMB_OPENCLAW_IMAGE"] == "local/fixed:v2"
               for _, _, kwargs in calls)
    assert all("KMB_AGENT_API_KEY" not in kwargs["env"] and "OPENAI_API_KEY" not in kwargs["env"]
               for _, _, kwargs in calls)
    assert all(command[1:4] == ["-I", "-m", "kmb.cli"]
               and "PYTHONPATH" not in kwargs["env"] and "PYTHONHOME" not in kwargs["env"]
               for _, command, kwargs in calls)
    text = (tmp_path / "batch/batch-status.json").read_text()
    assert "private-endpoint" not in text and "test-secret" not in text
    record = json.loads(text)
    assert record["configuration"]["model"]["requested"] == "fixed-model"
    assert record["configuration"]["model"]["parameters"]["endpoint_sha256"]


def test_missing_real_provider_rejects_before_output(config_file, tmp_path, monkeypatch, fake_cli):
    for key in ("KMB_BASE_URL", "KMB_API_KEY", "KMB_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert batch.main(invocation(config_file, tmp_path, agent="openclaw")) == 2
    assert not (tmp_path / "batch").exists() and fake_cli[0] == []


@pytest.mark.parametrize("change", [
    {"api_key": "forbidden"}, {"env": {"KMB_API_KEY": "forbidden"}},
    {"model": "forbidden"}, {"schema_version": True}, {"repeat": True}, {"repeat": 0},
    {"repeat": 4}, {"pilot": 1}, {"pilot": True, "split": "selection"},
    {"task": "../private"}, {"task": "unknown-task"}, {"variant": "D"},
    {"agent": "another-agent"}, {"images": {"openclaw": "image:v1"}},
    {"agent": "openclaw", "images": {"hermes": "image:v1"}},
    {"agent": "openclaw", "images": {"openclaw": "image:latest"}},
    {"agent": "openclaw", "images": {"openclaw": "image"}},
    {"agent": "openclaw", "images": {"openclaw": None}},
    {"agent": "openclaw", "simulation_behavior": "empty"},
    {"split": "holdout"}, {"dataset": "https://example.invalid/data"},
])
def test_configuration_boundaries(change, config_file, tmp_path, fake_cli):
    assert batch.main([*invocation(config_file, tmp_path, **change), "--dry-run"]) == 2
    assert not (tmp_path / "batch").exists() and fake_cli[0] == []


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"schema_version":1,"agent":"simulation","agent":"both"}')
    with pytest.raises(ValueError, match="duplicate"):
        batch.load_config(path)


@pytest.mark.parametrize("content", ["[]", "{", " " * 65_537])
def test_invalid_or_oversized_json_is_rejected(tmp_path, content):
    path = tmp_path / "config.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        batch.load_config(path)


def test_paths_resolve_from_config_and_output_stays_outside_data(config_file, tmp_path):
    path = config_file(dataset="dataset")
    config = batch.load_config(path)
    assert config["dataset"] == str(tmp_path / "dataset")
    with pytest.raises(ValueError):
        batch.fresh_output(tmp_path / "dataset/results", tmp_path / "dataset")
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValueError):
        batch.fresh_output(existing, PROJECT / "data")
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "not-present")
    with pytest.raises(ValueError):
        batch.fresh_output(link, PROJECT / "data")


def test_holdout_manifest_is_passed_to_each_original_cli_gate(config_file, tmp_path, fake_cli,
                                                            monkeypatch):
    monkeypatch.setenv("KMB_BASE_URL", "http://example.invalid/v1")
    monkeypatch.setenv("KMB_API_KEY", "test-only")
    monkeypatch.setenv("KMB_MODEL", "fixed-model")
    selection = tmp_path / "selection.json"
    selection.write_text('{"not_a_valid_selection":"original CLI must reject"}')
    path = config_file(agent="both", split="holdout", repeat=3, selection="selection.json")
    data = json.loads(path.read_text())
    del data["task"]
    path.write_text(json.dumps(data))
    config = batch.load_config(path)
    plan = batch.commands(config, tmp_path / "batch")
    assert all(command[command.index("--selection") + 1] == str(selection)
               for _, command in plan)
    calls, options = fake_cli
    options["fail"] = "run"  # Mock rejection by the unchanged formal CLI gate.
    assert batch.main(["--config", str(path), "--output", str(tmp_path / "batch")]) == 1
    assert [call[0] for call in calls] == ["run"]
    record = json.loads((tmp_path / "batch/batch-status.json").read_text())
    assert record["stages"][-1]["error_code"] == "cli_nonzero_exit"
    assert record["selection_file_sha256"] and record["status"] == "failed"


def test_selection_changes_stop_before_scoring(config_file, tmp_path, fake_cli):
    selection = tmp_path / "selection.json"
    selection.write_text('{"test_marker":"before"}')
    path = config_file(split="holdout", selection="selection.json")
    data = json.loads(path.read_text())
    del data["task"]
    path.write_text(json.dumps(data))
    calls, options = fake_cli
    options["change_selection"] = True
    assert batch.main(["--config", str(path), "--output", str(tmp_path / "batch")]) == 1
    assert [call[0] for call in calls] == ["run"]
    record = json.loads((tmp_path / "batch/batch-status.json").read_text())
    assert record["stages"][-1]["error_code"] == "selection_drift"


def test_engineering_protocol_is_passed_and_bound_across_stages(config_file, tmp_path, fake_cli,
                                                                monkeypatch):
    monkeypatch.setenv("KMB_BASE_URL", "http://example.invalid/v1")
    monkeypatch.setenv("KMB_API_KEY", "test-only")
    monkeypatch.setenv("KMB_MODEL", "fixed-model")
    protocol = tmp_path / "protocol.json"
    protocol.write_text('{"kind":"engineering"}')
    path = config_file(agent="both", split="holdout", repeat=3, protocol="protocol.json")
    data = json.loads(path.read_text())
    del data["task"]
    path.write_text(json.dumps(data))
    config = batch.load_config(path)
    assert all(command[command.index("--protocol") + 1] == str(protocol)
               and "--selection" not in command for _, command in batch.commands(config, tmp_path / "batch"))
    calls, options = fake_cli
    options["fail"] = "run"  # The dedicated CLI, not the batch wrapper, validates the protocol.
    assert batch.main(["--config", str(path), "--output", str(tmp_path / "batch")]) == 1
    assert [call[0] for call in calls] == ["run"]
    record = json.loads((tmp_path / "batch/batch-status.json").read_text())
    assert record["protocol_file_sha256"] and record["status"] == "failed"


def test_engineering_protocol_drift_stops_before_scoring(config_file, tmp_path, fake_cli):
    (tmp_path / "protocol.json").write_text('{"kind":"engineering"}')
    path = config_file(split="holdout", protocol="protocol.json")
    data = json.loads(path.read_text())
    del data["task"]
    path.write_text(json.dumps(data))
    calls, options = fake_cli
    options["change_protocol"] = True
    assert batch.main(["--config", str(path), "--output", str(tmp_path / "batch")]) == 1
    assert [call[0] for call in calls] == ["run"]
    record = json.loads((tmp_path / "batch/batch-status.json").read_text())
    assert record["stages"][-1]["error_code"] == "protocol_drift"


def test_engineering_protocol_and_human_selection_are_exclusive(config_file, tmp_path):
    (tmp_path / "protocol.json").write_text("{}")
    (tmp_path / "selection.json").write_text("{}")
    path = config_file(split="holdout", protocol="protocol.json", selection="selection.json")
    with pytest.raises(ValueError, match="mutually exclusive"):
        batch.load_config(path)


def test_engineering_protocol_retains_run_and_scorer_failures(config_file, tmp_path, fake_cli):
    (tmp_path / "protocol.json").write_text('{"kind":"engineering"}')
    calls, options = fake_cli
    options["infra_evidence"] = True
    options["score_error"] = True
    path = config_file(split="holdout", task="update-008", protocol="protocol.json")
    config = batch.load_config(path)
    output = tmp_path / "batch"
    assert batch.execute(config, output, batch.selected_tasks(config), {}) == 0
    assert [stage for stage, _, _ in calls] == ["run", "score", "report"]
    record = json.loads((output / "batch-status.json").read_text())
    assert record["status"] == "completed"


@pytest.mark.parametrize("changes", [{"agent": "openclaw", "repeat": 3},
                                     {"agent": "both", "repeat": 1},
                                     {"agent": "both", "repeat": 3, "task": "update-005"}])
def test_formal_holdout_shape_is_not_weakened(config_file, tmp_path, changes):
    (tmp_path / "selection.json").write_text("{}")
    path = config_file(split="holdout", selection="selection.json", **changes)
    if "task" not in changes:
        data = json.loads(path.read_text())
        del data["task"]
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="real holdout"):
        batch.load_config(path)


def test_example_is_valid_and_uses_existing_agents():
    config = batch.load_config(PROJECT / "examples/batch-dev.json")
    assert config["agent"] == "both" and len(batch.selected_tasks(config)) == 12
