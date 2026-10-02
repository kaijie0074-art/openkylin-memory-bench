# 一条命令执行批量评测

`scripts/run-benchmark.py` 是现有 CLI 的外置编排入口。它串行执行 `run → score → report`，三版评分复用同一批冻结证据；不修改核心评测协议、不创建人工标签，也不自动冻结评分器。

先只读验证配置与任务选择：

```bash
uv run --frozen --no-sync python -I scripts/run-benchmark.py \
  --config examples/batch-dev.json --output reports/my-batch --dry-run
```

`--validate` 与 `--dry-run` 相同：不调用子进程或模型，不创建输出目录。它不能证明 Docker、模型服务或正式留出门禁可用。

项目须已安装于该 Python 环境（开发环境的 editable 安装也可）。三个子命令固定使用同一解释器的 `-I -m kmb.cli`，并移除 `PYTHONPATH` / `PYTHONHOME`，避免调用目录或环境变量使子命令加载另一份实现；入口自身也建议按示例使用 `python -I`。

确认调用进程已经显式设置 `KMB_BASE_URL`、`KMB_API_KEY`、`KMB_MODEL`，以及需要时的 `KMB_API_STYLE`、平台 Docker 配置，再执行同一命令并去掉 `--dry-run`。入口不搜索或加载 `.env`、个人配置或其他凭据文件；配置 JSON 不允许填写环境变量、模型地址或密钥。真实执行和 B/C 裁判会使用该服务并可能产生模型费用。

示例配置选择两款现有智能体、dev pilot、各运行一次，再生成 A/B/C 对比。正式运行前，所用镜像必须已在目标 Docker 中准备好。可选 `images` 只允许为选中的 `openclaw` / `hermes` 指定固定标签或摘要，禁止 `latest`；未指定时采用调用进程的 `KMB_OPENCLAW_IMAGE` / `KMB_HERMES_IMAGE`，否则采用现有适配器默认值。现有适配器会记录实际镜像身份；标签本身不等于不可变摘要。

| 字段 | 含义 |
|---|---|
| `schema_version` | 必须为整数 `1` |
| `agent` | 必填：`openclaw`、`hermes`、`both` 或显式 `simulation` |
| `split` | `dev`（默认）、`selection`、`holdout` |
| `pilot` | 默认 false，仅用于 dev；与 `task` 同时填写时从 pilot 子集中筛选 |
| `task` | 可选任务 ID；省略即运行所选集合全部任务 |
| `repeat` | 整数 1–3，默认 1 |
| `variant` | `A`、`B`、`C`、`all`（默认） |
| `images` | 可选的智能体名 → 固定镜像标签/摘要映射 |
| `dataset` | 可选本地数据目录；默认项目的 `data` |
| `selection` | 人工选型的holdout清单；与`protocol`互斥 |
| `protocol` | 工程验证的holdout清单；与`selection`互斥 |
| `simulation_behavior` | 仅 simulation：`expected`、`empty`、`infra_error` |

配置中的相对 `dataset` / `selection` / `protocol` 路径以配置文件所在目录为基准；命令行 `--output` 以当前目录为基准。输出目录必须完全不存在，即使已有空目录也拒绝。不得输出到数据或源码目录内。不接受未知字段、重复 JSON 键或把布尔值当作运行次数。

离线流程检查可自行保存以下明确模拟配置，并使用同一入口。A-only simulation 会移除子进程中的模型配置，不调用模型；simulation 配合 B/C/all 仍需真实配置的裁判，模拟来源始终保留。

```json
{"schema_version": 1, "agent": "simulation", "task": "update-001", "variant": "A"}
```

真实 holdout 必须使用 `agent: "both"`、`split: "holdout"`、`repeat: 3`，省略 `task`，并显式提供`selection`或`protocol`其中之一。三个阶段均把清单传给原有CLI核验。`selection`路径保留双人人审选型门禁；`protocol`路径检查独立的工程冻结条件和控制反例，不宣称人工验证。两者都不补判或降级，也不提供人工标签输入。

成功输出包括 `runs/` 原始证据、`scores/` 此次重评分、`report/index.html` 对比报告及 `batch-status.json`。状态文件记录安全配置、有效镜像选择、模型公开身份与 endpoint 指纹、任务集合、阶段命令/退出码/耗时、原证据文件哈希。真实密钥和完整模型 endpoint 不写入状态；子进程 stdout/stderr 不转发或保存，避免意外泄漏，详细试验/裁判错误以各阶段已写出的证据和评分记录为准。

任一子命令非零退出、证据矩阵不符、证据被修改或报告缺失，入口立即停止并保留已生成文件，不重放智能体或自动重试整个阶段。人工选型模式遇到执行未知或评分错误也停止；工程协议模式保留这些记录并继续生成报告，单列为执行/评分故障，不能视为成功样本。普通任务判fail是有效实验结果；“批量流程完成”不代表智能体任务通过或正式实验合格。要重试须检查原因后明确指定另一个全新输出目录。

阶段失败使用本脚本定义的固定 `error_code`：`cli_nonzero_exit`、`evidence_matrix_mismatch`、`execution_infrastructure_or_unknown`、`evidence_drift`、`selection_drift`、`score_coverage_mismatch`、`scoring_record_error`、`report_missing`；文件/格式问题另记 `stage_io_error` / `invalid_artifact`。这些代码不包含上游原文、路径或凭据。键盘中断记为 `interrupted_cleanup_unknown`，退出 130；这不证明所有 CLI/容器进程已经停止，必须先检查原进程与现场，不能直接重跑。

## 当前验证范围

2026-10-02 的工程 v3 已通过一键入口完成 108 次真实留出、324 条 A/B/C 评分和 HTML 报告，无基础设施或评分错误；另有 1 次预算耗尽完整保留。安装版入口、两款真实开发演示和六维报告已在 openKylin 桌面录制。下述 v2 故障属于历史批次，不是当前完成状态，见 [v3 结果](engineering-v3-results.md)。

2026-09-28已通过53项批处理本地测试，并实际运行历史`simulation + update-001 + A`的三个阶段，生成一份模拟证据、A评分和HTML报告；这些离线检查均未调用模型。随后真实工程协议的首个留出矩阵通过此入口执行，但 108 份证据中 88 份为基础设施故障，批处理没有形成合格报告；详情见 [留出批次故障记录](engineering-holdout-incident.md)。离线集成记录保存在`reports/batch-entry-validation-v1`、`reports/batch-entry-failure-v1`与`.runtime/batch-validation/integration-summary.json`。

离线测试验证的是编排入口的集成行为，不代替真实智能体或 openKylin 验收。故障矩阵保留原始记录；新版本的选型和留出须使用新目录及新协议，不能挪用旧结果。
