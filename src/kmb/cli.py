from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from kmb.dataset import load_dataset, pilot_tasks, validate_dataset
from kmb.provider import ProviderError
from kmb.storage import load_evidence_tree, load_scores, save_score, write_json


def default_data() -> Path:
    repository = Path(__file__).parents[2] / "data"
    return repository if repository.exists() else Path(__file__).parent / "data"


def emit(data):
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def configured_provider():
    import os

    from kmb.provider import JudgeProvider
    if not all(os.environ.get(k) for k in ["KMB_BASE_URL", "KMB_API_KEY", "KMB_MODEL"]):
        return None
    return JudgeProvider.from_env()


async def score_batch(evidence_path: Path, score_path: Path, dataset_root: Path,
                      variant: str, use_provider: bool = True, selection: dict | None = None,
                      protocol: dict | None = None):
    from kmb.scorers import score
    if selection is not None and protocol is not None:
        raise ValueError("selection and engineering protocol are mutually exclusive")
    if score_path.exists() and any(score_path.rglob("*.json")):
        raise ValueError("score output already contains a batch; use a new output directory")
    rubrics = {t.id: r for t, r in load_dataset(dataset_root)}
    evidence = load_evidence_tree(evidence_path)
    if not evidence:
        raise ValueError("no evidence found")
    holdout = any(e.provenance == "real" and e.split == "holdout" for e in evidence)
    if protocol is not None and (not holdout or any(e.provenance != "real" or e.split != "holdout" for e in evidence)):
        raise ValueError("engineering protocol is for real holdout evidence only")
    manifest = None
    if holdout:
        from kmb.review import validate_engineering_evidence, validate_holdout_evidence
        if selection is None and protocol is None:
            raise ValueError("real holdout scoring requires --selection or --protocol")
        manifest = read_holdout_manifest(evidence_path)
        if protocol is not None:
            validate_engineering_evidence(evidence, protocol, dataset_root, manifest)
            if variant != "all":
                raise ValueError("engineering holdout requires all three scoring variants")
        else:
            validate_holdout_evidence(evidence, selection, dataset_root, manifest)
        if selection is not None and variant not in {"all", selection["selected"]}:
            raise ValueError("holdout scoring must include the frozen selected variant; use all to compare ABC")
    provider = configured_provider() if use_provider else None
    if holdout:
        from kmb.review import validate_engineering_provider, validate_holdout_provider
        if protocol is not None:
            validate_engineering_provider(protocol, provider)
        else:
            validate_holdout_provider(selection, provider)
    count = 0
    scored = []
    for bundle in evidence:
        if bundle.task_id not in rubrics:
            raise ValueError("evidence references unknown task")
        for v in ("ABC" if variant == "all" else variant):
            result = await score(bundle, rubrics[bundle.task_id], v, provider)
            save_score(result, score_path)
            scored.append(result)
            count += 1
    validation = None
    if holdout:
        from kmb.review import validate_engineering_scores, validate_holdout_scores
        validation = (validate_engineering_scores(evidence, scored, protocol, dataset_root, manifest)
                      if protocol is not None else
                      validate_holdout_scores(evidence, scored, selection, dataset_root, manifest))
    return {"evidence": len(evidence), "scores": count, "provider_configured": provider is not None,
            "output": str(score_path.resolve()), "independent_validation": validation}


