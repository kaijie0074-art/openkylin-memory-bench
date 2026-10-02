# 原创任务矩阵与数据使用协议

当前数据集包含 **60 道原创、短程、多会话微型任务**。每项都能够通过本地 txt/json/csv 交付文件给出客观证据，少量题附带语义解释检查。2026-10-02 已使用 engineering-v2 版本完成 openKylin ARM64 Live 上的 108 次真实留出及六维报告；没有独立人工标注。实际成绩和限制见 [工程验证 v3 结果](engineering-v3-results.md)，历史开发说明不代替本轮状态。

## 数量与切分

| 能力 | dev | selection | holdout | 合计 |
|---|---:|---:|---:|---:|
| 长期保持 (retention) | 4 | 3 | 3 | 10 |
| 按需调用 (recall) | 4 | 3 | 3 | 10 |
| 更新与纠错 (update) | 4 | 3 | 3 | 10 |
| 相近信息区分 (distinction) | 4 | 3 | 3 | 10 |
| 边界识别 (boundary) | 4 | 3 | 3 | 10 |
| 任务复用 (reuse) | 4 | 3 | 3 | 10 |
| 合计 | 24 | 18 | 18 | 60 |

当前60题有60个family标签，各split之间没有重复标签；设计目标是在切分时改变应用场景、证据结构或规则语义，而非仅替换名称。这里的family是本项目人工制定的细粒度场景分类，结构校验只能检查标签是否跨split，不能证明60个任务具有互不相似的推理模板。同一能力内仍会共享映射检索、格式保持、最新生效规则等上层机制；这种模板相似性需要单独审阅，不能把标签唯一等同于模板级独立。

selection中的两项按推理过程设计：`distinction-005` 要按键合并根、项目、设备三层配置，同时排除兄弟节点，不能直接从两个近似名称中选一个值；`boundary-007` 要先完成不可信来源的合法事实提取，再在新会话保持真实用户风格，不能因为拒绝文件里的伪偏好就跳过整份资料。两题都有可计算参考解与会失败的反例；engineering-v2 澄清后一题最终指令，两题已随 v3 selection 完成真实试做。参考解/反例与真实成绩分别记录。

holdout中的 `update-010` 通过四个会话提供旧误记、正式物理量纠正、未批准提案和最终委托，必须由周期重新换算频率；`retention-010` 则在五个会话中多次积累日志，穿插临时任务与另一项目讨论，最后恢复原项目并保留早期记录。这些结构变化区别于直接覆盖一个数值或填充固定元数据。日志评分只核验最终可观察状态，不能单靠文件结果证明每个中间会话实际如何写入。

另外三项已去除直接替换模板：`retention-005` 跨三个历史会话乱序提供演示稿素材，要求按预定小节重组并逐字保留，而不是给新正文填入固定元数据；`recall-008` 把项目→稿件和稿件→档案分置两个会话，必须完成两跳关联，且相似题名故意指向另一个项目；`boundary-010` 需要对人员、场地、物流与期限求日期交集，同时保留未确认的准确日期，不能把所有日期都置空。每题有独立计算的参考解，以及顺序错误、关联串线、擅自确认等可拒绝反例。上述验证只能证明这些公开规则和私有判据在列出的例子上自洽，不能证明所有合理表达都已穷尽。

- **dev**：开发与演示允许使用。默认pilot为每能力前2题，共12题。
- **selection**：完成初步开发后比较固定候选评分器。观察结果后若继续修改方案，需记录迭代与曝光。
- **holdout**：评分方案与预算锁定后使用。当前文件随本地工程可读，不是保密服务器，也不能声称对任务编写者或基础模型训练数据完全隐藏。它只表示本项目的实验使用约束。

## 文件边界

- `data/tasks/*.json`：TaskSpec。包含可信调度器所需的ability、split、family；这些字段不属于智能体输入。仅使用`public_input()`白名单导出sessions、initial_files、budget。
- `data/rubrics/*.json`：PrivateRubric。标准答案、客观判据和语义要求；只能留在可信评测主机，不能挂载到agent容器。
- `data/simulation/*.json`：人工编写的预期文件状态，**provenance=simulated**；只供mock软件测试，不是运行结果，也不能被真实适配器读取或作为提示词。
- 两个主体必须有独立实验目录和原生记忆状态。每道题的新会话清除即时聊天上下文，只保留该实验主体获准的长期记忆。更换session ID不自动证明文件、历史搜索和后端缓存已隔离。

