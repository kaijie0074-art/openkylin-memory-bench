#!/usr/bin/env python3
"""One serial run -> score -> report pipeline, without changing the core protocol."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
FIELDS = {"schema_version", "agent", "task", "pilot", "split", "repeat", "variant",
          "images", "dataset", "selection", "protocol", "simulation_behavior"}


class BatchFailure(ValueError):
    """A fixed diagnostic code owned by this wrapper, never upstream error text."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON configuration field")
        result[key] = value
    return result


def config_path(value, base: Path) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value or "://" in value:
        raise ValueError("invalid local configuration path")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def load_config(path: Path) -> dict:
    from kmb.adapters import _validate_image

    if not path.is_file() or path.stat().st_size > 65_536:
        raise ValueError("configuration must be a JSON file of at most 64 KiB")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid JSON configuration") from exc
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError("unsupported configuration fields; credentials and environment are forbidden")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    if value.get("agent") not in ("openclaw", "hermes", "both", "simulation"):
        raise ValueError("agent must explicitly select openclaw, hermes, both or simulation")
    result = {"schema_version": 1, "agent": value["agent"], "pilot": False, "split": "dev",
              "repeat": 1, "variant": "all", "images": {}, **value}
    if type(result["pilot"]) is not bool:
        raise ValueError("pilot must be a boolean")
    if result["split"] not in ("dev", "selection", "holdout"):
        raise ValueError("invalid split")
    if type(result["repeat"]) is not int or not 1 <= result["repeat"] <= 3:
        raise ValueError("repeat must be an integer from 1 to 3")
    if result["variant"] not in ("A", "B", "C", "all"):
        raise ValueError("invalid scoring variant")
    if "task" in result and (not isinstance(result["task"], str)
                            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", result["task"])):
        raise ValueError("invalid task identifier")
    if result["pilot"] and result["split"] != "dev":
        raise ValueError("pilot is development-only")
    agents = ["openclaw", "hermes"] if result["agent"] == "both" else [result["agent"]]
    images = result["images"]
    if (not isinstance(images, dict) or set(images) - set(agents)
            or (result["agent"] == "simulation" and images)):
        raise ValueError("images may only override selected real agents")
    for image in images.values():
        if not isinstance(image, str):
            raise TypeError("image must be an explicit fixed tag or digest")
        _validate_image(image)
    if "simulation_behavior" in result and (
            result["agent"] != "simulation"
            or result["simulation_behavior"] not in ("expected", "empty", "infra_error")):
        raise ValueError("simulation_behavior is only available for explicit simulation")
    result["dataset"] = str(config_path(value.get("dataset", str(PROJECT / "data")), path.parent))
    if "selection" in value and "protocol" in value:
        raise ValueError("selection and engineering protocol are mutually exclusive")
    for key in ("selection", "protocol"):
        if key in value:
            result[key] = str(config_path(value[key], path.parent))
            if not Path(result[key]).is_file():
                raise ValueError(f"{key} manifest does not exist")
            if result["split"] != "holdout":
                raise ValueError(f"{key} manifest is only accepted for holdout")
    if result["split"] == "holdout":
        if "selection" not in result and "protocol" not in result:
            raise ValueError("holdout requires an explicit selection or engineering protocol")
        if result["agent"] != "simulation" and (
                result["agent"] != "both" or result["repeat"] != 3 or "task" in result):
            raise ValueError("real holdout requires both agents, all tasks and three repeats")
    return result


def selected_tasks(config: dict) -> list[str]:
    from kmb.dataset import load_dataset, pilot_tasks

    dataset = Path(config["dataset"])
    pairs = pilot_tasks(dataset) if config["pilot"] else load_dataset(dataset, config["split"])
    ids = [task.id for task, _ in pairs if "task" not in config or task.id == config["task"]]
    if not ids:
        raise ValueError("configuration selects no tasks")
    return ids


def fresh_output(path: Path, dataset: Path) -> Path:
    path = path.expanduser()
    if path.exists() or path.is_symlink():
        raise ValueError("output must not already exist, even if empty")
    result = path.resolve()
    if result.exists() or result.is_relative_to(dataset) or result.is_relative_to(PROJECT / "src"):
        raise ValueError("output must be fresh and outside dataset/source directories")
    for parent in result.parents:
        if parent.exists() and not parent.is_dir():
            raise ValueError("output parent is not a directory")
    return result


def commands(config: dict, output: Path) -> list[tuple[str, list[str]]]:
    prefix = [sys.executable, "-I", "-m", "kmb.cli", "--dataset", config["dataset"]]
    evidence = str(output / "runs")
    protocol = (["--selection", config["selection"]] if "selection" in config
                else ["--protocol", config["protocol"]] if "protocol" in config else [])
    run = [*prefix, "run", "--agent", config["agent"], "--split", config["split"],
           "--repeat", str(config["repeat"]), "--output", evidence, *protocol]
    if config["pilot"]:
        run.append("--pilot")
    if "task" in config:
        run.extend(["--task", config["task"]])
    if "simulation_behavior" in config:
        run.extend(["--behavior", config["simulation_behavior"]])
    score = [*prefix, "score", "--evidence", evidence, "--variant", config["variant"],
             "--output", str(output / "scores"), *protocol]
    report = [*prefix, "report", "--evidence", evidence, "--scores", str(output / "scores"),
              "--output", str(output / "report"), *protocol]
    return [("run", run), ("score", score), ("report", report)]


def child_environment(config: dict, environ: dict) -> tuple[dict, dict]:
    from kmb.adapters import AGENTS, _validate_image
    from kmb.provider import JudgeProvider

    # No dotenv/profile loading; gateway credentials must be generated by the existing runner.
    env = {key: value for key, value in environ.items()
           if not key.startswith(("OPENAI_", "ANTHROPIC_", "KMB_AGENT_"))
           and key not in {"PYTHONPATH", "PYTHONHOME"}}
    images = {}
    agents = ["openclaw", "hermes"] if config["agent"] == "both" else [config["agent"]]
    for agent in agents:
        if agent == "simulation":
            continue
        name = f"KMB_{agent.upper()}_IMAGE"
        images[agent] = config["images"].get(agent, env.get(name, AGENTS[agent]))
        _validate_image(images[agent])
        env[name] = images[agent]
    needs_model = config["agent"] != "simulation" or config["variant"] != "A"
    identity = JudgeProvider.from_env(env).public_identity() if needs_model else None
    # A-only simulation cannot accidentally pick up a provider from the caller.
    if not needs_model:
        for name in ("KMB_BASE_URL", "KMB_API_KEY", "KMB_MODEL", "KMB_API_STYLE"):
            env.pop(name, None)
    return env, {"model": identity, "images": images, "model_calls_enabled": needs_model}


def evidence_state(output: Path, config: dict, tasks: list[str]) -> dict:
    from kmb.storage import load_evidence_tree

    evidence = load_evidence_tree(output / "runs")
    agents = ["openclaw", "hermes"] if config["agent"] == "both" else [config["agent"]]
    if config["agent"] == "simulation":
        agents = ["simulation/" + config.get("simulation_behavior", "expected")]
    wanted = Counter({(task, agent): config["repeat"] for task in tasks for agent in agents})
    actual = Counter((e.task_id, e.agent) for e in evidence)
    provenance = "simulated" if config["agent"] == "simulation" else "real"
    if actual != wanted or any(e.split != config["split"] or e.provenance != provenance
                               for e in evidence):
        raise BatchFailure("evidence_matrix_mismatch")
    # Engineering validation keeps every attempted run, including infrastructure
    # and unknown outcomes, so the final report can account for them explicitly.
    if "protocol" not in config and any(
            e.status in {"infrastructure_error", "execution_unknown"} for e in evidence):
        raise BatchFailure("execution_infrastructure_or_unknown")
    return {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((output / "runs").rglob("evidence.json"))}


def validate_scores(output: Path, config: dict) -> None:
    from kmb.storage import load_evidence_tree, load_scores

    evidence = load_evidence_tree(output / "runs")
    scores = load_scores(output / "scores")
    variants = "ABC" if config["variant"] == "all" else config["variant"]
    expected = Counter((e.run_id, e.task_id, e.evidence_hash, v) for e in evidence for v in variants)
    actual = Counter((s.run_id, s.task_id, s.evidence_hash, s.scorer) for s in scores)
    if actual != expected:
        raise BatchFailure("score_coverage_mismatch")
    if "protocol" not in config and any(s.status != "scored" or s.error for s in scores):
        raise BatchFailure("scoring_record_error")


def write_record(output: Path, record: dict, secrets: tuple[str, ...]) -> None:
    # Logs contain no raw process output/environment; additionally scrub exact configured secrets.
    def scrub(value):
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, str):
            for secret in secrets:
                if secret:
                    value = value.replace(secret, "[REDACTED]")
        return value
    text = json.dumps(scrub(record), ensure_ascii=False, indent=2)
    temporary = output / ".batch-status.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(text + "\n")
    temporary.replace(output / "batch-status.json")


