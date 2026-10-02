# 工程验证 v3 交付清单（2026-10-02）

本轮按官方任务书重新核对并编排的入口是 `competition/`，包含作品方案、评审复现说明、实际 openKylin 冻结环境与机读核对表。正式整理的 PDF 和精简/完整 ZIP 见 [官方交付核对](competition-deliverables.md)。下面的 tar.gz 是更早的工程归档，保留原哈希，文档版本可能早于当前参赛编排；不要用其旧状态代替本轮核对。

交付档案：`deliverables/openkylin-memory-bench-engineering-v3-20261002.tar.gz`，SHA-256 见同目录的 `checksums.sha256`。档案中的目录结构与仓库一致；解压后从 `README.md` 开始。未包含 `.env.local`、代理密钥、虚拟机磁盘、Docker 镜像缓存或第三方智能体镜像；真实模型实验复跑须自行提供可用代理配置与固定镜像。

| 内容 | 档案内位置 | 验证状态 |
|---|---|---|
| 源码、脚本、测试、锁文件、许可证 | `src/`、`scripts/`、`tests/`、`containers/`、`pyproject.toml`、`uv.lock`、`LICENSE` | 本机 592 项测试、Ruff 通过 |
| 原创题库与修订数据 | `data/`、`datasets/engineering-v2/` | v3 冻结协议明确指定 v2；安装包内的原始 60 题不能代替它 |
| 参考项目合集与研究说明 | `docs/` | 含来源、许可、借鉴与验证边界 |
| 工程冻结协议 | `reports/engineering-v3-protocol-20261001.json` | 协议文件 SHA-256 `8f3c22d49b28a8e2ab1319aa5b6ec84796f02b9aa0daed6d66a48920466b8542` |
| 选型与受控反例 | `reports/engineering-v3-selection-20261001/`、`reports/engineering-v3-controls-20261001/` | 36 次真实选型、8 项设计反例；无独立人审 |
| 正式留出证据、评分与 HTML 报告 | `reports/engineering-v3-holdout-20261001/` | 108 次真实运行、324 条 A/B/C 评分；107 正常结束，1 预算耗尽，0 基础设施或评分错误 |
| openKylin ARM64 安装包 | `dist/openkylin-memory-bench_0.1.0-3_arm64.deb` | SHA-256 `64cc1adb2e0238c9faf46812a63c006bfcb5076fd7703b34aae5e256b5f1fdc1`，已断网安装验收 |
| 安装包核验 | `reports/deb-build-engineering-v3/`、`reports/deb-install-engineering-v3/`、`reports/deb-installed-validation-v3/` | 安装版离线重评分与报告数据复核；模拟批处理不计真实成绩 |
| 新的桌面演示运行 | `reports/desktop-demo-real-20261002/` | 两款真实智能体均完成运行，因缺交付文件 A/B/C 均判失败；开发题，不混入留出 |
| 真实桌面视频 | `reports/desktop-recording-final-20261002/` | 880.3 秒连续原片及 207.4 秒剪辑节选；末尾六维图来自独立正式留出批次 |

报告入口：`reports/engineering-v3-holdout-20261001/report/index.html`。打开时保留整个 `report/` 目录，逐项证据 JSON 链接才有效。`docs/engineering-v3-results.md` 解释具体数字，`docs/demo-recording-plan.md` 记录视频时段与剪辑方式，`docs/validation-status.json` 保存机读状态。工程协议检验通过不等于独立人工准确率检验；本轮没有人审证明的最佳评分器。旧 v2 故障实验保留在原工作区但未纳入本交付档案的正式成绩。