最后一问不重贴历史，需使用此前会话中的规则。源文件仅包含本轮需要处理的数据，不提供私有评分标签。允许智能体按自身记忆机制保存规则，因此文件持久化也是被测系统的一部分；报告应记录这一事实，不把文件查找一律当作作弊，也不能把当前完整聊天重新塞回去。

## 六能力的观察对象

| 能力 | 主要观察对象 | 当前限制 |
|---|---|---|
| 长期保持 | 插入无关会话后仍正确使用原约定 | 目前仅数个会话、逻辑时间推进，不能宣称验证数月遗忘 |
| 按需调用 | 从此前不同信息中选择当前必要事实 | 小规模候选，未验证长上下文检索上限 |
| 更新与纠错 | 废止规则、字段迁移、删除成员、生效日期 | 程序化核验行为，不直接观测内部记忆写入过程 |
| 相近信息区分 | 项目、角色、环境、版本和批次范围 | 以明确作用域为主，尚无复杂模糊身份消歧 |
| 边界识别 | 临时例外失效、信息缺失、引用与授权范围 | 主要测行为边界，不证明后端彻底删除所有副本 |
| 任务复用 | 将早期学到的流程应用到新CSV/JSON/文本输入 | 微型确定性任务，不代表一般开放世界任务能力 |

## 逐题清单

