from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Ability = Literal["retention", "recall", "update", "distinction", "boundary", "reuse"]
Split = Literal["dev", "selection", "holdout"]
Verdict = Literal["pass", "fail", "undetermined"]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]


def safe_relative_path(value: str) -> str:
    p = PurePosixPath(value)
    if not value or p.is_absolute() or ".." in p.parts or "\\" in value or "\x00" in value or value == ".":
        raise ValueError(f"unsafe relative path: {value!r}")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True, allow_inf_nan=False)


class Budget(StrictModel):
    timeout_seconds: int = Field(default=600, gt=0, le=3600)
    max_tool_actions: int = Field(default=20, gt=0, le=1000)


class SessionSpec(StrictModel):
    id: str
    prompt: str
    final: bool = False


class TaskSpec(StrictModel):
    id: Identifier
    ability: Ability
    family: str
    split: Split
    sessions: list[SessionSpec] = Field(min_length=2)
    initial_files: dict[str, str] = Field(default_factory=dict)
    budget: Budget = Field(default_factory=Budget)
    memory_policy: str = "native persistence across fresh sessions; fresh subject per trial"

    @model_validator(mode="after")
    def check_task(self):
        if len({s.id for s in self.sessions}) != len(self.sessions):
            raise ValueError("session IDs must be unique")
        if sum(s.final for s in self.sessions) != 1 or not self.sessions[-1].final:
            raise ValueError("exactly the final session must be final")
        for path in self.initial_files:
            safe_relative_path(path)
        return self

    def public_input(self) -> dict[str, Any]:
        # Deliberate allowlist. No case ID, family, ability, split, rubric or target.
        return {"sessions": [s.model_dump() for s in self.sessions],
                "initial_files": self.initial_files, "budget": self.budget.model_dump()}


class Criterion(StrictModel):
    id: str
    kind: Literal["file_exists", "file_absent", "text_equals", "text_contains", "text_not_contains", "json_equals", "csv_equals", "action_contains", "semantic"]
    description: str
    path: str | None = None
    expected: Any = None
    critical: bool = False

    @model_validator(mode="after")
    def check_path(self):
        if self.path is not None:
            safe_relative_path(self.path)
        if self.kind not in {"semantic", "action_contains"} and self.path is None:
            raise ValueError("file criterion requires a path")
        return self


class PrivateRubric(StrictModel):
    task_id: Identifier
    criteria: list[Criterion] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_criteria(self):
        if len({x.id for x in self.criteria}) != len(self.criteria):
            raise ValueError("criterion IDs must be unique")
        return self


class ModelIdentity(StrictModel):
    requested: str | None = None
    returned: str | None = None
    provider: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class Usage(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class EvidenceEvent(StrictModel):
    id: str
    kind: str
    session_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class EvidenceBundle(StrictModel):
    schema_version: Literal["1", "2"] = "2"
    run_id: Identifier
    task_id: Identifier
    ability: Ability
    family: str
    split: Split
    agent: str
    agent_version: str = "unknown"
    provenance: Literal["simulated", "real"]
    environment: dict[str, str] = Field(default_factory=dict)
    status: Literal["completed", "task_failed", "budget_exhausted", "infrastructure_error", "execution_unknown"] = "completed"
    error: str | None = None
    model: ModelIdentity = Field(default_factory=ModelIdentity)
    events: list[EvidenceEvent] = Field(default_factory=list)
    files_before: dict[str, str] = Field(default_factory=dict)
    files_after: dict[str, str] = Field(default_factory=dict)
    files_complete: bool = True
    file_inventory_after: dict[str, Literal["file", "directory", "symlink", "special"]] | None = None
    file_inventory_complete: bool = False
    memory_observable: bool = False
    memory_snapshot: dict[str, str] = Field(default_factory=dict)
    usage: Usage = Field(default_factory=Usage)
    elapsed_seconds: float = Field(default=0.0, ge=0)
    evidence_hash: str = ""

    @model_validator(mode="after")
    def check_evidence(self):
        for path in [*self.files_before, *self.files_after, *self.memory_snapshot]:
            safe_relative_path(path)
        for path in self.file_inventory_after or {}:
            safe_relative_path(path)
        if self.file_inventory_complete and self.file_inventory_after is None:
            raise ValueError("complete file inventory requires an inventory")
        if self.schema_version == "1" and (self.file_inventory_after is not None or self.file_inventory_complete):
            raise ValueError("legacy evidence cannot carry unhashed inventory fields")
        if len({e.id for e in self.events}) != len(self.events):
            raise ValueError("evidence event IDs must be unique")
        return self

    def digest(self) -> str:
        raw = self.model_dump(exclude={"evidence_hash"})
        if self.schema_version == "1":
            if self.file_inventory_after is not None or self.file_inventory_complete:
                raise ValueError("legacy evidence cannot carry unhashed inventory fields")
            raw.pop("file_inventory_after")
            raw.pop("file_inventory_complete")
        return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

    def freeze(self):
        self.evidence_hash = self.digest()
        return self

    def verify(self):
        if not self.evidence_hash or self.evidence_hash != self.digest():
            raise ValueError("evidence missing checksum or has been modified")
        return self


class CriterionScore(StrictModel):
    criterion_id: str
    verdict: Verdict
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    critical: bool = False


class ScoreResult(StrictModel):
    run_id: Identifier
    task_id: Identifier
    evidence_hash: str
    scorer: Literal["A", "B", "C"]
    scorer_version: str = "1"
    scoring_fingerprint: str = ""
    status: Literal["scored", "error", "not_scored"] = "scored"
    criteria: list[CriterionScore] = Field(default_factory=list)
    verdict: Verdict = "undetermined"
    error: str | None = None
    model: ModelIdentity = Field(default_factory=ModelIdentity)
    usage: Usage = Field(default_factory=Usage)
    elapsed_seconds: float = Field(default=0.0, ge=0)
    conflicts: list[str] = Field(default_factory=list)
    components: dict[str, Any] = Field(default_factory=dict)


def aggregate_verdict(criteria: list[CriterionScore]) -> Verdict:
    if any(c.verdict == "fail" for c in criteria):
        return "fail"
    if criteria and all(c.verdict == "pass" for c in criteria):
        return "pass"
    return "undetermined"
