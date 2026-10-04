#!/usr/bin/env python3
"""Verify original engineering A records offline, without creating experiment scores.

The frozen kmb package, dataset, protocol, evidence and complete A/B/C score batch
are inputs. Only this external wrapper changes; no provider or agent is invoked.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
import sys
import time
from collections import Counter
from pathlib import Path

# Reading frozen inputs must not create bytecode alongside the installed core.
sys.dont_write_bytecode = True
SCHEMA = "offline-rule-verification-1"
RECORD_SCHEMA = "offline-rule-verification-record-1"
PURPOSE = "offline_rule_verification"
IGNORED_FIELDS = {"elapsed_seconds"}


class Rejected(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise Rejected(code)


def local_path(path: Path) -> Path:
    """Reject symlinks before resolve(), including a symlink in any ancestor."""
    path = path.expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    for candidate in (path, *path.parents):
        require(not candidate.is_symlink(), "path_symlink_forbidden")
    return path.resolve()


def overlap(left: Path, right: Path) -> bool:
    return left.is_relative_to(right) or right.is_relative_to(left)


def input_paths(args):
    inputs = {name: local_path(getattr(args, name))
              for name in ("dataset", "evidence", "scores", "protocol")}
    for name, path in inputs.items():
        require(path.is_file() if name == "protocol" else path.is_dir(), "input_missing_or_wrong_type")
    for i, path in enumerate(inputs.values()):
        for other in list(inputs.values())[i + 1:]:
            require(not overlap(path, other), "input_paths_overlap")
    output = local_path(args.output)
    require(not output.exists(), "output_already_exists")
    require(all(not overlap(output, path) for path in inputs.values()), "output_overlaps_input")
    return inputs, output


def file_digest(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "input_special_file_forbidden")
        value = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def snapshot(inputs, core_files):
    """Bind all input files and directory inventories, not only loaded records."""
    result = {}
    for name, root in inputs.items():
        local_path(root)
        require(root.is_file() if name == "protocol" else root.is_dir(), "input_missing_or_wrong_type")
        entries = [root] if root.is_file() else sorted(root.rglob("*"))
        files, directories = {}, []
        for path in entries:
            require(not path.is_symlink(), "input_symlink_forbidden")
            relative = path.name if root.is_file() else path.relative_to(root).as_posix()
            if path.is_dir():
                directories.append(relative)
            else:
                require(path.is_file(), "input_special_file_forbidden")
                files[relative] = file_digest(path)
        result[name] = {"files_sha256": files, "directories": directories}
    # Re-enumerate each time so a newly added/removed core module is a change too.
    current_core = [*sorted(core_files[0].parent.glob("*.py")), core_files[-1]]
    result["frozen_core"] = {str(path): file_digest(local_path(path)) for path in current_core}
    return result


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "duplicate_json_field")
        value[key] = item
    return value


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def stable_score(result):
    raw = result if isinstance(result, dict) else result.model_dump(mode="json")
    return {key: value for key, value in raw.items()
            if key not in IGNORED_FIELDS}


def differences(original, recomputed, prefix=""):
    """Publish every differing stable field, preserving list order and missing keys."""
    if type(original) is not type(recomputed):
        return [{"field": prefix, "original": original, "recomputed": recomputed}]
    if isinstance(original, dict):
        result = []
        for key in sorted(set(original) | set(recomputed)):
            field = f"{prefix}.{key}" if prefix else key
            if key not in original or key not in recomputed:
                result.append({"field": field, "original_present": key in original,
                               "recomputed_present": key in recomputed,
                               "original": original.get(key), "recomputed": recomputed.get(key)})
            else:
                result.extend(differences(original[key], recomputed[key], field))
        return result
    if isinstance(original, list):
        result = []
        for i in range(max(len(original), len(recomputed))):
            field = f"{prefix}[{i}]"
            if i >= len(original) or i >= len(recomputed):
                result.append({"field": field, "original_present": i < len(original),
                               "recomputed_present": i < len(recomputed),
                               "original": original[i] if i < len(original) else None,
                               "recomputed": recomputed[i] if i < len(recomputed) else None})
            else:
                result.extend(differences(original[i], recomputed[i], field))
        return result
    return [] if original == recomputed else [
        {"field": prefix, "original": original, "recomputed": recomputed}]


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


async def verify(args):
    from kmb import review
    from kmb.dataset import load_dataset
    from kmb.scorers import score
    from kmb.storage import load_evidence_tree, load_scores

    started = time.monotonic()
    inputs, output = input_paths(args)
    core_root = Path(review.__file__).resolve().parent
    core_files = [*sorted(core_root.glob("*.py")), core_root.parents[1] / "uv.lock"]
    require(all(path.is_file() for path in core_files), "frozen_core_or_lock_missing")
    require(not overlap(output, core_root) and not overlap(output, core_files[-1]),
            "output_overlaps_frozen_core")
    before = snapshot(inputs, core_files)
    protocol = read_json(inputs["protocol"])
    manifest = read_json(inputs["evidence"] / "manifest.json")
    evidence = load_evidence_tree(inputs["evidence"])
    raw_scores = [read_json(path) for path in sorted(inputs["scores"].rglob("*.json"))]
    originals = load_scores(inputs["scores"])
    validation = review.validate_engineering_scores(
        evidence, originals, protocol, inputs["dataset"], manifest)
    require(validation["eligible"], "original_engineering_batch_incomplete")
    require(protocol["scorer_conditions"]["A"]["mode"] == "rules", "frozen_A_is_not_rules")
    require(len(evidence) == 108 and len(originals) == 324, "original_matrix_count_mismatch")
    rubrics = {task.id: rubric for task, rubric in load_dataset(inputs["dataset"], "holdout")}
    # Compare stored JSON, not Pydantic-normalized originals: missing/defaulted
    # fields or coercible types are changes too. Core models still gate ABC above.
    original_A = {item["run_id"]: item for item in raw_scores if item["scorer"] == "A"}
    records = []
    for bundle in evidence:
        result = await score(bundle, rubrics[bundle.task_id], "A", provider=None)
        original, recomputed = stable_score(original_A[bundle.run_id]), stable_score(result)
        delta = differences(original, recomputed)
        records.append({"schema_version": RECORD_SCHEMA, "purpose": PURPOSE,
                        "new_experiment": False, "model_calls": 0, "agent_runs": 0,
                        "run_id": bundle.run_id, "task_id": bundle.task_id,
                        "execution_status": bundle.status, "evidence_hash": bundle.evidence_hash,
                        "status": "different" if delta else "match", "differences": delta,
                        "original_A": original, "recomputed_A": recomputed})
    require(snapshot(inputs, core_files) == before, "input_changed_during_verification")
    # Recheck output parents after scoring, then reserve the output exclusively.
    require(local_path(args.output) == output and not output.exists(), "output_already_exists")
    output.mkdir(parents=True, exist_ok=False)
    (output / "records").mkdir()
    for record in records:
        write_json(output / "records" / f"{record['run_id']}.json", record)
    after = snapshot(inputs, core_files)
    require(after == before, "input_changed_during_verification")
    changed = [record["run_id"] for record in records if record["differences"]]
    report = {"schema_version": SCHEMA, "purpose": PURPOSE, "new_experiment": False,
              "model_calls": 0, "agent_runs": 0, "status": "different" if changed else "verified",
              "verification_scope": "A_recomputed_stable_fields_and_ABC_structural_binding",
              "B_C_verification": {"status": "not_recomputed",
                                   "authentication": "not_authenticated_without_release_checksums"},
              "original_validation": validation, "original_evidence_count": len(evidence),
              "original_score_count": len(originals), "verified_rule_count": len(records),
              "matching_rule_count": len(records) - len(changed), "different_rule_count": len(changed),
              "different_runs": changed, "execution_status_counts": dict(Counter(e.status for e in evidence)),
              "comparison": {"ignored_fields": sorted(IGNORED_FIELDS),
                             "compared_fields": sorted(records[0]["original_A"])},
              "protocol_file_sha256": before["protocol"]["files_sha256"][inputs["protocol"].name],
              "protocol_hash": review.engineering_protocol_hash(protocol),
              "experiment_fingerprint": protocol["experiment_fingerprint"],
              "verifier_sha256": file_digest(Path(__file__).resolve()),
              "input_hashes_unchanged": True, "input_snapshot": before,
              "elapsed_seconds": time.monotonic() - started,
              "notice": "verified 仅指 A 稳定字段重算与 ABC 结构绑定；B/C 未重算，原字节真实性须另核发行校验清单。未运行智能体或模型，不是新实验或独立人审。"}
    write_json(output / "verification.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "evidence", "scores", "protocol", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(verify(args))
        print(json.dumps({key: value for key, value in report.items() if key != "input_snapshot"},
                         ensure_ascii=False, indent=2))
        return 1 if report["different_rule_count"] else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"schema_version": SCHEMA, "purpose": PURPOSE, "status": "rejected",
                          "new_experiment": False, "model_calls": 0, "agent_runs": 0,
                          "error": str(exc) if isinstance(exc, ValueError) else type(exc).__name__},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
