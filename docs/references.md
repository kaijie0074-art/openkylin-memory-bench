# 参考项目合集与采用边界

本合集覆盖 27 项公开项目。核查日期：2026-09-28。方法参考条目保持 **static_only**；Inspect AI、Pydantic、Plotly、pytest、Hypothesis已安装并完成本地离线测试，实际版本记录在 [references.json](references.json) 和 uv.lock。OpenClaw固定2026.9.6，Hermes固定提交2ffa4977baf9874c498453a09447dc399b0fd215，两镜像已完成构建和无模型调用的CLI/离线配置探针，详情见agent-images.json。两款智能体已完成一道开发题的真实模型实验，ABC共六次评分通过（reports/real-local-proxy-smoke-v2），尚未完成openKylin验收；未复现的其他项目版本/提交仍记为unknown。

本项目构建一套评测底座，用同一任务、智能体轨迹与证据比较 A 规则、B 模型裁判、C 混合三版评分。参考的目的包括代码依赖、数据设计、实验协议、输入隔离和报告经验，不等于把 27 个完整框架拼在一起。

## 采用分层

- `direct_dependency` / `direct_test_dependency`：计划或已纳入本项目依赖；是否实际调用、安装成功、测试通过应以具体运行记录确认。
- `real_adapter_target`：真实智能体适配目标；适配代码存在不代表外部环境或真实实验成功。
- `method_reference`：只借鉴方法，使用独立实现与原创任务，不复制该项目代码或数据。
- `optional_memory_extension` / `optional_native_candidate` / `export_extension`：后续扩展，不作为当前已实现能力。
- `discovery_only` / `deferred_pending_license`：登记线索，条件未核实，不直接采用。

## 全量项目表

