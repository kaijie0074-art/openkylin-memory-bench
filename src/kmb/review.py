"""Independent, blind human review. No synthetic or AI-generated human labels."""
from __future__ import annotations

import hashlib
import json
import random
import uuid
from collections import Counter
from pathlib import Path

from kmb.models import CriterionScore, EvidenceBundle, PrivateRubric, aggregate_verdict
from kmb.storage import private_write, write_json


def rubric_hash(rubric: PrivateRubric) -> str:
    return hashlib.sha256(json.dumps(rubric.model_dump(), sort_keys=True,
                                     ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def export_review(evidence: list[EvidenceBundle], rubrics: dict[str, PrivateRubric], output: Path,
                  reviewers: tuple[str, str] = ("reviewer-1", "reviewer-2")) -> dict:
    if len(set(reviewers)) != 2 or not all(reviewers):
        raise ValueError("two distinct reviewer identities are required")
    if output.exists():
        raise ValueError("review export directory already exists; do not overwrite blind reviews")
    output.mkdir(parents=True)
    mapping = {"review_id": uuid.uuid4().hex, "reviewers": list(reviewers), "items": {}}
    cards = []
    for bundle in evidence:
        bundle.verify()
        card_id = uuid.uuid4().hex
        rubric = rubrics[bundle.task_id]
        mapping["items"][card_id] = {"run_id": bundle.run_id, "evidence_hash": bundle.evidence_hash,
                                      "task_id": bundle.task_id, "split": bundle.split,
                                      "provenance": bundle.provenance,
                                      "criterion_ids": [c.id for c in rubric.criteria], "rubric_hash": rubric_hash(rubric)}
        cards.append({"item_id": card_id, "source": bundle.provenance,
                      "status": bundle.status, "events": [e.model_dump() for e in bundle.events],
                      "files_before": bundle.files_before, "files_after": bundle.files_after,
                      "files_complete": bundle.files_complete,
                      "file_inventory_after": bundle.file_inventory_after,
                      "file_inventory_complete": bundle.file_inventory_complete,
                      "memory_observable": bundle.memory_observable,
                      "memory_snapshot": bundle.memory_snapshot if bundle.memory_observable else {},
                      "criteria": [c.model_dump() for c in rubric.criteria]})
    private_write(output / "private-mapping.json", mapping)
    for index, reviewer in enumerate(reviewers):
        order = cards.copy()
        random.Random(mapping["review_id"] + str(index)).shuffle(order)
        # Review directories are opaque to avoid path injection from reviewer display names.
        document = {
            "review_id": mapping["review_id"], "reviewer": reviewer,
            "instructions": "先独立逐项判断，再处理分歧。这里没有任何自动评分结论。可语音给出判断，由操作员录入。",
            "items": order,
        }
        directory = output / f"reviewer-{index + 1}"
        write_json(directory / "cards.json", document, exclusive=True)
        from kmb.blind_review import render_blind_cards
        render_blind_cards(document, directory / "index.html")
    return {"review_id": mapping["review_id"], "items": len(cards), "reviewers": list(reviewers)}


def record_label(root: Path, reviewer: str, item_id: str, criteria: dict[str, str], note: str) -> Path:
    mapping = json.loads((root / "private-mapping.json").read_text())
    if reviewer not in mapping["reviewers"] or item_id not in mapping["items"]:
        raise ValueError("unknown reviewer or blind item")
    entry = mapping["items"][item_id]
    if set(criteria) != set(entry["criterion_ids"]):
        raise ValueError("human labels must cover every criterion exactly once")
    if any(v not in {"pass", "fail", "undetermined"} for v in criteria.values()):
        raise ValueError("invalid human verdict")
    if not note.strip():
        raise ValueError("record the human's reasoning or voice-transcription note")
    index = mapping["reviewers"].index(reviewer)
    path = root / f"reviewer-{index + 1}" / "labels" / f"{item_id}.json"
    write_json(path, {"review_id": mapping["review_id"], "reviewer": reviewer, "item_id": item_id,
                     "criteria": criteria, "note": note, "source": "human_attested",
                     "evidence_hash": entry["evidence_hash"]}, exclusive=True)
    return path


def adjudicate(root: Path, item_id: str, criteria: dict[str, str], note: str) -> Path:
    mapping = json.loads((root / "private-mapping.json").read_text())
    entry = mapping["items"].get(item_id)
    if not entry or set(criteria) != set(entry["criterion_ids"]):
        raise ValueError("invalid adjudication criteria")
    if not all((root / f"reviewer-{i + 1}" / "labels" / f"{item_id}.json").exists() for i in range(2)):
        raise ValueError("both independent reviews are required before adjudication")
    if not note.strip() or any(v not in {"pass", "fail", "undetermined"} for v in criteria.values()):
        raise ValueError("explicit human adjudication with valid labels and reason is required")
    path = root / "adjudications" / f"{item_id}.json"
    write_json(path, {"criteria": criteria, "note": note, "source": "human_adjudicated",
                     "evidence_hash": entry["evidence_hash"]}, exclusive=True)
    return path


def consensus(root: Path) -> dict:
    mapping = json.loads((root / "private-mapping.json").read_text())
    result = {"review_id": mapping["review_id"], "labels": {}, "pending": [], "conflicts": []}
    for item_id, entry in mapping["items"].items():
        paths = [root / f"reviewer-{i + 1}" / "labels" / f"{item_id}.json" for i in range(2)]
        if not all(p.exists() for p in paths):
            result["pending"].append(item_id)
            continue
        labels = [json.loads(p.read_text()) for p in paths]
        for index, label in enumerate(labels):
            if label.get("review_id") != mapping["review_id"] or label.get("item_id") != item_id or not str(label.get("note", "")).strip() or label.get("reviewer") != mapping["reviewers"][index] or label.get("source") != "human_attested" or label.get("evidence_hash") != entry["evidence_hash"] or set(label["criteria"]) != set(entry["criterion_ids"]):
                raise ValueError("human label provenance, identity or evidence mismatch")
        agreed = labels[0]["criteria"] == labels[1]["criteria"]
        decision = labels[0]
        if not agreed:
            path = root / "adjudications" / f"{item_id}.json"
            if not path.exists():
                result["conflicts"].append(item_id)
                continue
            decision = json.loads(path.read_text())
            if decision.get("source") != "human_adjudicated" or decision.get("evidence_hash") != entry["evidence_hash"] or set(decision["criteria"]) != set(entry["criterion_ids"]):
                raise ValueError("invalid adjudication provenance")
        verdict = aggregate_verdict([CriterionScore(criterion_id=k, verdict=v, reason="human") for k, v in decision["criteria"].items()])
        result["labels"][entry["run_id"]] = {**entry, "criteria": decision["criteria"],
                                             "verdict": verdict, "independent_agreement": agreed}
    return result


def comparison(evidence: list[EvidenceBundle], scores, human: dict | None = None, *,
               selection: dict | None = None, dataset_root: Path | None = None,
               holdout_manifest: dict | None = None, protocol: dict | None = None) -> dict:
    if selection is not None and protocol is not None:
        raise ValueError("selection and engineering protocol are mutually exclusive")
    if protocol is not None and any(e.provenance != "real" or e.split != "holdout" for e in evidence):
        raise ValueError("engineering protocol is for real holdout evidence only")
    if len({e.provenance for e in evidence}) > 1:
        raise ValueError("simulated and real evidence require separate reports")
    by_id = {e.run_id: e.verify() for e in evidence}
    if len(by_id) != len(evidence):
        raise ValueError("duplicate evidence runs")
    from kmb.scorers import _references
    score_map = {}
    for s in scores:
        if s.run_id not in by_id or s.evidence_hash != by_id[s.run_id].evidence_hash or s.task_id != by_id[s.run_id].task_id:
            raise ValueError("score not associated with the supplied frozen evidence")
        if s.status == "scored" and s.verdict != aggregate_verdict(s.criteria):
            raise ValueError("score aggregate contradicts criterion results")
        if len({c.criterion_id for c in s.criteria}) != len(s.criteria):
            raise ValueError("duplicate score criterion IDs")
        available_refs = _references(by_id[s.run_id])
        if any(ref not in available_refs for c in s.criteria for ref in c.evidence_refs):
            raise ValueError("score cites an unknown evidence reference")
        key = (s.run_id, s.scorer)
        if key in score_map:
            raise ValueError("duplicate scorer/run results; choose one frozen scoring batch")
        score_map[key] = s
    labels = (human or {}).get("labels", {})
    if not isinstance(labels, dict):
        raise ValueError("human labels must be a run-to-label mapping")  # noqa: TRY004 - invalid review JSON, handled by CLI
    known_rubrics = {}
    if labels and dataset_root is not None:
        from kmb.dataset import load_dataset
        known_rubrics = {t.id: r for t, r in load_dataset(dataset_root)}
    for run_id, label in labels.items():
        if not isinstance(label, dict) or run_id not in by_id or label.get("evidence_hash") != by_id[run_id].evidence_hash:
            raise ValueError("human labels don't belong to supplied evidence")
        bundle = by_id[run_id]
        if label.get("task_id") != bundle.task_id or any(
            key in label and label[key] != getattr(bundle, key) for key in ("split", "provenance")
        ):
            raise ValueError("human label task or provenance does not match evidence")
        criteria = label.get("criteria")
        if not isinstance(criteria, dict) or not criteria or any(
            not isinstance(key, str) or not key or not isinstance(value, str)
            or value not in {"pass", "fail", "undetermined"} for key, value in criteria.items()
        ):
            raise ValueError("human criteria must contain valid criterion IDs and verdicts")
        score_sets = [{c.criterion_id for c in s.criteria} for s in scores
                      if s.run_id == run_id and s.criteria]
        declared = label.get("criterion_ids")
        if declared is not None and (not isinstance(declared, list) or not declared
                                     or any(not isinstance(key, str) or not key for key in declared)
                                     or len(set(declared)) != len(declared)):
            raise ValueError("invalid human rubric criterion set")
        if known_rubrics:
            if bundle.task_id not in known_rubrics:
                raise ValueError("human label references an unknown dataset task")
            expected = {c.id for c in known_rubrics[bundle.task_id].criteria}
        else:
            expected = set(declared) if declared is not None else score_sets[0] if score_sets else None
        if (expected is None or set(criteria) != expected
                or (declared is not None and set(declared) != expected)
                or any(identifiers != expected for identifiers in score_sets)):
            raise ValueError("human criteria must cover the rubric criterion set exactly")
        derived = aggregate_verdict([CriterionScore(criterion_id=key, verdict=value, reason="human")
                                     for key, value in criteria.items()])
        if label.get("verdict") != derived:
            raise ValueError("human aggregate verdict contradicts criterion results")
    valid = [e for e in evidence if e.status in {"completed", "task_failed", "budget_exhausted"}]
    entries = []
    for variant in "ABC":
        corresponding = [score_map[(e.run_id, variant)] for e in valid if (e.run_id, variant) in score_map]
        decided = [s for s in corresponding if s.status == "scored" and s.verdict != "undetermined"]
        labelled = [e for e in valid if e.run_id in labels and labels[e.run_id]["verdict"] != "undetermined"]
        matched = [(score_map.get((e.run_id, variant)), labels[e.run_id]) for e in labelled]
        auto = [(s, h) for s, h in matched if s and s.status == "scored" and s.verdict != "undetermined"]
        correct = sum(s.verdict == h["verdict"] for s, h in auto)
        critical_false_pass = sum(c.critical and c.verdict == "pass" and h["criteria"].get(c.criterion_id) == "fail" for s, h in matched if s and s.status == "scored" for c in s.criteria)
        usages = [s.usage for s in corresponding]
        tokens = sum(u.input_tokens + u.output_tokens for u in usages) if usages and all(u.input_tokens is not None and u.output_tokens is not None for u in usages) else None
        coverage = len(decided) / len(valid) if valid else None
        agreement = correct / len(auto) if auto else None
        entries.append({"variant": variant, "valid_runs": len(valid), "scored_runs": len(corresponding),
                        "decided_runs": len(decided), "coverage": coverage,
                        "labelled_runs": len(labelled), "human_agreement": agreement,
                        "correct_automatic_fraction": correct / len(labelled) if labelled else None,
                        "critical_false_passes": critical_false_pass if labelled else None,
                        "tokens": tokens, "elapsed_seconds": sum(s.elapsed_seconds for s in corresponding),
                        "meets_internal_target": bool(labelled and len(labelled) == len(valid) and coverage is not None and coverage >= .8 and agreement is not None and agreement >= .9 and critical_false_pass == 0)})
    validation = {"status": "not_applicable", "selected": None, "eligible": False}
    if any(e.provenance == "real" and e.split == "holdout" for e in evidence):
        if dataset_root is None or (selection is None and protocol is None):
            raise ValueError("real holdout reporting requires a frozen selection or engineering protocol and dataset")
        validation = (validate_engineering_scores(evidence, scores, protocol, dataset_root, holdout_manifest)
                      if protocol is not None else
                      validate_holdout_scores(evidence, scores, selection, dataset_root, holdout_manifest))
        from kmb.dataset import load_dataset
        rubrics = {t.id: r for t, r in load_dataset(dataset_root, "holdout")}
        for run_id, label in labels.items():
            task_id = by_id[run_id].task_id
            if label.get("task_id") != task_id or label.get("rubric_hash") != rubric_hash(rubrics[task_id]):
                raise ValueError("holdout human rubric provenance changed or is missing")
    for entry in entries:
        entry["evaluation_role"] = ("frozen_candidate" if entry["variant"] == validation["selected"]
                                    else "exploratory" if validation["selected"] else "candidate_comparison")
    result = {"schema_version": "1", "total_runs": len(evidence), "valid_runs": len(valid),
            "infrastructure_or_unknown_runs": len(evidence) - len(valid),
            "provenance": sorted({e.provenance for e in evidence}),
            "splits": sorted({e.split for e in evidence}),
            "human_review": "available" if labels else "not_performed",
            "pending_human_items": len((human or {}).get("pending", [])),
            "conflicting_human_items": len((human or {}).get("conflicts", [])),
            "independent_validation": validation,
            "variants": entries,
            "evidence_hashes": {e.run_id: e.evidence_hash for e in evidence},
            "human_rubric_hashes": {run: {"task_id": label.get("task_id"), "rubric_hash": label.get("rubric_hash")} for run, label in labels.items()},
            "matrix": [{"task_id":e.task_id,"agent":e.agent,"agent_version":e.agent_version,
                        "run_id":e.run_id,"model":e.model.model_dump(),"environment":e.environment} for e in evidence]}
    if protocol is not None:
        result["engineering_validation"] = validation
        result["objective_results"] = objective_results(evidence, scores, dataset_root)
        result["scorer_differences"] = scorer_differences(evidence, scores)
    return result


def experiment_fingerprint(dataset_root: Path) -> str:
    if (dataset_root / "data" / "tasks").is_dir():
        dataset_root = dataset_root / "data"
    digest = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(p.name.encode() + p.read_bytes())
    for folder in ["tasks", "rubrics"]:
        for p in sorted((dataset_root / folder).glob("*.json")):
            digest.update((folder + "/" + p.name).encode() + p.read_bytes())
    lock = Path(__file__).parents[2] / "uv.lock"
    if lock.exists():
        digest.update(lock.read_bytes())
    return digest.hexdigest()


def freeze_selection(result: dict, output: Path, scores, dataset_root: Path, *,
                     calibration: Path | None = None) -> dict:
    if result["provenance"] != ["real"] or result["splits"] != ["selection"]:
        raise ValueError("selection requires real evidence exclusively from the selection split")
    if result["pending_human_items"] or result["conflicting_human_items"]:
        raise ValueError("human review must be complete and adjudicated")
    from collections import Counter

    from kmb.dataset import load_dataset
    expected = Counter((t.id, agent) for t, _ in load_dataset(dataset_root, "selection") for agent in ["openclaw", "hermes"])
    actual = Counter((r["task_id"], r["agent"]) for r in result["matrix"])
    if expected != actual or len(expected) != 36:
        raise ValueError("selection requires the complete 18-task x 2-agent preregistered matrix")
    expected_scores = {(row["run_id"], variant) for row in result["matrix"] for variant in "ABC"}
    if {(item.run_id, item.scorer) for item in scores} != expected_scores or len(scores) != len(expected_scores):
        raise ValueError("selection requires one A/B/C scoring result for every run, including explicit errors")
    if result["infrastructure_or_unknown_runs"]:
        raise ValueError("resolve incomplete execution before freezing a selection")
    rubrics = {t.id: rubric for t, rubric in load_dataset(dataset_root, "selection")}
    scorer_conditions = frozen_scorer_conditions(scores, rubrics)
    eligible = [v for v in result["variants"] if v["meets_internal_target"]
                and scorer_conditions[v["variant"]]["mode"] != "unavailable"]
    if not eligible:
        raise ValueError("no scorer meets the preregistered internal target")
    def rank(v):
        return (v["critical_false_passes"], -v["correct_automatic_fraction"],
                v["tokens"] if v["tokens"] is not None else float("inf"),
                v["elapsed_seconds"], "ACB".index(v["variant"]))
    from kmb import scorers
    code_hash = hashlib.sha256(Path(scorers.__file__).read_bytes()).hexdigest()
    from kmb.scorers import scoring_fingerprint
    human_hashes = result.get("human_rubric_hashes", {})
    if set(human_hashes) != set(result["evidence_hashes"]):
        raise ValueError("complete human rubric provenance required before selection")
    for label in human_hashes.values():
        if label["task_id"] not in rubrics or label["rubric_hash"] != rubric_hash(rubrics[label["task_id"]]):
            raise ValueError("human review rubric changed; repeat review against current rubric")
    for scored in scores:
        if scored.task_id not in rubrics or scored.scoring_fingerprint != scoring_fingerprint(rubrics[scored.task_id]):
            raise ValueError("scoring code, prompt or rubric changed since scoring; rescore selection evidence")
        requirements = {c.id: c for c in rubrics[scored.task_id].criteria}
        if ((scored.status == "scored" and {c.criterion_id for c in scored.criteria} != set(requirements))
                or any(c.criterion_id not in requirements or c.critical != requirements[c.criterion_id].critical
                       for c in scored.criteria)):
            raise ValueError("selection score criteria or critical flags differ from the rubric")
    if calibration is None:
        raise ValueError("selection requires preregistered scorer calibration controls")
    from kmb.calibration import validate_calibration
    controls, rejected_controls = {}, {}
    for candidate in eligible:
        variant = candidate["variant"]
        try:
            controls[variant] = validate_calibration(calibration, dataset_root, variant,
                                                     expected_judge=scorer_conditions[variant]["judge"])
        except ValueError as exc:
            rejected_controls[variant] = str(exc)
    eligible = [candidate for candidate in eligible if candidate["variant"] in controls]
    if not eligible:
        raise ValueError("no eligible scorer passed the preregistered calibration controls")
    best = min(eligible, key=rank)
    chosen_scores = [s.model_dump() for s in scores if s.scorer == best["variant"]]
    choice = {"schema_version": "2", "selected": best["variant"], "selection": result, "scorer_code_hash": code_hash,
              "experiment_fingerprint": experiment_fingerprint(dataset_root),
              "execution_conditions": frozen_conditions(result["matrix"]),
              "scorer_conditions": scorer_conditions,
              "calibration": controls[best["variant"]],
              "calibration_rejections": rejected_controls,
              "scoring_records_hash": hashlib.sha256(json.dumps(chosen_scores, sort_keys=True).encode()).hexdigest(),
              "scorer_models": [s.model.model_dump() for s in scores if s.scorer == best["variant"]],
              "notice": "冻结后留出集只用于独立验证，不用于重新选型。"}
    private_write(output, choice)
    return choice


def _needs_judge(variant: str, rubric: PrivateRubric) -> bool:
    return variant == "B" or (variant == "C" and any(c.kind == "semantic" for c in rubric.criteria))


def _judge_condition(model: dict, *, require_returned: bool) -> dict:
    """Keep every recorded judge setting; these parameters contain no run counters."""
    condition = {key: model.get(key) for key in ("requested", "returned", "provider", "parameters")}
    required = ("requested", "provider", "returned") if require_returned else ("requested", "provider")
    if any(not isinstance(condition[key], str) or condition[key] in {"", "unknown"} for key in required):
        raise ValueError("scorer requires observed judge identity before freezing or validation")
    parameters = condition["parameters"]
    if not isinstance(parameters, dict) or parameters.get("api_style") not in {"chat_completions", "responses"}:
        raise ValueError("scorer requires an observed judge API protocol")
    return condition


def frozen_scorer_conditions(scores, rubrics: dict[str, PrivateRubric]) -> dict:
    """Reject mixed judge identities even in a candidate that will not win."""
    conditions = {}
    for variant in "ABC":
        rows = [s for s in scores if s.scorer == variant]
        versions = {s.scorer_version for s in rows}
        if len(versions) != 1 or not next(iter(versions), ""):
            raise ValueError("selection contains inconsistent scorer versions")
        observed, configured = [], []
        required = [s for s in rows if _needs_judge(variant, rubrics[s.task_id])]
        for scored in required:
            identity = scored.model.model_dump()
            successful = scored.status == "scored" and scored.error is None
            if not successful and not any(identity.get(k) for k in ("requested", "returned", "provider")):
                continue  # Explicit failed/unconfigured candidate, not a verified judge.
            condition = _judge_condition(identity, require_returned=successful)
            configured.append({k: v for k, v in condition.items() if k != "returned"})
            if condition["returned"] not in {None, "", "unknown"}:
                observed.append(condition)
        if any(item != configured[0] for item in configured[1:]) or any(item != observed[0] for item in observed[1:]):
            raise ValueError(f"selection contains inconsistent judge conditions for scorer {variant}")
        conditions[variant] = {"scorer_version": next(iter(versions)),
                               "mode": "rules" if not required else "model" if observed else "unavailable",
                               "judge": observed[0] if observed else None}
    return conditions


def selection_manifest_hash(selected: dict) -> str:
    payload = json.dumps(selected, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def engineering_protocol_hash(protocol: dict) -> str:
    """A protocol is a separate artifact, never an alias for human selection."""
    return selection_manifest_hash(protocol)


def _canonical_records_hash(records) -> str:
    encoded = json.dumps([item.model_dump() for item in sorted(
        records, key=lambda item: (item.run_id, item.scorer))], sort_keys=True,
        ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _complete_matrix(evidence: list[EvidenceBundle], dataset_root: Path, split: str,
                     repeats: int) -> list[str]:
    from kmb.dataset import load_dataset

    tasks = [task.id for task, _ in load_dataset(dataset_root, split)]
    if len(tasks) != 18 or len(set(tasks)) != 18:
        raise ValueError(f"engineering {split} requires exactly 18 distinct tasks")
    expected = Counter({(task, agent): repeats for task in tasks for agent in ("openclaw", "hermes")})
    actual = Counter((bundle.task_id, bundle.agent) for bundle in evidence)
    if (actual != expected or len({bundle.run_id for bundle in evidence}) != 36 * repeats
            or any(bundle.provenance != "real" or bundle.split != split for bundle in evidence)):
        raise ValueError(f"engineering {split} requires complete 18 x 2 x {repeats} real evidence")
    for bundle in evidence:
        bundle.verify()
    return sorted(tasks)


def _validate_engineering_records(evidence: list[EvidenceBundle], scores, dataset_root: Path,
                                  split: str, conditions: dict | None = None) -> dict:
    from kmb.dataset import load_dataset
    from kmb.scorers import _references, scoring_fingerprint

    rubrics = {task.id: rubric for task, rubric in load_dataset(dataset_root, split)}
    by_id = {bundle.run_id: bundle for bundle in evidence}
    expected = {(run_id, variant) for run_id in by_id for variant in "ABC"}
    if len(scores) != len(expected) or {(s.run_id, s.scorer) for s in scores} != expected:
        raise ValueError("engineering protocol requires one A/B/C score for every real run")
    for scored in scores:
        bundle = by_id[scored.run_id]
        rubric = rubrics[bundle.task_id]
        requirements = {criterion.id: criterion for criterion in rubric.criteria}
        if (scored.task_id != bundle.task_id or scored.evidence_hash != bundle.evidence_hash
                or scored.scoring_fingerprint != scoring_fingerprint(rubric)
                or scored.verdict != aggregate_verdict(scored.criteria)
                or (scored.status == "scored" and {c.criterion_id for c in scored.criteria} != set(requirements))
                or any(c.criterion_id not in requirements or c.critical != requirements[c.criterion_id].critical
                       or any(ref not in _references(bundle) for ref in c.evidence_refs) for c in scored.criteria)):
            raise ValueError("engineering score rubric, version, criterion or evidence changed")
        if conditions is not None:
            condition = conditions[scored.scorer]
            if scored.scorer_version != condition["scorer_version"]:
                raise ValueError("engineering scoring version changed")
            if _needs_judge(scored.scorer, rubric):
                if condition["mode"] != "model":
                    raise ValueError("engineering model scorer lacks frozen judge")
                identity = scored.model.model_dump()
                successful = scored.status == "scored" and scored.error is None
                if not successful and not any(identity.get(k) for k in ("requested", "returned", "provider")):
                    continue
                observed = _judge_condition(identity, require_returned=successful)
                frozen = condition["judge"]
                if any(observed[k] != frozen[k] for k in ("requested", "provider", "parameters")) or (
                        observed["returned"] not in {None, frozen["returned"]}):
                    raise ValueError("engineering judge identity changed")
    return rubrics


def freeze_engineering(evidence: list[EvidenceBundle], scores, output: Path,
                       dataset_root: Path, *, calibration: Path) -> dict:
    """Freeze observable engineering conditions without inventing human labels or a winner."""
    tasks = _complete_matrix(evidence, dataset_root, "selection", 1)
    rubrics = _validate_engineering_records(evidence, scores, dataset_root, "selection")
    if any(bundle.status in {"infrastructure_error", "execution_unknown"} for bundle in evidence):
        raise ValueError("resolve uncertain selection execution before engineering freeze")
    scorer_conditions = frozen_scorer_conditions(scores, rubrics)
    if any(item["mode"] == "unavailable" for item in scorer_conditions.values()):
        raise ValueError("engineering comparison requires all A/B/C scorers available")
    from kmb.calibration import control_cases, suite_hash, validate_calibration
    controls = {variant: validate_calibration(
        calibration, dataset_root, variant, expected_judge=scorer_conditions[variant]["judge"])
        for variant in "ABC"}
    matrix = [{"agent": e.agent, "agent_version": e.agent_version,
               "model": e.model.model_dump(), "environment": e.environment} for e in evidence]
    execution_conditions = frozen_conditions(matrix)
    for item in execution_conditions.values():
        if item["parameters"].get("max_tool_actions") != 20 or item["parameters"].get("timeout_seconds") != 600:
            raise ValueError("engineering execution budget must be 20 actions and 600 seconds")
    from kmb.dataset import load_dataset
    holdout_tasks = sorted(task.id for task, _ in load_dataset(dataset_root, "holdout"))
    if len(holdout_tasks) != 18 or len(set(holdout_tasks)) != 18:
        raise ValueError("engineering holdout requires exactly 18 distinct tasks")
    protocol = {"schema_version": "1", "protocol_type": "engineering_validation",
                "human_validation": "not_performed", "selected_scorer": None,
                "experiment_fingerprint": experiment_fingerprint(dataset_root),
                "selection_matrix": {"split": "selection", "tasks": tasks,
                                     "agents": ["openclaw", "hermes"], "repeats": 1},
                "selection_evidence_hashes": {e.run_id: e.evidence_hash for e in evidence},
                "selection_scoring_records_hash": _canonical_records_hash(scores),
                "execution_conditions": execution_conditions,
                "scorer_conditions": scorer_conditions,
                "controls": {"suite_hash": suite_hash(control_cases()), "variants": controls},
                "holdout_matrix": {"split": "holdout", "tasks": holdout_tasks,
                                   "agents": ["openclaw", "hermes"], "repeats": 3},
                "budget": {"seconds_per_case": 600, "tool_actions_per_case": 20},
                "notice": "工程协议只验证固定实验与设计反例；无独立人审，不选评分器赢家。"}
    private_write(output, protocol)
    return protocol


def validate_engineering_protocol(protocol: dict, dataset_root: Path) -> dict:
    from kmb.calibration import control_cases, suite_hash
    from kmb.dataset import load_dataset

    if (protocol.get("schema_version") != "1" or protocol.get("protocol_type") != "engineering_validation"
            or protocol.get("human_validation") != "not_performed" or protocol.get("selected_scorer") is not None
            or protocol.get("experiment_fingerprint") != experiment_fingerprint(dataset_root)):
        raise ValueError("invalid or changed engineering protocol")
    if protocol.get("budget") != {"seconds_per_case": 600, "tool_actions_per_case": 20}:
        raise ValueError("engineering execution budget changed")
    for split, repeats in (("selection", 1), ("holdout", 3)):
        tasks = sorted(task.id for task, _ in load_dataset(dataset_root, split))
        if (len(tasks) != 18 or protocol.get(f"{split}_matrix") != {
                "split": split, "tasks": tasks, "agents": ["openclaw", "hermes"], "repeats": repeats}):
            raise ValueError("engineering dataset or experiment matrix changed")
    hashes = protocol.get("selection_evidence_hashes")
    score_hash = protocol.get("selection_scoring_records_hash")
    if (not isinstance(hashes, dict) or len(hashes) != 36 or
            any(not isinstance(value, str) or len(value) != 64 for value in hashes.values()) or
            not isinstance(score_hash, str) or len(score_hash) != 64):
        raise ValueError("engineering selection evidence or scoring binding missing")
    conditions = protocol.get("execution_conditions")
    if not isinstance(conditions, dict) or set(conditions) != {"openclaw", "hermes"}:
        raise ValueError("engineering execution conditions missing")
    scorers = protocol.get("scorer_conditions")
    if not isinstance(scorers, dict) or set(scorers) != set("ABC") or any(
            not isinstance(value, dict) or value.get("mode") not in {"rules", "model"}
            or not value.get("scorer_version") for value in scorers.values()):
        raise ValueError("engineering scorer conditions missing")
    controls = protocol.get("controls")
    if (not isinstance(controls, dict) or controls.get("suite_hash") != suite_hash(control_cases())
            or not isinstance(controls.get("variants"), dict)
            or set(controls["variants"]) != set("ABC") or any(
                item.get("selected") != variant or item.get("suite_hash") != controls["suite_hash"]
                or item.get("experiment_fingerprint") != protocol["experiment_fingerprint"]
                or item.get("critical_false_passes") != 0
                or item.get("critical_expected_matches") != item.get("critical_criteria")
                for variant, item in controls["variants"].items())):
        raise ValueError("engineering designed control binding changed")
    return protocol


def validate_engineering_evidence(evidence: list[EvidenceBundle], protocol: dict,
                                  dataset_root: Path, manifest: dict | None) -> None:
    validate_engineering_protocol(protocol, dataset_root)
    _complete_matrix(evidence, dataset_root, "holdout", 3)
    if (not isinstance(manifest, dict)
            or manifest.get("protocol_hash") != engineering_protocol_hash(protocol)
            or manifest.get("selection") is not None or manifest.get("selection_hash") is not None
            or any(manifest.get(key) != protocol["holdout_matrix"][key]
                   for key in ("split", "tasks", "agents", "repeats"))
            or manifest.get("provenance") != "real"):
        raise ValueError("holdout evidence is not bound to the frozen engineering protocol")
    _validate_holdout_execution_conditions(evidence, protocol["execution_conditions"])


def validate_engineering_provider(protocol: dict, provider) -> None:
    for variant in ("B", "C"):
        condition = protocol["scorer_conditions"][variant]
        if condition["mode"] != "model":
            continue
        if provider is None:
            raise ValueError("engineering scorer requires the frozen model judge")
        actual = _judge_condition(provider.public_identity(), require_returned=False)
        if any(actual[key] != condition["judge"][key]
               for key in ("requested", "provider", "parameters")):
            raise ValueError("engineering judge configuration changed")


def validate_engineering_scores(evidence: list[EvidenceBundle], scores, protocol: dict,
                                dataset_root: Path, manifest: dict | None) -> dict:
    validate_engineering_evidence(evidence, protocol, dataset_root, manifest)
    _validate_engineering_records(evidence, scores, dataset_root, "holdout",
                                  protocol["scorer_conditions"])
    scoring_errors = sum(score.status != "scored" or score.error is not None for score in scores)
    execution_errors = sum(e.status in {"infrastructure_error", "execution_unknown"} for e in evidence)
    return {"status": "bound" if not scoring_errors and not execution_errors else "incomplete",
            "eligible": not scoring_errors and not execution_errors, "selected": None,
            "protocol_type": "engineering_validation", "protocol_hash": engineering_protocol_hash(protocol),
            "scoring_errors": scoring_errors, "execution_errors": execution_errors,
            "human_validation": "not_performed", "scorer_winner": None,
            "notice": "工程协议绑定仅确认可复现条件；语义评分未获独立人工验证。"}


def objective_results(evidence: list[EvidenceBundle], scores, dataset_root: Path) -> dict:
    from kmb.dataset import load_dataset

    rubrics = {task.id: rubric for task, rubric in load_dataset(dataset_root)}
    score_map = {(score.run_id, score.scorer): score for score in scores}
    rows = []
    for bundle in evidence:
        objective = {criterion.id for criterion in rubrics[bundle.task_id].criteria
                     if criterion.kind != "semantic"}
        if not objective:
            continue
        scored = score_map.get((bundle.run_id, "A"))
        verdicts = {criterion.criterion_id: criterion.verdict for criterion in scored.criteria} if scored and scored.status == "scored" else {}
        outcome = ("pass" if all(verdicts.get(key) == "pass" for key in objective)
                   else "fail" if any(verdicts.get(key) == "fail" for key in objective)
                   else "undetermined")
        rows.append({"run_id": bundle.run_id, "task_id": bundle.task_id, "agent": bundle.agent,
                     "ability": bundle.ability, "execution_status": bundle.status,
                     "objective_verdict": outcome, "criteria": sorted(objective)})
    grouped = {}
    for agent in sorted({row["agent"] for row in rows}):
        for ability in sorted({row["ability"] for row in rows}):
            group = [row for row in rows if row["agent"] == agent and row["ability"] == ability]
            if group:
                grouped[f"{agent}:{ability}"] = {"runs": len(group), **{
                    verdict: sum(row["objective_verdict"] == verdict for row in group)
                    for verdict in ("pass", "fail", "undetermined")}}
    return {"source": "rule_scorer_A_objective_criteria_only", "rows": rows, "by_agent_ability": grouped}


def scorer_differences(evidence: list[EvidenceBundle], scores) -> dict:
    score_map = {(score.run_id, score.scorer): score for score in scores}
    rows = []
    for bundle in evidence:
        outcomes = {variant: score_map[(bundle.run_id, variant)].verdict
                    if (bundle.run_id, variant) in score_map else "missing" for variant in "ABC"}
        if len(set(outcomes.values())) > 1:
            rows.append({"run_id": bundle.run_id, "task_id": bundle.task_id,
                         "agent": bundle.agent, "verdicts": outcomes})
    return {"disagreement_runs": len(rows), "rows": rows,
            "notice": "版本间一致性不是与人工判断的一致率。"}


def validate_selection_manifest(selected: dict, dataset_root: Path) -> dict:
    if selected.get("schema_version") != "2" or selected.get("selected") not in {"A", "B", "C"}:
        raise ValueError("selection manifest lacks frozen scorer conditions; repeat selection freeze")
    if selected.get("experiment_fingerprint") != experiment_fingerprint(dataset_root):
        raise ValueError("code/data changed since selection; holdout cannot proceed")
    from kmb.calibration import control_cases, suite_hash
    cases = control_cases()
    critical_count = sum(criterion.critical for case in cases for criterion in case.rubric.criteria)
    controls = selected.get("calibration")
    if (not isinstance(controls, dict) or controls.get("selected") != selected["selected"]
            or controls.get("experiment_fingerprint") != selected["experiment_fingerprint"]
            or controls.get("suite_hash") != suite_hash(cases)
            or controls.get("cases") != len(cases)
            or controls.get("critical_false_passes") != 0
            or controls.get("critical_unsupported_passes") != 0
            or not isinstance(controls.get("critical_criteria"), int)
            or controls["critical_criteria"] != critical_count
            or controls.get("critical_expected_matches") != controls["critical_criteria"]
            or not isinstance(controls.get("scoring_records_hash"), str)
            or len(controls["scoring_records_hash"]) != 64
            or any(c not in "0123456789abcdef" for c in controls["scoring_records_hash"])):
        raise ValueError("selection has no valid frozen calibration binding")
    condition = selected.get("scorer_conditions", {}).get(selected["selected"])
    if not condition or condition.get("mode") not in {"rules", "model"} or not condition.get("scorer_version"):
        raise ValueError("selected scorer has no usable frozen conditions")
    if condition["mode"] == "model":
        judge = condition.get("judge") or {}
        _judge_condition(judge, require_returned=True)
        configured = controls.get("configured_model")
        if (controls.get("selected_models") != [judge]
                or controls.get("expected_match") != len(cases)
                or not isinstance(configured, dict)
                or any(configured.get(key) != judge.get(key) for key in ("requested", "provider", "parameters"))):
            raise ValueError("selected judge differs from frozen calibration binding")
    return condition


def validate_holdout_evidence(evidence: list[EvidenceBundle], selected: dict, dataset_root: Path,
                              manifest: dict | None) -> None:
    from collections import Counter

    from kmb.dataset import load_dataset

    validate_selection_manifest(selected, dataset_root)
    tasks = {t.id for t, _ in load_dataset(dataset_root, "holdout")}
    if not manifest or manifest.get("selection_hash") != selection_manifest_hash(selected):
        raise ValueError("holdout run is not bound to this frozen selection manifest")
    if (manifest.get("split") != "holdout" or manifest.get("provenance") != "real"
            or manifest.get("repeats") != 3 or sorted(manifest.get("agents", [])) != ["hermes", "openclaw"]
            or sorted(manifest.get("tasks", [])) != sorted(tasks)):
        raise ValueError("holdout run manifest does not describe the complete preregistered matrix")
    expected = Counter({(task, agent): 3 for task in tasks for agent in ("openclaw", "hermes")})
    actual = Counter((e.task_id, e.agent) for e in evidence)
    if (len(tasks) != 18 or expected != actual or len({e.run_id for e in evidence}) != 108
            or any(e.split != "holdout" or e.provenance != "real" for e in evidence)):
        raise ValueError("holdout requires the complete 18-task x 2-agent x 3-repeat evidence matrix")
    _validate_holdout_execution_conditions(evidence, selected.get("execution_conditions"))


def _validate_holdout_execution_conditions(evidence: list[EvidenceBundle], conditions: dict | None) -> None:
    """Compare known observations without inventing identities for failed trials.

    Selection and every scorable holdout trial still require the full observed
    configuration. An infrastructure/unknown trial may stop before observing a
    model response, image or request parameters; its missing observations do not
    establish either sameness or a change. Such trials remain ineligible in
    validate_holdout_scores(), even when every known field matches.
    """
    if not isinstance(conditions, dict) or set(conditions) != {"openclaw", "hermes"}:
        raise ValueError("selection manifest has no complete frozen execution conditions")

    def observed(value) -> bool:
        return value is not None and not (isinstance(value, str) and value in {"", "unknown"})

    for bundle in evidence:
        row = {"agent": bundle.agent, "agent_version": bundle.agent_version,
               "model": bundle.model.model_dump(), "environment": bundle.environment}
        expected = conditions[bundle.agent]
        if bundle.status not in {"infrastructure_error", "execution_unknown"}:
            if frozen_conditions([row]) != {bundle.agent: expected}:
                raise ValueError("observed holdout conditions differ from selection")
            continue

        parameters = bundle.model.parameters
        known = {"requested": bundle.model.requested, "returned": bundle.model.returned,
                 "provider": bundle.model.provider, "transport": parameters.get("transport"),
                 "image_id": bundle.environment.get("image_id"), "agent_version": bundle.agent_version}
        if any(observed(value) and value != expected.get(key) for key, value in known.items()):
            raise ValueError("observed holdout conditions differ from selection")
        # An aggregate returned=None can also mean conflicting observed models;
        # never mistake that conflict for an unobserved model on a failed trial.
        returned_models = parameters.get("returned_models", [])
        if not isinstance(returned_models, list) or any(
            observed(model) and model != expected.get("returned") for model in returned_models
        ):
            raise ValueError("observed holdout conditions differ from selection")
        expected_parameters = expected.get("parameters", {})
        for key, value in parameters.items():
            if key in {"upstream_calls", "proposed_tool_calls", "returned_models"}:
                continue
            if key == "requested_inference_parameters":
                # A failed run may have observed only a prefix of the request
                # parameter sets. Every set actually used must already be frozen.
                frozen_requests = expected_parameters.get(key, [])
                if not isinstance(value, list) or any(item not in frozen_requests for item in value):
                    raise ValueError("observed holdout conditions differ from selection")
            elif observed(value) and (key not in expected_parameters or value != expected_parameters[key]):
                raise ValueError("observed holdout conditions differ from selection")


def validate_holdout_provider(selected: dict, provider) -> None:
    """Check configured settings before any judge request; response identity is checked later."""
    condition = selected["scorer_conditions"][selected["selected"]]
    if condition["mode"] == "rules":
        return
    if provider is None:
        raise ValueError("the frozen holdout scorer requires its configured judge")
    actual = _judge_condition(provider.public_identity(), require_returned=False)
    expected = condition["judge"]
    if any(actual[key] != expected[key] for key in ("requested", "provider", "parameters")):
        raise ValueError("judge configuration changed since selection")


def validate_holdout_scores(evidence: list[EvidenceBundle], scores, selected: dict,
                            dataset_root: Path, manifest: dict | None) -> dict:
    from kmb.dataset import load_dataset
    from kmb.scorers import scoring_fingerprint

    validate_holdout_evidence(evidence, selected, dataset_root, manifest)
    rubrics = {t.id: r for t, r in load_dataset(dataset_root, "holdout")}
    chosen = selected["selected"]
    condition = selected["scorer_conditions"][chosen]
    rows = [s for s in scores if s.scorer == chosen]
    by_id = {e.run_id: e for e in evidence}
    if len(rows) != len(evidence) or {s.run_id for s in rows} != set(by_id):
        raise ValueError("holdout scores must include the frozen selected scorer for every run")
    incomplete = 0
    for scored in rows:
        if (scored.task_id != by_id[scored.run_id].task_id or scored.evidence_hash != by_id[scored.run_id].evidence_hash
                or scored.scorer_version != condition["scorer_version"]
                or scored.scoring_fingerprint != scoring_fingerprint(rubrics[scored.task_id])):
            raise ValueError("holdout scoring version, rubric or frozen evidence changed")
        successful = scored.status == "scored" and scored.error is None
        incomplete += not successful
        if _needs_judge(chosen, rubrics[scored.task_id]):
            if condition["mode"] != "model":
                raise ValueError("selected scorer has no frozen judge for this holdout task")
            identity = scored.model.model_dump()
            if not successful and not any(identity.get(k) for k in ("requested", "returned", "provider")):
                continue  # Retain explicit failures without calling them verified judgments.
            actual = _judge_condition(identity, require_returned=successful)
            expected = condition["judge"]
            if any(actual[key] != expected[key] for key in ("requested", "provider", "parameters")) or (actual["returned"] is not None and actual["returned"] != expected["returned"]):
                raise ValueError("holdout judge identity or configuration changed since selection")
    execution_incomplete = sum(e.status in {"infrastructure_error", "execution_unknown"} for e in evidence)
    return {"status": "bound" if not incomplete and not execution_incomplete else "incomplete",
            "eligible": not incomplete and not execution_incomplete, "selected": chosen,
            "selection_hash": selection_manifest_hash(selected),
            "selected_scoring_errors": incomplete, "execution_errors": execution_incomplete,
            "exploratory_variants": [v for v in "ABC" if v != chosen],
            "notice": "资格仅表示与冻结协议一致，不代表准确率达标；其他版本只作探索展示，不重新选型。"}


def frozen_conditions(matrix: list[dict]) -> dict:
    """Only stable, observed execution conditions; omit per-trial token counts."""
    conditions = {}
    for row in matrix:
        model = row["model"]
        parameters = {key: value for key, value in model.get("parameters", {}).items()
                      if key not in {"upstream_calls", "proposed_tool_calls", "returned_models"}}
        item = {"requested": model.get("requested"), "returned": model.get("returned"),
                "provider": model.get("provider"),
                "transport": parameters.get("transport"),
                "image_id": row["environment"].get("image_id"),
                "agent_version": row.get("agent_version")}
        if any(value in {None, "", "unknown", "configured_host_gateway"} for value in item.values()):
            raise ValueError("selection requires observed model, provider, protocol and agent image versions")
        endpoint = parameters.get("endpoint_sha256")
        if not isinstance(endpoint, str) or len(endpoint) != 64 or any(c not in "0123456789abcdef" for c in endpoint):
            raise ValueError("selection requires an observed endpoint fingerprint")
        item["parameters"] = parameters
        agent = row["agent"]
        if agent in conditions and conditions[agent] != item:
            raise ValueError("selection contains inconsistent execution conditions")
        conditions[agent] = item
    return conditions


def validate_holdout_conditions(selected: dict) -> None:
    """Fail before execution if the configured model or local image changed."""
    import os
    import subprocess

    from kmb.adapters import AGENTS
    from kmb.environment import docker_prefix
    from kmb.provider import JudgeProvider

    provider = JudgeProvider.from_env()
    conditions = selected.get("execution_conditions")
    if not conditions or set(conditions) != set(AGENTS):
        raise ValueError("selection manifest has no complete frozen execution conditions")
    for agent, expected in conditions.items():
        if provider.model != expected["requested"] or provider.api_style != expected["transport"]:
            raise ValueError("model or API protocol changed since selection")
        identity = provider.public_identity()
        if (identity["provider"] != expected.get("provider")
                or identity["parameters"].get("endpoint_sha256") != expected.get("parameters", {}).get("endpoint_sha256")):
            raise ValueError("execution provider endpoint changed since selection")
        image = os.environ.get(f"KMB_{agent.upper()}_IMAGE", AGENTS[agent])
        result = subprocess.run(docker_prefix() + ["image", "inspect", image, "--format", "{{json .Id}}"],
                                capture_output=True, text=True, timeout=15, check=False)
        if result.returncode or json.loads(result.stdout) != expected["image_id"]:
            raise ValueError("agent image changed or is unavailable since selection")