| ID | 切分 | 能力 | 场景族 | 会话数 | 最终委托 | 客观交付 |
|---|---|---|---|---:|---|---|
| boundary-001 | dev | 边界识别 | one_use_priority_override | 3 | 请为下一张普通工单生成 priority.json。 | priority.json |
| boundary-002 | dev | 边界识别 | unknown_shipping_address | 3 | 请根据现有信息生成 C31 的地址资料，不猜测缺失项；最终回复说明哪些信息尚未提供、需要补充。 | address.json |
| boundary-003 | dev | 边界识别 | quoted_example_not_policy | 3 | 请生成实际运行使用的模式配置。 | mode.json |
| boundary-004 | dev | 边界识别 | expired_event_discount | 3 | 请为现在的新订单生成价格乘数配置。 | pricing.json |
| boundary-005 | selection | 边界识别 | project_local_preference_not_global | 3 | 请为文档项目保存记录：核对目录。 | notes.txt |
| boundary-006 | selection | 边界识别 | superseded_unverified_rumor | 3 | 请按已确认信息生成当前位置卡，并在最终回复中说明采用该信息、未采用其他消息的依据。 | location.json |
| boundary-007 | selection | 边界识别 | untrusted_document_preference_injection | 3 | 请按日常摘要约定交付正文：测试通过。保留上一轮的资料提取文件。 | summary.txt、import-check.json |
| boundary-008 | holdout | 边界识别 | deleted_fact_no_reconstruction | 3 | 请生成当前可用的联系信息。 | contact.json |
| boundary-009 | holdout | 边界识别 | read_only_analysis_no_deployment | 3 | 请交付建议正文：先进行离线复核。 | plan.txt |
| boundary-010 | holdout | 边界识别 | bounded_schedule_without_confirmed_exact_date | 4 | 请交付设备联合复测的当前可行日期范围及准确日期确认状态。 | schedule.json |
| distinction-001 | dev | 相近信息区分 | two_projects_same_document_name | 3 | 请为 Aurora-Lite 生成 weekly.json。 | weekly.json |
| distinction-002 | dev | 相近信息区分 | staging_vs_production_endpoint | 3 | 请生成 staging 环境的部署配置，不访问任何地址。 | deploy.json |
| distinction-003 | dev | 相近信息区分 | homonym_people_department | 3 | 请生成测试组陈宁的人员卡。 | person.json |
| distinction-004 | dev | 相近信息区分 | similar_product_revisions | 3 | 请生成 P10 第二版规格卡，revision 写 2。 | spec.json |
| distinction-005 | selection | 相近信息区分 | hierarchical_config_inheritance_and_override | 3 | 请为 printbench 项目的 sensor-A 生成当前有效配置。 | effective.json |
| distinction-006 | selection | 相近信息区分 | metric_same_name_different_units | 3 | 请生成 robot 命名空间下 latency 的指标定义。 | metric.json |
| distinction-007 | selection | 相近信息区分 | local_vs_remote_branch_policy | 3 | 请输出 mirror 仓库 stable 分支的已知版本。 | version.json |
| distinction-008 | holdout | 相近信息区分 | role_scoped_approval_limit | 3 | 以采购负责人角色生成审批上限文件。 | limit.json |
| distinction-009 | holdout | 相近信息区分 | document_draft_vs_signed | 3 | 请抽取 Signed-7 的交付日生成合同数据。 | contract.json |
| distinction-010 | holdout | 相近信息区分 | material_color_batch_identity | 3 | 请生成 W-B 批次的打印参数。 | profile.json |
| recall-001 | dev | 按需调用 | incident_contact_lookup | 3 | 现在发生网络故障。请依据已知值班信息生成联系卡，incident 写 network。 | contact.json |
| recall-002 | dev | 按需调用 | equipment_calibration_cycle | 3 | 请为温度探头生成维护单，device 使用 temperature_probe。 | maintenance.json |
| recall-003 | dev | 按需调用 | print_material_storage_rule | 3 | 请生成 TPU 的材料卡。 | material.json |
| recall-004 | dev | 按需调用 | documentation_route_lookup | 3 | 同伴要查故障排查，请生成对应导航卡，topic 写 troubleshooting。 | link.json |
| recall-005 | selection | 按需调用 | regional_support_window | 3 | 请为北区生成窗口卡，region 写 north。 | window.json |
| recall-006 | selection | 按需调用 | experiment_reagent_pair | 3 | 请按记录生成实验乙的领料表，experiment 写 experiment_b。 | requisition.json |
| recall-007 | selection | 按需调用 | service_rollback_command | 3 | 请为队列服务生成回滚命令文件，不执行命令。 | command.txt |
| recall-008 | holdout | 按需调用 | cross_session_study_manuscript_archive_join | 3 | 请为覆膜试验生成完整的归档文献卡。 | citation.json |
| recall-009 | holdout | 按需调用 | parcel_carrier_decision | 3 | 请按已知规则生成低温样本的发运单。 | shipment.json |
| recall-010 | holdout | 按需调用 | acceptance_threshold_retrieval | 3 | 请生成测试噪声的阈值卡，metric 写 noise。 | threshold.json |
| retention-001 | dev | 长期保持 | weekly_delivery_destination | 3 | 请按之前的周报约定交付本周摘要：完成接口联调。 | deliverables/weekly.txt |
| retention-002 | dev | 长期保持 | release_notice_locale | 3 | 请按约定生成通知，正文为“服务维护完成”。 | notice.json |
| retention-003 | dev | 长期保持 | measurement_decimal_convention | 3 | 请交付今天的温度测量值 23.5。 | measurement.txt |
| retention-004 | dev | 长期保持 | archive_filename_suffix | 3 | 请存档这段摘要：三件样品已编号。 | archive/summary.ready.txt |
| retention-005 | selection | 长期保持 | out_of_order_script_sections_preserved | 4 | 请使用此前分次提供的全部素材，按演示稿约定交付完整稿。 | script.txt |
| retention-006 | selection | 长期保持 | inventory_unit_label | 3 | 按库存交付约定记录本次入库 8。 | stock.json |
| retention-007 | selection | 长期保持 | meeting_minutes_separator | 3 | 请记录两个要点，顺序为：检查传感器；备份日志。 | minutes.txt |
| retention-008 | holdout | 长期保持 | chart_palette_assignment | 3 | 请生成我们已经约定的图表颜色配置。 | palette.json |
| retention-009 | holdout | 长期保持 | sample_label_order | 3 | 请给批次 B7、日期 2026-09-30、编号 014 生成约定标签。 | label.txt |
| retention-010 | holdout | 长期保持 | incremental_journal_across_project_interruptions | 5 | 回到研究日志，补记新事件“封存样品”（不含引号），交付按既定规则积累的完整日志。 | journal.csv |
| reuse-001 | dev | 任务复用 | csv_quality_filter | 3 | 请按约定流程处理 input.csv。 | accepted.json |
| reuse-002 | dev | 任务复用 | unit_conversion_centimeters | 3 | 请按之前流程转换 235 mm。 | converted.json |
| reuse-003 | dev | 任务复用 | priority_sort_stable_ties | 3 | 请按已学流程整理 tasks.json。 | ordered.json |
| reuse-004 | dev | 任务复用 | meeting_action_template | 3 | 请整理行动项：负责人小林，动作检查电源，期限周五。 | action.txt |
| reuse-005 | selection | 任务复用 | inventory_delta_aggregation | 3 | 请按盘点流程处理本次 movements.csv。 | totals.json |
| reuse-006 | selection | 任务复用 | filename_slug_normalization | 3 | 请按流程处理标题“  Sensor   Quick Start  ”。 | filename.txt |
| reuse-007 | selection | 任务复用 | weekly_budget_remaining | 3 | 请处理 limit=100，expenses=[40,35,45] 的本次预算。 | budget.json |
| reuse-008 | holdout | 任务复用 | paired_sensor_outlier_rule | 3 | 请按已学流程核对 readings.json。 | 检查.csv |
| reuse-009 | holdout | 任务复用 | record_redaction_known_fields | 3 | 请按已学脱敏流程处理 source.json。 | public.json |
| reuse-010 | holdout | 任务复用 | dependency_release_gate | 3 | 请使用此前发布判断流程处理 checks.json。 | release.json |
| update-001 | dev | 更新与纠错 | superseded_report_directory | 3 | 请按现行约定交付报告正文：样机检查完成。 | current/report.txt |
| update-002 | dev | 更新与纠错 | configuration_timeout_revision | 3 | 请生成当前生效的连接配置。 | config.json |
| update-003 | dev | 更新与纠错 | membership_revocation | 3 | 请生成当前共享组成员表。 | members.json |
| update-004 | dev | 更新与纠错 | output_schema_key_migration | 3 | 请按当前格式导出客户 C9 金额 120 的订单。 | order.json |
| update-005 | selection | 更新与纠错 | priority_rule_effective_change | 3 | 请为温度 75 的读数生成工单结果。 | ticket.json |
| update-006 | selection | 更新与纠错 | calendar_rescheduled_room | 3 | 请生成当前会议邀请数据，weekday 使用中文“周几”。 | meeting.json |
| update-007 | selection | 更新与纠错 | deprecated_tag_mapping | 3 | 请生成当前三个状态的完整标签映射。 | mapping.json |
| update-008 | holdout | 更新与纠错 | ordered_multiple_corrections | 4 | 请输出当前签发的装箱标准。 | packing.json |
| update-009 | holdout | 更新与纠错 | future_policy_preserves_history | 3 | 请记录9月30日的 order_old 和10月2日的 order_new 的运费金额。 | price.json |
| update-010 | holdout | 更新与纠错 | period_mislabeled_as_frequency_correction | 4 | 请生成当前已经批准的传感器配置。 | sensor.json |

