"""Offline fixtures for pipeline verification, NEVER agent capability results."""
from __future__ import annotations

import json
import platform
import uuid
from pathlib import Path

from kmb.models import EvidenceBundle, EvidenceEvent, TaskSpec


def simulate(task: TaskSpec, dataset_root: Path, behavior: str = "expected") -> EvidenceBundle:
    if behavior not in {"expected", "empty", "infra_error"}:
        raise ValueError("unknown simulated behavior")
    fixture = json.loads((dataset_root / "simulation" / f"{task.id}.json").read_text())
    if fixture.get("provenance") != "simulated" or fixture["task_id"] != task.id:
        raise ValueError("simulation fixture provenance or task mismatch")
    events = []
    for n, session in enumerate(task.sessions):
        sid = f"sim-session-{uuid.uuid4().hex}"
        events.append(EvidenceEvent(id=f"event-{n * 2}", kind="user_input", session_id=sid,
                                    data={"text": session.prompt}))
        events.append(EvidenceEvent(id=f"event-{n * 2 + 1}", kind="assistant_output", session_id=sid,
                                    data={"text": fixture.get("final_response", "SIMULATED") if session.final else "SIMULATED acknowledgement", "simulated": True}))
    return EvidenceBundle(
        run_id=f"sim-{uuid.uuid4().hex}", task_id=task.id, ability=task.ability,
        family=task.family, split=task.split, agent=f"simulation/{behavior}",
        provenance="simulated", agent_version="fixture-v1",
        environment={"host": platform.system(), "architecture": platform.machine(), "execution": "fixture-only; no agent or model called"},
        status="infrastructure_error" if behavior == "infra_error" else "completed",
        error="simulated unavailable environment" if behavior == "infra_error" else None,
        events=events, files_before=task.initial_files,
        files_after=fixture["files_after"] if behavior == "expected" else task.initial_files,
        memory_observable=False,
    ).freeze()
