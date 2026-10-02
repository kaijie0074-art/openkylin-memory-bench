"""Inspect AI is the execution/logging backbone; external agents own their sessions."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from inspect_ai import Task
from inspect_ai import eval as inspect_eval
from inspect_ai.dataset import Sample
from inspect_ai.model import ModelOutput
from inspect_ai.scorer import Score, scorer
from inspect_ai.solver import solver

from kmb.gateway import TrialGateway
from kmb.models import EvidenceBundle, EvidenceEvent, PrivateRubric, TaskSpec
from kmb.provider import JudgeProvider
from kmb.storage import load_evidence, save_evidence, save_score


async def _run_real_task(task: TaskSpec, agent: str, output: Path,
                         provider: JudgeProvider) -> EvidenceBundle:
    """A fresh gateway belongs to one subject/trial, across that trial's sessions."""
    from kmb.adapters import run_task

    start = time.monotonic()
    with TrialGateway(provider, max_tool_actions=task.budget.max_tool_actions,
                       timeout_seconds=task.budget.timeout_seconds) as gateway:
        evidence = await run_task(task, agent, output, model_config=gateway.container_config())
        drained = await asyncio.to_thread(gateway.drain)
    # Close/cancel any in-flight calls before collecting the final immutable record.
    evidence.verify()
    if evidence.task_id != task.id or evidence.agent != agent or evidence.provenance != "real":
        raise ValueError("native adapter returned mismatched trial identity")
    updated = evidence.model_copy(deep=True)
    gateway_events = gateway.evidence_events()
    for event in gateway_events:
        event.id = f"gateway/{updated.run_id}/{event.id}"
    updated.events.extend(gateway_events)
    updated.model = gateway.model_identity()
    updated.usage = gateway.usage()  # The gateway is authoritative; do not double-count native logs.
    updated.elapsed_seconds = max(updated.elapsed_seconds, time.monotonic() - start)
    updated.environment.update({"model_gateway": "authenticated_per_trial_host_gateway",
                                "model_transport": "chat_completions",
                                "tool_budget_scope": "model_proposed_tool_calls"})
    responses = [event for event in gateway_events if event.kind == "model_response"]
    errors = [event.data["error"] for event in responses if event.data.get("error")]
    # Some CLIs exit successfully even when their last provider request failed.
    # Only a subsequent successfully forwarded response establishes recovery.
    last_response_failed = bool(responses) and (
        bool(responses[-1].data.get("error")) or responses[-1].data.get("forwarded") is False)
    if not drained:
        updated.status = "execution_unknown"
        updated.error = "model_gateway_drain_unconfirmed"
    elif gateway.budget_exhausted and updated.status != "execution_unknown":
        updated.status = "budget_exhausted"
        updated.error = "tool_budget_exhausted"
    elif updated.status != "execution_unknown" and last_response_failed:
        updated.status = "infrastructure_error"
        updated.error = "model_gateway_upstream_error"
    elif updated.status == "completed" and updated.model.parameters.get("upstream_calls", 0) == 0:
        updated.status = "infrastructure_error"
        updated.error = "no_gateway_model_calls"
    updated.events.append(EvidenceEvent(
        id=f"gateway/{updated.run_id}/summary", kind="model_gateway_summary",
        data={"tool_budget_exhausted": gateway.budget_exhausted,
              "accepted_requests_settled": drained,
              "usage": updated.usage.model_dump(), "model": updated.model.model_dump(),
              "upstream_error_count": len(errors),
              "tool_calls_are_proposals_not_executed_actions": True}))
    # Revalidate event identity constraints after merging, then freeze exactly once.
    return EvidenceBundle.model_validate(updated.model_dump()).freeze()


def run_inspect(pairs: list[tuple[TaskSpec, PrivateRubric]], dataset_root: Path,
                output: Path, agent: str = "simulation", behavior: str = "expected"):
    from kmb.scorers import score
    from kmb.simulation import simulate

    if agent not in {"simulation", "openclaw", "hermes"}:
        raise ValueError("unsupported agent")
    provider = None if agent == "simulation" else JudgeProvider.from_env()
    tasks = {t.id: t for t, _ in pairs}
    rubrics = {t.id: r for t, r in pairs}
    output.mkdir(parents=True, exist_ok=True)

    @solver(name="kmb_external_agent")
    def external_agent():
        async def solve(state, generate):
            task = tasks[str(state.sample_id)]
            if agent == "simulation":
                evidence = simulate(task, dataset_root, behavior)
            else:
                evidence = await _run_real_task(task, agent, output / "native" / task.id, provider)
            evidence_path = save_evidence(evidence, output / "evidence" / evidence.run_id)
            state.metadata.update({"evidence_path": str(evidence_path.resolve()),
                                   "evidence_hash": evidence.evidence_hash,
                                   "provenance": evidence.provenance,
                                   "agent": evidence.agent,
                                   "execution_status": evidence.status})
            state.output = ModelOutput.from_content("external-agent", json.dumps({
                "run_id": evidence.run_id, "status": evidence.status,
                "provenance": evidence.provenance}, ensure_ascii=False))
            state.completed = True
            return state
        return solve

    @scorer(name="kmb_rules", metrics=[])
    def rule_scorer():
        async def evaluate(state, target):
            evidence = load_evidence(Path(state.metadata["evidence_path"]))
            result = await score(evidence, rubrics[evidence.task_id], "A")
            saved = save_score(result, output / "scores")
            # Deliberately no built-in accuracy: undetermined/errors are not numeric zero.
            return Score(value=result.verdict, explanation=result.error or "See criterion evidence",
                         metadata={"score_path": str(saved.resolve()), "result": result.model_dump()})
        return evaluate

    samples = [Sample(id=t.id, input=json.dumps(t.public_input(), ensure_ascii=False)) for t, _ in pairs]
    task = Task(dataset=samples, solver=external_agent(), scorer=rule_scorer(), name="kmb_memory")
    # Inspect's model is never invoked. The actual provider belongs to the external adapter.
    return inspect_eval(task, model="mockllm/model", log_dir=str(output / "inspect"),
                        log_model_api=False, max_samples=1, retry_on_error=0,
                        fail_on_error=False, display="none")
