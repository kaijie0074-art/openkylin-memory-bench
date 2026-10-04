# openKylin Memory Bench

面向 openKylin 的智能体跨会话记忆评测工具。让 OpenClaw 与 Hermes 经历 3–5 次新会话，收集真实对话、可见记忆、工具提案与最终文件；冻结证据后，以规则、模型裁判、混合三种方法离线评分，并生成可追溯的六维 HTML 报告。

[观看演示视频（3 分 27 秒）](https://kaijie0074-art.github.io/openkylin-memory-bench/) · [下载作品方案](https://github.com/kaijie0074-art/openkylin-memory-bench/releases/download/v0.1.0-engineering-v3/openkylin-memory-bench-introduction.pdf)

**公开源码包含代码、60 道原创任务、锁定依赖、测试、ARM64 安装包的复现说明与实际结果。** [发行材料](https://github.com/kaijie0074-art/openkylin-memory-bench/releases/tag/v0.1.0-engineering-v3)提供作品介绍 PDF、精简评审包、完整技术档案、ARM64 安装包和讲解字幕视频。视频观看链接已准备；报名与投稿仍由参赛人自行完成，步骤见 [人工投稿说明](SUBMISSION.md)。

## 已验证的范围

2026-10-02 的工程 v3 在 openKylin 3.0 ARM64 Live 桌面完成 18 道留出题 × 两款智能体 × 三次重复：108 次真实运行、324 条 A/B/C 评分。107 次 completed、1 次 budget_exhausted，全部保留。智能体采用固定 Debian ARM64 Docker 用户空间；调度、采集、评分、报告在 openKylin 运行。没有声称 x86 验证、原生 AgentOS SDK 集成或数月记忆验证。

| 六种能力的客观通过 | OpenClaw | Hermes |
|---|---:|---:|
| 长期保持 | 6/9 | 7/9 |
| 记忆调用 | 4/9 | 6/9 |
| 动态更新 | 7/9 | 1/9 |
| 相近区分 | 0/9 | 0/9 |
| 边界识别 | 5/9 | 3/9 |
| 任务复用 | 9/9 | 9/9 |
| 合计 | 31/54 | 26/54 |

每维只有 3 道独立题目，三次重复用于观察稳定性。三版总体均为 57 通过 / 51 失败，6 个语义判据出现分歧。模型与规则一致率不能称为人工准确率；本轮没有独立人审或评分器赢家。详见 [实际结果与凭证复核](docs/public-results.md)。

## 先运行不消耗模型的演示

需要 Python 3.11+ 和 uv。首次安装依赖需要网络；之后可用已有缓存/固定环境。

```bash
uv sync --frozen --group dev
uv run --frozen --no-sync kmb --dataset datasets/engineering-v2 dataset validate
uv run --frozen --no-sync kmb doctor
uv run --frozen --no-sync kmb demo --output reports/my-demo
uv run --frozen --no-sync pytest
```

`reports/my-demo` 必须不存在。演示明确标记为模拟，只验证工具链，不进入真实成绩。`doctor` 不安装组件、不启动 Docker、不调用模型。592 项软件测试已在 Mac 和 openKylin 固定运行环境通过；[GitHub CI 已通过](https://github.com/kaijie0074-art/openkylin-memory-bench/actions/runs/37003061152)：592 项测试、Ruff、题库校验和离线模拟流程。软件检查与 openKylin 平台验证分别记录。

## 真实运行与离线评分

真实运行需要预先准备的 Docker、固定 OpenClaw / Hermes 镜像、显式模型配置和独立状态目录。[.env.example](.env.example) 只有无效占位，本工具不自动读取其他应用的凭证。模型接口、智能体运行和 B/C 裁判会消耗实际服务额度；缺配置时不能用模拟结果替代。

最小真实开发运行：

```bash
uv run --frozen --no-sync kmb --dataset datasets/engineering-v2 run --agent both --split dev --task boundary-001 --output reports/my-real-dev
uv run --frozen --no-sync kmb --dataset datasets/engineering-v2 score --evidence reports/my-real-dev --variant A --output reports/my-real-A
uv run --frozen --no-sync kmb --dataset datasets/engineering-v2 report --evidence reports/my-real-dev --scores reports/my-real-A --output reports/my-real-report
```

A 规则重评分不调用模型。B/C 需要配置裁判；它们读取同一份证据，重评分不重新执行智能体。完整冻结流程与安装包复验见 [评审复现说明](competition/评审复现说明.md)；原始实验档案须另行恢复 `reports/` 相对布局。源码不包含模型密钥、智能体镜像、虚拟机磁盘或全套运行档案。

## 结构与信任边界

| 路径 | 用途 |
|---|---|
| `src/kmb/` | CLI、调度、适配、证据、评分、比较、报告 |
| `data/` | 原始 60 题、私有判据、软件模拟参考状态 |
| `datasets/engineering-v2/` | 正式 v3 使用的修订题库；仅澄清 boundary-007 指令 |
| `scripts/run-benchmark.py` | 一键串行执行、评分、报告；已有输出不覆盖 |
| `containers/` | 智能体镜像构建配方；重建不保证与实测镜像相同字节 |
| `examples/competition/` | 开发、离线检查和完整留出矩阵的配置 |
| `tests/` | 状态串扰、答案隔离、错误评分、证据缺失、预算/中断等回归 |
| `docs/references.json` | 27 项参考、许可证与采用边界 |

答案仅在可信调度/评分侧，智能体只获得 `public_input()` 白名单。一次实验一个身份；同一身份内顺序执行。缺少文件只有在完整目录采集下才可断言不存在。工具提案、原生工具自报和独立执行证据分别记录；不可见的内部记忆不声称已删除。

## 参与与许可

阅读 [贡献说明](CONTRIBUTING.md)、[更新记录](CHANGELOG.md)、[维护计划](ROADMAP.md)、[参考合集](docs/references.md)和[实现说明](docs/implementation.md)。后续目标是原生 openKylin / AgentOS、x86、长时任务与独立语义人审，均为计划。

自研代码 [Apache-2.0](LICENSE)；原创合成数据 [CC BY 4.0](data/LICENSE)。参考项目各自有许可，含非商业条款或许可未明确的代码/数据未直接并入。AI 辅助开发与静态 AI 复核如实标注，不能替代独立人工评审。