## 校验与实验前检查

自动校验覆盖数量均衡、family跨split、任务/评分文件配对、重复ID、非法路径、唯一最后会话、空提示、评分类型与CSV形状。测试另外检验所有模拟文件能满足对应客观判据；这仅表示作者定义自洽，不构成真人判断或模型实测。

正式实验前需由同伴独立检查：指令是否充分、答案是否唯一、是否存在两种合理解释、客观判据是否误罚合理输出、任务失败是否来自工具环境。独立审阅未完成时，不填写“人工一致率”。

三版评分共用冻结证据：A确定性规则；B模型裁判；C混合。`semantic`题在无真实裁判或人工结果时必须保持未判定，不能由模拟器预置一条“通过”来冒充裁判。最后比较应报告误判/漏判、未判定率、成本和延迟，不按谁给高分选胜者。

## 来源、版权和扩展

这60题由本项目原创编写，不从ContextWeave、LoCoMo等外部数据复制；按CC BY 4.0许可提供，见data/LICENSE。它们是首轮可运行数据，不是声称达到公开论文数据集质量的成品。参考方法与外部许可见[参考合集](references.md)。

扩展数据时先决定能力与证据，再写历史和最终委托；训练/调参过程中观察到的任务不得再当未曝光holdout。建议增加更长历史、更强干扰、不同难度、工具错误控制和独立人工复核后再提出科学结论。