def execute(config: dict, output: Path, tasks: list[str], environ: dict) -> int:
    env, identity = child_environment(config, environ)
    secrets = tuple(environ.get(name, "") for name in ("KMB_API_KEY", "KMB_BASE_URL"))
    record = {"schema_version": 1, "status": "running", "config": config,
              "tasks": tasks, "configuration": identity, "stages": [],
              "protocol_gate": "delegated_to_existing_cli", "automatic_replay": False,
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    manifest_key = "selection" if "selection" in config else "protocol" if "protocol" in config else None
    if manifest_key:
        record[f"{manifest_key}_file_sha256"] = hashlib.sha256(
            Path(config[manifest_key]).read_bytes()).hexdigest()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_record(output, record, secrets)
    frozen = None
    for name, command in commands(config, output):
        stage = {"name": name, "status": "running", "command": command, "exit_code": None}
        record["stages"].append(stage)
        write_record(output, record, secrets)
        start = time.monotonic()
        try:
            if frozen is not None and evidence_state(output, config, tasks) != frozen:
                raise BatchFailure("evidence_drift")
            if manifest_key and hashlib.sha256(
                    Path(config[manifest_key]).read_bytes()).hexdigest() != record[f"{manifest_key}_file_sha256"]:
                raise BatchFailure(f"{manifest_key}_drift")
            result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            stage["exit_code"] = result.returncode
            if result.returncode:
                raise BatchFailure("cli_nonzero_exit")
            snapshot = evidence_state(output, config, tasks)
            if frozen is not None and snapshot != frozen:
                raise BatchFailure("evidence_drift")
            frozen = snapshot
            record["evidence_files"] = frozen
            if name == "score":
                validate_scores(output, config)
            if name == "report" and not (output / "report" / "index.html").is_file():
                raise BatchFailure("report_missing")
            stage["status"] = "completed"
        except (OSError, ValueError, TypeError, KeyboardInterrupt) as exc:
            stage["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            # Error messages may contain paths, keys or output supplied by subprocess libraries.
            stage["error_type"] = type(exc).__name__
            stage["error_code"] = (exc.code if isinstance(exc, BatchFailure)
                                   else "interrupted_cleanup_unknown" if isinstance(exc, KeyboardInterrupt)
                                   else "stage_io_error" if isinstance(exc, OSError)
                                   else "invalid_artifact")
            if isinstance(exc, KeyboardInterrupt):
                stage["execution_cleanup"] = "unconfirmed; inspect processes before any new run"
            record["status"] = stage["status"]
        finally:
            stage["elapsed_seconds"] = round(time.monotonic() - start, 6)
            write_record(output, record, secrets)
        if stage["status"] != "completed":
            return 130 if stage["status"] == "interrupted" else 1
    record["status"] = "completed"
    record["meaning"] = "pipeline completed; task verdicts and formal eligibility are separate"
    write_record(output, record, secrets)
    return 0


def main(argv: list[str] | None = None) -> int:
    from kmb.provider import ProviderError

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", "--validate", action="store_true", dest="dry_run",
                        help="Read-only config/task/path validation; no output or model call.")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config.resolve())
        tasks = selected_tasks(config)
        output = fresh_output(args.output, Path(config["dataset"]))
        if args.dry_run:
            print(json.dumps({"status": "validated", "agent": config["agent"],
                              "task_count": len(tasks), "repeat": config["repeat"],
                              "variant": config["variant"], "phases": ["run", "score", "report"],
                              "model_calls": 0, "output_created": False,
                              "notice": "Provider/Docker and protocol gate are checked on execution."}))
            return 0
        result = execute(config, output, tasks, dict(os.environ))
        print(json.dumps({"status": "completed" if result == 0 else "failed", "exit_code": result,
                          "record": "batch-status.json", "automatic_replay": False}))
        return result
    except (OSError, ValueError, TypeError, ProviderError) as exc:
        print(json.dumps({"status": "rejected", "error_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
