# openKylin Memory Bench

Goal: reproducible six-ability cross-session evaluation of OpenClaw and Hermes with three offline scorers.
Stack: Python 3.11+, uv, Inspect AI, Pydantic, Plotly, pytest, Hypothesis.

## Hard boundaries

- Simulated controls/fixtures never become real-agent, human-review or openKylin results. Preserve provenance.
- Export agent inputs only through `public_input()`; never mount the repository, rubrics, tests, other trials or user profiles.
- A new session ID alone does not prove memory isolation; native history search and persistent files are separate channels.
- `native_session`/`native_tool_report` are subject claims; model tool calls are proposals. Neither proves independent action execution.
- Keep explicit directory inventory completeness separate from captured text completeness. Missing evidence is not evidence of absence.
- Preserve native evidence and hashes; never overwrite historical runs/scores or relabel errors as ability failures.
- Normal gateway responses preserve business content; redact evidence separately and reject known upstream credential echoes.
- Credentials stay in ignored local configuration; do not print them or discover credentials in other applications.
- Freeze selection only with complete real matrix, independent human labels and matching designed-control calibration. Stop protocol edits during formal selection/holdout.
- When a pilot overlaps development, retain its loaded protocol snapshot and rescore frozen evidence uniformly; current disk code is not its execution provenance.
- Keep unknown usage, hidden memory, uncertain cleanup and unobserved model identities unknown.
- New image builds need unused tags and recorded digests; never overwrite a tag used by an experiment.
- Do not publish, submit applications, send mail, or write to the personal knowledge base automatically.
- KylinAgent fronts Hermes; do not count them as two independent memory engines.
- ContextWeave/LoCoMo have noncommercial terms; use method references unless separately reviewed.

## Commands and configuration

- Offline setup/check: `uv sync --group dev`, `uv run pytest`, `uv run kmb dataset validate`.
- Offline demo: `uv run kmb demo --output <new-directory>`; every output directory must be new.
- Explicit upstream: `KMB_BASE_URL`, `KMB_API_KEY`, `KMB_MODEL`, `KMB_API_STYLE`; `.env.example` is not automatically loaded.
- Trial-generated `KMB_AGENT_*` values are internal gateway credentials, not host provider keys.
- `KMB_DOCKER_CONTEXT`, `KMB_DOCKER_HOST_IP`, `KMB_RUNTIME_ROOT` select existing isolated runtime resources.
- Optional `KMB_OPENCLAW_IMAGE` / `KMB_HERMES_IMAGE` overrides still require fixed digests and probes.

## Documentation map

| Topic | Source |
|---|---|
| Setup and operator workflow | README.md |
| Code, freeze and evidence contracts | docs/implementation.md |
| Gateway and native adapters | docs/model-gateway.md, docs/agent-adapters.md |
| Image builds and observed identities | containers/README.md, docs/agent-images.json |
| Original tasks and upstream licenses | docs/task-matrix.md, docs/references.md, docs/references.json |
| Actual verification and pending work | docs/public-results.md, docs/validation-status.json |
| openKylin environment | docs/openkylin-validation.md |