| 项目 | 代码许可核查 | 采用方式 | 对应环节 | 最小借鉴单元 |
|---|---|---|---|---|
| [ContextWeave](https://github.com/OpenMOSS/ContextWeave) | CC-BY-NC-4.0 (root LICENSE; no separate permissive code grant found) | method_reference | scenario, session_isolation, evidence, scoring | 参考工作区快照、历史注入与当前召回分离、有无记忆配对、文件与行为证据。 |
| [LongMemEval](https://github.com/xiaowu0162/LongMemEval) | MIT | method_reference | dataset, session_history, scoring | 知识更新、拒答与时间戳对话；题型分别评分。 |
| [LoCoMo](https://github.com/snap-research/locomo) | CC-BY-NC-4.0 | method_reference | dataset, evidence, scoring | 会话、说话人、时间、证据ID；多跳与时序问题的设计。 |
| [MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench) | MIT (root; third-party subdirectories need individual review) | method_reference | dataset, memory_ingestion, session_isolation | 增量注入、一次构建多题测试、冲突更新与测试时学习。 |
| [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2) | Apache-2.0 | method_reference | input_boundary, memory_interface, scoring, budget | memory query不接收gold、题型和题号；统一证据上下文预算，轨迹输入与评分分离。 |
| [MemoryArena](https://github.com/ZexueHe/MemoryArena) | unknown: root LICENSE not found | method_reference | environment, agent_loop, memory_interface | 跨会话行动、环境反馈、存记忆、后续行动的闭环；环境/智能体/记忆拆分。 |
| [LoCoMo-Plus](https://github.com/xjtuleeyf/Locomo-Plus) | unknown: README refers to license but root LICENSE not found | method_reference | dataset_generation, human_review, semantic_scoring | 早期线索与后期隐式触发、人工筛选、语义距离控制。 |
| [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) | MIT | direct_dependency | runner, scoring, trace | 评估任务、求解器、评分器分层；统一日志和重跑。 |
| [Harbor](https://github.com/harbor-framework/harbor) | Apache-2.0 | method_reference | environment, container, artifact | 任务环境生命周期、文件制品、可复现环境。 |
| [AgentGym](https://github.com/WooooDyy/AgentGym) | MIT | method_reference | agent_interface, environment, trajectory | 统一环境交互与轨迹结构；复旦智能体研究连接。 |
| [OSWorld](https://github.com/xlang-ai/OSWorld) | Apache-2.0 | method_reference | desktop_environment, reset, evidence | 桌面任务复位、执行证据、基于实际状态的验证。 |
| [OpenClaw](https://github.com/openclaw/openclaw) | MIT | real_adapter_target | agent_adapter, session, memory | 一款独立智能体的会话与原生持久化适配。 |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | MIT | real_adapter_target | agent_adapter, session, memory | 第二款独立智能体；保留原生轨迹与会话恢复语义。 |
| [KylinAgent](https://gitee.com/openkylin/kylin-agent) | AGPL-3.0 (official repository declaration) | optional_native_candidate | openkylin, agent_adapter | openKylin原生入口；当前Hermes后端关系要明确。 |
| [KylinBot SIG](https://gitee.com/openkylin/community/tree/master/sig/KylinBot) | unknown: core repository access/license not fully verified | discovery_only | openkylin, community | 跟踪原生智能体生态与可接入入口。 |
| [DeepEval](https://github.com/confident-ai/deepeval) | Apache-2.0 | method_reference | semantic_scoring, judge_calibration | 语义评估、裁判提示与评分可靠性检查。 |
| [Ragas](https://github.com/vibrantlabsai/ragas) | Apache-2.0 | method_reference | retrieval_scoring, judge_calibration | 检索证据质量与生成回答质量分开分析。 |
| [Promptfoo](https://github.com/promptfoo/promptfoo) | MIT (core repository) | method_reference | experiment_matrix, assertions, comparison | 配置化对比、断言和固定测试样本。 |
| [Langfuse](https://github.com/langfuse/langfuse) | MIT core; ee and other paths have separate terms | method_reference | trace, observability, cost | 轨迹、版本、延迟、成本与评估记录关联。 |
| [OpenTelemetry Python](https://github.com/open-telemetry/opentelemetry-python) | Apache-2.0 | export_extension | trace_export | 标准化事件与span导出能力预留。 |
| [pytest](https://github.com/pytest-dev/pytest) | MIT | direct_test_dependency | tests | 数据、隔离、证据和评分回归。 |
| [Hypothesis](https://github.com/HypothesisWorks/hypothesis) | MPL-2.0 | direct_test_dependency | property_tests | 属性测试发现路径、证据和输入边界反例。 |
| [Pydantic](https://github.com/pydantic/pydantic) | MIT | direct_dependency | schema, validation | 严格任务/证据/评分模型，拒绝未知字段。 |
| [Plotly.py](https://github.com/plotly/plotly.py) | MIT | direct_dependency | report, visualization | 本地交互报告、能力对比和样例下钻。 |
| [Mem0](https://github.com/mem0ai/mem0) | Apache-2.0 (open-source repository) | optional_memory_extension | memory_adapter | 第三方结构化记忆组件的后续配置对照。 |
| [LangMem](https://github.com/langchain-ai/langmem) | MIT | optional_memory_extension | memory_adapter | 记忆抽取、管理与检索组件的后续实验。 |
| [Letta](https://github.com/letta-ai/letta) | historical Apache-2.0; current migration to letta-code requires version/license re-verification | deferred_pending_license | memory_adapter, agent_adapter | 持久状态智能体与记忆分层的设计参考。 |

## 与长期记忆评测最直接的经验

**ContextWeave** 提供参考工作区、历史注入、召回、行动和评分拆分；其与 OpenMOSS 的关联提供了研究交流背景，并不证明本项目获得上游认可。每个子任务的参考镜像有助于避免前一任务失败污染后一任务。根许可证是 CC BY-NC 4.0，本项目默认只借方法。

**LongMemEval 与 LoCoMo** 的关键价值是历史组织与能力分解。它们主要测问答，问答正确不意味着文件或工具动作正确。导入任意样本时，应将答案、证据标签、能力标签与模型输入剥离。LoCoMo 根许可证含非商业限制，当前任务全部原创，不重发布其样本。

**MemoryAgentBench** 的增量分块与冲突更新适合设计“记住—修正—使用”的任务；一次记忆构建供多个问题使用可以节约成本，但多个问题不能反向污染记忆快照。其根MIT许可不自动覆盖每个嵌入第三方后端与改编数据来源。

**LongMemEval-V2** 的严格接口最值得借鉴：后端不能看到gold答案、题型和题号，只获取允许的查询及证据；统一记忆上下文预算。它读取行动轨迹，但最终评分仍主要是问答，不能直接宣称测完本地行动。

**MemoryArena** 展示真正跨会话的行动—反馈—记忆循环；其根代码许可尚未明确，使用方法级参考。观察到稳定的任务组namespace时，不能假定多次运行自动隔离，本项目另外加入run隔离。

**LoCoMo-Plus** 提供“早期线索—后期隐式触发”的出题经验，可用于检验无显式提醒时的信息调用；新增部分代码及数据许可待确认，当前仅原创化借鉴。

## 比较三版时锁定的条件

任务集、智能体、底层模型、工具预算、初始化状态、证据文件和任务版本应固定。A/B/C 对同一冻结证据离线评分。评分器的优劣依据与独立人工判断的一致率、误判、漏判、成本和延迟，而不是给智能体的分数高低。没有人工标注时，只报告自动评分分歧，不写“已获人工验证”。

无记忆、普通检索记忆、版本化记忆是受测配置的另一个维度，不能用它们替代两款不同智能体。KylinAgent 当前使用 Hermes 后端的事实也不能使它与 Hermes 自动变成两款独立记忆引擎。

## 每项许可证证据与风险

### ContextWeave

- 代码：CC-BY-NC-4.0 (root LICENSE; no separate permissive code grant found)。
- 数据：CC-BY-NC-4.0 (root LICENSE; review bundled assets separately)。
- 证据：[官方来源 1](https://github.com/OpenMOSS/ContextWeave/blob/main/LICENSE)。
- 限制与复现风险：非商业限制；本项目不复制其代码或数据。Docker镜像、指定CLI和外部接口未在openKylin复现。
- 状态：static_only；版本 unknown；提交 unknown。

### LongMemEval

- 代码：MIT。
- 数据：MIT declared on cleaned dataset card; separate data LICENSE not found。
- 证据：[官方来源 1](https://github.com/xiaowu0162/LongMemEval/blob/main/LICENSE)；[官方来源 2](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned)。
- 限制与复现风险：主要问答；旧答案与新答案同时出现也可能判对，不能直接判行动。使用cleaned版本并剥离答案字段。
- 状态：static_only；版本 unknown；提交 unknown。

### LoCoMo

- 代码：CC-BY-NC-4.0。
- 数据：CC-BY-NC-4.0 (root LICENSE)。
- 证据：[官方来源 1](https://github.com/snap-research/locomo/blob/main/LICENSE.txt)。
- 限制与复现风险：不复制受限数据；QA标签与历史同对象必须拆开；图片仅URL，部分任务实现未发布。
- 状态：static_only；版本 unknown；提交 unknown。

### MemoryAgentBench

- 代码：MIT (root; third-party subdirectories need individual review)。
- 数据：MIT declared on dataset card; upstream adapted subsets need provenance review。
- 证据：[官方来源 1](https://github.com/HUST-AI-HYZ/MemoryAgentBench/blob/main/LICENSE)；[官方来源 2](https://huggingface.co/datasets/ai-hyz/MemoryAgentBench)。
- 限制与复现风险：后端依赖冲突；问答/摘要并不等于真实工具任务；重复运行必须单独隔离状态。
- 状态：static_only；版本 unknown；提交 unknown。

### LongMemEval-V2

- 代码：Apache-2.0。
- 数据：Apache-2.0。
- 证据：[官方来源 1](https://github.com/xiaowu0162/LongMemEval-V2/blob/main/LICENSE)；[官方来源 2](https://huggingface.co/datasets/xiaowu0162/longmemeval-v2/blob/main/LICENSE)。
- 限制与复现风险：输入为行动轨迹，但最终主要问答；默认数据与模型服务较重，未整套复现。
- 状态：static_only；版本 unknown；提交 unknown。

### MemoryArena

- 代码：unknown: root LICENSE not found。
- 数据：CC-BY-4.0 per official dataset documentation。
- 证据：[官方来源 1](https://github.com/ZexueHe/MemoryArena)；[官方来源 2](https://memoryarena.github.io/)。
- 限制与复现风险：代码仍preview且根许可未明确，不复制；多环境很重，重跑namespace需加入run ID。
- 状态：static_only；版本 unknown；提交 unknown。

### LoCoMo-Plus

- 代码：unknown: README refers to license but root LICENSE not found。
- 数据：unknown for additions; upstream LoCoMo carries CC-BY-NC-4.0。
- 证据：[官方来源 1](https://github.com/xjtuleeyf/Locomo-Plus)；[官方来源 2](https://github.com/snap-research/locomo/blob/main/LICENSE.txt)。
- 限制与复现风险：不得把未明确许可等同可复制；原项目主要测回答约束，需扩展文件行为。
- 状态：static_only；版本 unknown；提交 unknown。

### Inspect AI

- 代码：MIT。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/LICENSE)。
- 限制与复现风险：依赖安装不代表本项目已完成真实智能体或openKylin集成。
- 状态：installed_and_offline_tested；版本 0.3.271；提交 unknown。

### Harbor

- 代码：Apache-2.0。
- 数据：task-dependent; not inherited from code license。
- 证据：[官方来源 1](https://github.com/harbor-framework/harbor/blob/main/LICENSE)。
- 限制与复现风险：不导入未知任务资产；完整执行器体量较大。
- 状态：static_only；版本 unknown；提交 unknown。

### AgentGym

- 代码：MIT。
- 数据：environment-dependent; not globally verified。
- 证据：[官方来源 1](https://github.com/WooooDyy/AgentGym/blob/main/LICENSE)。
- 限制与复现风险：不同环境许可证与依赖分别核查；未声称复旦合作或上游认可。
- 状态：static_only；版本 unknown；提交 unknown。

### OSWorld

- 代码：Apache-2.0。
- 数据：task and VM assets require individual review。
- 证据：[官方来源 1](https://github.com/xlang-ai/OSWorld/blob/main/LICENSE)。
- 限制与复现风险：首版不搬整套桌面虚拟机与任务；桌面控制成本高。
- 状态：static_only；版本 unknown；提交 unknown。

### OpenClaw

- 代码：MIT。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/openclaw/openclaw/blob/main/LICENSE)。
- 限制与复现风险：需要隔离profile及真实版本检测；隐藏记忆不可假称已观测；仅有单题Mac/Debian真实smoke，完整pilot与openKylin结论待验证。
- 状态：real_mac_container_smoke_passed_openkylin_pending；版本 2026.9.6 (eb377ac)；提交 unknown。

### Hermes Agent

- 代码：MIT。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE)。
- 限制与复现风险：CLI和服务版本须固定；用模拟器不能替代真实双智能体。
- 状态：real_mac_container_smoke_passed_openkylin_pending；版本 unknown；提交 2ffa4977baf9874c498453a09447dc399b0fd215。

### KylinAgent

- 代码：AGPL-3.0 (official repository declaration)。
- 数据：not_applicable。
- 证据：[官方来源 1](https://gitee.com/openkylin/kylin-agent)。
- 限制与复现风险：不能把Hermes与其前端算作两个独立记忆引擎；直接复用需处理AGPL义务，首版不复制。
- 状态：static_only；版本 unknown；提交 unknown。

### KylinBot SIG

- 代码：unknown: core repository access/license not fully verified。
- 数据：unknown。
- 证据：[官方来源 1](https://gitee.com/openkylin/community/tree/master/sig/KylinBot)。
- 限制与复现风险：SIG目录不是已验证运行时；未完整核查核心代码和许可。
- 状态：static_only；版本 unknown；提交 unknown。

### DeepEval

- 代码：Apache-2.0。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/confident-ai/deepeval/blob/main/LICENSE.md)。
- 限制与复现风险：不以裁判自评作为真值；外部服务和许可范围另查。
- 状态：static_only；版本 unknown；提交 unknown。

### Ragas

- 代码：Apache-2.0。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/vibrantlabsai/ragas/blob/main/LICENSE)。
- 限制与复现风险：RAG问答指标不能直接代表任务完成度。
- 状态：static_only；版本 unknown；提交 unknown。

### Promptfoo

- 代码：MIT (core repository)。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/promptfoo/promptfoo/blob/main/LICENSE)。
- 限制与复现风险：依赖或界面更换不会自动解决评测泄漏；本项目不整合其服务。
- 状态：static_only；版本 unknown；提交 unknown。

### Langfuse

- 代码：MIT core; ee and other paths have separate terms。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/langfuse/langfuse/blob/main/LICENSE)。
- 限制与复现风险：不能把整个仓库都标MIT；首版本地证据，不上传用户内容。
- 状态：static_only；版本 unknown；提交 unknown。

### OpenTelemetry Python

- 代码：Apache-2.0。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/open-telemetry/opentelemetry-python/blob/main/LICENSE)。
- 限制与复现风险：仅预留导出，不把端到端遥测视为已实现。
- 状态：static_only；版本 unknown；提交 unknown。

### pytest

- 代码：MIT。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/pytest-dev/pytest/blob/main/LICENSE)。
- 限制与复现风险：测试通过仅证明覆盖的条件，不代表真实智能体性能。
- 状态：installed_and_offline_tested；版本 9.1.1；提交 unknown。

### Hypothesis

- 代码：MPL-2.0。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/HypothesisWorks/hypothesis/blob/master/LICENSE.txt)。
- 限制与复现风险：修改其MPL覆盖文件时需保留相应义务；首版仅依赖。
- 状态：installed_and_offline_tested；版本 6.168.2；提交 unknown。

### Pydantic

- 代码：MIT。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/pydantic/pydantic/blob/main/LICENSE)。
- 限制与复现风险：类型校验不等于可信输入或语义正确。
- 状态：installed_and_offline_tested；版本 2.13.5；提交 unknown。

### Plotly.py

- 代码：MIT。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/plotly/plotly.py/blob/master/LICENSE.txt)。
- 限制与复现风险：雷达图仅在相同数据和协议下可比。
- 状态：installed_and_offline_tested；版本 6.9.0；提交 unknown。

### Mem0

- 代码：Apache-2.0 (open-source repository)。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/mem0ai/mem0/blob/main/LICENSE)。
- 限制与复现风险：不是独立第二智能体；托管产品规则与开源库不同。
- 状态：static_only；版本 unknown；提交 unknown。

### LangMem

- 代码：MIT。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/langchain-ai/langmem/blob/main/LICENSE)。
- 限制与复现风险：替换记忆组件属于受测条件，不应同时改变全部框架。
- 状态：static_only；版本 unknown；提交 unknown。

### Letta

- 代码：historical Apache-2.0; current migration to letta-code requires version/license re-verification。
- 数据：not_applicable。
- 证据：[官方来源 1](https://github.com/letta-ai/letta)。
- 限制与复现风险：不将历史Apache声明直接套用当前迁移后的版本；未加入首版依赖。
- 状态：static_only；版本 unknown；提交 unknown。

## 已排除的捷径

不把存在README等同于可复现；不把公开源码等同于宽松许可证；不将排行榜分数当成本机结果；不以模型裁判的自评作为实验真值；不因一个组件提高结果就宣称提高了整个系统的长期记忆。所有未完成的集成、平台验证与人工复核应在项目交付状态中单独列出。
