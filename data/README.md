# 数据目录

60道原创微型任务；dev24、selection18、holdout18，每能力均分。

- tasks：可信runner任务规格；只允许public_input白名单进入智能体。
- rubrics：私有评分；绝不挂载agent环境。
- simulation：人工预期文件状态；仅用于软件模拟测试，不是真实运行证据。

完整矩阵、限制和切分协议见 ../docs/task-matrix.md。原创数据按 CC BY 4.0 许可提供，见本目录 LICENSE。不要把本目录整体挂载给被测智能体。
