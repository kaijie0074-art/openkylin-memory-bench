# 工程验证数据 v2

60道原创微型任务；dev24、selection18、holdout18，每能力均分。

- tasks：可信runner任务规格；只允许public_input白名单进入智能体。
- rubrics：私有评分；绝不挂载agent环境。
- simulation：人工预期文件状态；仅用于软件模拟测试，不是真实运行证据。

完整矩阵、限制和切分协议见 ../../docs/task-matrix.md。原创数据按 CC BY 4.0 许可提供，见本目录 LICENSE。不要把本目录整体挂载给被测智能体。

此版本保留原 60 题和全部评分标准，仅澄清 `boundary-007` 最终会话中摘要正文与保留文件指令的边界。`audit.json` 逐题记录从输入独立推导的预期结果与测量限制。旧版 `data/` 和既有真实运行证据不变；两版本的证据不可混合评分。

`audit-report.json` 记录旧版、新版数据摘要与逐文件差异；运行 `scripts/audit-dataset.py` 可重新验证。此审计是静态题目审计，不代表独立人工评分或真实智能体成绩。
