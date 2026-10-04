# 工程 v3 实际结果与复核

材料修订日期：2026-10-04（R1）；正式实验版本仍为工程 v3。正式成绩来自 openKylin 3.0 ARM64 Live 上冻结后的 18 道留出 × 两款智能体 × 三次重复；不是模拟器或视频中的两次开发演示。

| 项目 | 实际结果 |
|---|---|
| 真实证据 | 108；107 completed，1 budget_exhausted；均保留 |
| 自动评分 | A/B/C 共 324 条；无评分程序错误 |
| 客观任务通过 | OpenClaw 31/54；Hermes 26/54 |
| 三版总体 | 各 57 通过、51 失败；总体判定无分歧 |
| 逐项分歧 | 120 个判据中 6 个语义项；A 无法判定，B/C 通过；对应运行的客观必要条件均失败 |
| 三次重复 | 36 个题目×智能体组，29 组总体判定稳定，7 组波动 |
| 裁判 tokens / 用时 | A 0 / 1.63 秒；B 2,049,598 / 670.15 秒；C 97,695 / 36.08 秒 |
| 独立人审 / 赢家 | 未进行 / 未选出 |

冻结前另有 36 次真实 selection 和 8 项设计反例；A 在语义项弃权一次，B/C 对 8 项均匹配设计期望，关键客观误放为零。反例是模拟控制和 AI 复核，不能当成人工标签或真实智能体任务成绩。相同模型来源可能带来相关偏差，三版一致不能证明语义判断准确。

协议 SHA-256：`8f3c22d49b28a8e2ab1319aa5b6ec84796f02b9aa0daed6d66a48920466b8542`。  
代码/任务/判据/锁文件指纹：`951f96bfce93484f7fbe0e16da09336fb53f95f223edc12beb42e0c94e466b4a`。

## 平台与软件检查

可信 CLI、采集、评分、报告在 openKylin 上运行；OpenClaw 与 Hermes 在该客体管理的固定 Debian ARM64 Docker 用户空间运行。Mac 是虚拟机宿主。模型请求与回执均为 gpt-5.5。未验证 x86 或 AgentOS SDK 原生集成。

Mac 及客体 fresh source 目录各通过 592 项测试、Ruff 和编译；客体源码构建通过。fresh source 复用了已有固定运行环境；新依赖环境全离线安装有缓存缺失，不能声称通过。GitHub CI 已远程通过；Pages 发布成功。R1 的新增检查在独立修订记录中登记。

ARM64 .deb `0.1.0-3` 断网安装、普通用户模拟批处理、卸载重装及开发证据 A 离线重评分、正式报告再生成通过；安装包 SHA-256 为 `64cc1adb2e0238c9faf46812a63c006bfcb5076fd7703b34aae5e256b5f1fdc1`。它携带原始题库；正式复现须另选 `datasets/engineering-v2`。它不携带模型账户、智能体镜像或虚拟机磁盘。

## 原始凭证如何复核

完整技术档案提供源码、题库、私有判据、成功/失败样例、安装包、连续原片与 `08_完整实验凭证.tar.gz`。原版发行附件已公开：[完整材料与安装包](https://github.com/kaijie0074-art/openkylin-memory-bench/releases/tag/v0.1.0-engineering-v3)。[视频观看页](https://kaijie0074-art.github.io/openkylin-memory-bench/)无需登录。[R1 修订材料](https://github.com/kaijie0074-art/openkylin-memory-bench/releases/tag/v0.1.0-engineering-v3-r1) 已于 2026-10-05 公开。原版链接保留为历史入口。

在源码根目录解压凭证后恢复 `reports/`：

- `reports/engineering-v3-protocol-20261001.json`：工程冻结条件。
- `reports/engineering-v3-holdout-20261001/report/index.html`：完整六维报告及逐项证据链接。
- `reports/engineering-v3-holdout-20261001/`：108 次原始运行与 324 条评分。
- `reports/engineering-v3-selection-20261001/`、`reports/engineering-v3-controls-20261001/`：冻结前验证。
- `reports/desktop-demo-real-20261002/`：视频中的两次开发任务，文件失败，单独保留。

按 [评审复现说明](../competition/评审复现说明.md)核对哈希及离线重评分。保留整个 HTML report 目录，文件引用才能打开；副本不重复计数。重新执行真实实验需要显式配置模型与原镜像，并使用新的输出路径。历史 v2 通道故障不混入 v3 正式成绩。

短程 3–5 次会话只能证明这些文件任务的实际行为，不能直接外推数月记忆保持。隐藏记忆、完整动作执行及语义判据仍有可观测性与校准限制。

## R1 补充核验（2026-10-05）

独立离线工具复算原 108 份 A，稳定字段零差异；B/C 不调用模型，仅结构绑定。新版 `.deb` `0.1.0-4` 在新的 openKylin 3.0 ARM64 客体通过断网安装、模拟编排、核验与正式报告再生成。最终完整/精简档案在 Mac 与断网客体从源码目录外验收。公开技术提交 [CI](https://github.com/kaijie0074-art/openkylin-memory-bench/actions/runs/37216001058) 通过 678 项。

新的 4 次真实开发运行、12 条评分单独保存：3 次任务通过，1 次 Hermes 预算耗尽并失败。原正式 108/324 不变。视频为 200 秒的历史真实素材重剪，本地连续播放与解码通过，线上浏览器实际播放因工具超时未确认；线上文件哈希与分段下载通过。原智能体镜像未公开分发，Dockerfile 重建为新条件。更正邮件仅为未发送草稿。

最终档案内源码为发布前技术快照；当前仓库另补实际发布链接和回执，不修改快照的哈希或冻结评测核心。