def read_holdout_manifest(evidence_path: Path) -> dict:
    path = evidence_path / "manifest.json"
    if not path.is_file():
        raise ValueError("real holdout requires its run-root --evidence directory with manifest.json")
    return json.loads(path.read_text())


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kmb", description="openKylin长期记忆评测：真实证据与模拟验证严格分开")
    p.add_argument("--dataset", type=Path, default=default_data())
    commands = p.add_subparsers(dest="command", required=True)
    d = commands.add_parser("dataset")
    d.add_argument("action", choices=["validate", "list"])
    d.add_argument("--split", choices=["dev", "selection", "holdout"])
    commands.add_parser("doctor")
    commands.add_parser("probe-model")
    r = commands.add_parser("run")
    r.add_argument("--agent", choices=["simulation", "openclaw", "hermes", "both"], required=True)
    r.add_argument("--pilot", action="store_true")
    r.add_argument("--split", choices=["dev", "selection", "holdout"], default="dev")
    r.add_argument("--task")
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--behavior", choices=["expected", "empty", "infra_error"], default="expected")
    r_validation = r.add_mutually_exclusive_group()
    r_validation.add_argument("--selection", type=Path)
    r_validation.add_argument("--protocol", type=Path)
    r.add_argument("--output", type=Path, required=True)
    s = commands.add_parser("score")
    s.add_argument("--evidence", type=Path, required=True)
    s.add_argument("--variant", choices=["A", "B", "C", "all"], default="all")
    s_validation = s.add_mutually_exclusive_group()
    s_validation.add_argument("--selection", type=Path, help="human-selected scorer protocol")
    s_validation.add_argument("--protocol", type=Path, help="engineering validation protocol")
    s.add_argument("--output", type=Path, required=True)
    for name in ["report", "compare", "freeze"]:
        item = commands.add_parser(name)
        item.add_argument("--evidence", type=Path, required=True)
        item.add_argument("--scores", type=Path, required=True)
        item.add_argument("--human", type=Path)
        if name != "freeze":
            validation = item.add_mutually_exclusive_group()
            validation.add_argument("--selection", type=Path, help="human-selected scorer protocol")
            validation.add_argument("--protocol", type=Path, help="engineering validation protocol")
        else:
            item.add_argument("--calibration", type=Path, required=True, help="validated designed control run directory")
        item.add_argument("--output", type=Path, required=True)
    engineering = commands.add_parser("freeze-engineering")
    engineering.add_argument("--evidence", type=Path, required=True)
    engineering.add_argument("--scores", type=Path, required=True)
    engineering.add_argument("--calibration", type=Path, required=True)
    engineering.add_argument("--output", type=Path, required=True)
    demo = commands.add_parser("demo")
    demo.add_argument("--output", type=Path, default=Path("reports/demo"))
    review = commands.add_parser("review")
    actions = review.add_subparsers(dest="action", required=True)
    export = actions.add_parser("export")
    export.add_argument("--evidence", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--reviewers", nargs=2, default=["reviewer-1", "reviewer-2"])
    for name in ["label", "adjudicate"]:
        action = actions.add_parser(name)
        action.add_argument("--directory", type=Path, required=True)
        action.add_argument("--item", required=True)
        action.add_argument("--criteria", required=True, help='JSON: {"criterion-id":"pass|fail|undetermined"}')
        action.add_argument("--note", required=True)
        if name == "label":
            action.add_argument("--reviewer", required=True)
    merge = actions.add_parser("merge")
    merge.add_argument("--directory", type=Path, required=True)
    merge.add_argument("--output", type=Path, required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "dataset":
            if args.action == "validate":
                result = validate_dataset(args.dataset)
                emit(result)
                return 1 if result.get("errors") else 0
            emit([{"id": t.id, "ability": t.ability, "family": t.family, "split": t.split} for t, _ in load_dataset(args.dataset, args.split)])
        elif args.command == "doctor":
            from kmb.environment import doctor
            emit(doctor())
        elif args.command == "probe-model":
            provider = configured_provider()
            if provider is None:
                raise ValueError("KMB_BASE_URL / KMB_API_KEY / KMB_MODEL not configured")
            result = asyncio.run(provider.probe())
            emit(result)
            return 0 if result.get("ok", result.get("status") == "ready") else 1
        elif args.command == "run":
            from kmb.inspect_bridge import run_inspect
            from kmb.review import engineering_protocol_hash, selection_manifest_hash
            selected = protocol = None
            if args.repeat < 1 or args.repeat > 3:
                raise ValueError("repeat must be 1..3")
            if args.output.exists() and any(args.output.iterdir()):
                raise ValueError("run output must be a new or empty directory")
            if args.pilot and args.split != "dev":
                raise ValueError("pilot is a development-only collection")
            if args.split == "holdout" and args.agent != "simulation":
                from kmb.review import (
                    validate_engineering_protocol,
                    validate_holdout_conditions,
                    validate_selection_manifest,
                )
                if args.selection:
                    selected = json.loads(args.selection.read_text())
                    validate_selection_manifest(selected, args.dataset)
                    validate_holdout_conditions(selected)
                elif args.protocol:
                    protocol = json.loads(args.protocol.read_text())
                    validate_engineering_protocol(protocol, args.dataset)
                    validate_holdout_conditions(protocol)
                else:
                    raise ValueError("real holdout requires a frozen selection or engineering protocol")
                if args.agent != "both" or args.repeat != 3 or args.task:
                    raise ValueError("formal holdout requires both agents, all holdout tasks and three repeats")
            elif args.protocol:
                raise ValueError("engineering protocol is for formal real holdout only")
            pairs = pilot_tasks(args.dataset) if args.pilot else load_dataset(args.dataset, args.split)
            if args.task:
                pairs = [(t, r) for t, r in pairs if t.id == args.task]
            if not pairs:
                raise ValueError("no tasks selected")
            agents = ["openclaw", "hermes"] if args.agent == "both" else [args.agent]
            manifest = {"id": uuid.uuid4().hex, "tasks": [t.id for t, _ in pairs],
                       "agents": agents, "repeats": args.repeat, "split": args.split,
                       "provenance": "simulated" if args.agent == "simulation" else "real",
                       "selection": str(args.selection) if args.selection else None,
                       "selection_hash": selection_manifest_hash(selected) if selected else None,
                       "protocol": str(args.protocol) if args.protocol else None,
                       "protocol_hash": engineering_protocol_hash(protocol) if protocol else None}
            write_json(args.output / "manifest.json", manifest, exclusive=True)
            logs = []
            for agent in agents:
                for repeat in range(args.repeat):
                    logs.extend(run_inspect(pairs, args.dataset, args.output / agent / str(repeat + 1), agent, args.behavior))
            bundles = load_evidence_tree(args.output)
            if args.split == "holdout" and args.agent != "simulation":
                from kmb.review import validate_engineering_evidence, validate_holdout_evidence
                if protocol is not None:
                    validate_engineering_evidence(bundles, protocol, args.dataset, manifest)
                else:
                    validate_holdout_evidence(bundles, selected, args.dataset, manifest)
            emit({"execution_status": [b.status for b in bundles], "inspect_logs": len(logs), "status": [x.status for x in logs], "output": args.output.resolve()})
            return 0 if all(x.status == "success" for x in logs) and len(bundles) == len(pairs) * len(agents) * args.repeat and all(b.status not in {"infrastructure_error", "execution_unknown"} for b in bundles) else 1
        elif args.command == "score":
            selected = json.loads(args.selection.read_text()) if args.selection else None
            protocol = json.loads(args.protocol.read_text()) if args.protocol else None
            emit(asyncio.run(score_batch(args.evidence, args.output, args.dataset, args.variant,
                                        selection=selected, protocol=protocol)))
        elif args.command == "freeze-engineering":
            from kmb.review import freeze_engineering
            emit(freeze_engineering(load_evidence_tree(args.evidence), load_scores(args.scores),
                                    args.output, args.dataset, calibration=args.calibration))
        elif args.command in {"report", "compare", "freeze"}:
            from kmb.review import comparison, freeze_selection
            evidence, scores = load_evidence_tree(args.evidence), load_scores(args.scores)
            if not evidence:
                raise ValueError("no evidence supplied")
            human = json.loads(args.human.read_text()) if args.human else None
            selected = json.loads(args.selection.read_text()) if getattr(args, "selection", None) else None
            protocol = json.loads(args.protocol.read_text()) if getattr(args, "protocol", None) else None
            manifest = read_holdout_manifest(args.evidence) if any(e.provenance == "real" and e.split == "holdout" for e in evidence) else None
            if args.command == "report":
                from kmb.report import render_report
                emit({"report": render_report(evidence, scores, args.output, human, selection=selected,
                                              dataset_root=args.dataset, holdout_manifest=manifest,
                                              protocol=protocol).resolve()})
            else:
                result = comparison(evidence, scores, human, selection=selected,
                                    dataset_root=args.dataset, holdout_manifest=manifest,
                                    protocol=protocol)
                if args.command == "freeze":
                    result = freeze_selection(result, args.output, scores, args.dataset, calibration=args.calibration)
                else:
                    write_json(args.output, result, exclusive=True)
                emit(result)
        elif args.command == "review":
            from kmb.review import adjudicate, consensus, export_review, record_label
            if args.action == "export":
                rubrics = {t.id: r for t, r in load_dataset(args.dataset)}
                emit(export_review(load_evidence_tree(args.evidence), rubrics, args.output, tuple(args.reviewers)))
            elif args.action == "label":
                emit({"label": record_label(args.directory, args.reviewer, args.item, json.loads(args.criteria), args.note)})
            elif args.action == "adjudicate":
                emit({"adjudication": adjudicate(args.directory, args.item, json.loads(args.criteria), args.note)})
            else:
                result = consensus(args.directory)
                write_json(args.output, result, exclusive=True)
                emit(result)
        elif args.command == "demo":
            from kmb.inspect_bridge import run_inspect
            from kmb.report import render_report
            if args.output.exists() and any(args.output.iterdir()):
                raise ValueError("demo output must be new/empty; original evidence is preserved")
            pairs = pilot_tasks(args.dataset)
            run_inspect(pairs, args.dataset, args.output / "runs" / "expected", behavior="expected")
            run_inspect(pairs, args.dataset, args.output / "runs" / "empty", behavior="empty")
            asyncio.run(score_batch(args.output / "runs", args.output / "scores", args.dataset, "all", use_provider=False))
            path = render_report(load_evidence_tree(args.output / "runs"), load_scores(args.output / "scores"), args.output)
            emit({"report": path.resolve(), "provenance": "simulated", "model_calls": 0,
                  "notice": "Only fixture/Inspect pipeline validation; B is unconfigured, not simulated as a real model."})
        return 0
    except (ValueError, FileNotFoundError, FileExistsError, KeyError, ProviderError) as exc:
        print(f"kmb: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
