# 真实智能体适配与验证边界

本模块提供 `await run_task(task, agent, output_dir, model_config)`，返回冻结的
`EvidenceBundle`。`agent` 只接受 `openclaw` / `hermes`。实现代码不代表环境已经验证；
真实安装、代理工具调用往返及 openKylin 运行各自需要实际记录。

## 接入方式

可信主机先启动 TrialGateway，再传入 `gateway.container_config()`：

```python
config = {
    "base_url": "http://host.docker.internal:<port>/v1",
    "api_key": "<random-trial-token>",
    "model": "<fixed-model-id>",
    "transport": "chat_completions",
    "budget_enforced": True,
}
evidence = await run_task(task, "hermes", output_dir, config)
```

真实服务商密钥留在主机网关中。`budget_enforced` 是可信调用方对网关已经启用动作预算
的声明，不能根据模型建议或环境字符串自动打开。第一版正式运行仅接收 Chat Completions。
网关记录“模型提出的工具调用”；原生轨迹中的工具结果标记为 `native_tool_report`，
属于被测进程自报。两者都不能单独证明可信动作成功，`actions_complete` 保持 false。

`probe_agent(agent)` 只检查本地固定镜像与真实 CLI help/version，不调用模型。
`run_diagnostic(...)` 是显式的付费集成诊断入口，未知动作预算仅在该入口允许；其结果不会
标成 completed，也不进入正式排名。正常 CLI 运行应使用 `run_task` + 可信网关。

## 隔离与证据

- `KMB_DOCKER_CONTEXT=colima-kmb` 指定独立 daemon，不修改用户默认 context。
- Colima bridge 容器不假定能解析 `host.docker.internal`。适配器通过对应 profile 的
  `getent hosts host.lima.internal` 查询 Mac 地址，再加显式 `--add-host`；也可用
  `KMB_DOCKER_HOST_IP` 指定 IPv4。`host-gateway` 可能指向 VM，不能代替 Mac 网关地址。
  2026-09-28 已验证该 VM 内的 `192.168.5.2` 映射可访问宿主临时 HTTP 服务；运行时仍动态查询。
- `KMB_RUNTIME_ROOT` 默认项目 `.runtime`。每次 trial 新建随机 subject，容器仅挂载该目录。
  不挂载源码、rubric、tests、报告、主机 home、用户 profile 或 Docker socket。
- 整个 public_input 经 stdin 交给镜像内监督程序；每个原生进程只收到当前 prompt。
  未来提示没有写进挂载目录，既往对话不在下一会话中重放。
- 同一 trial 共享独立长期状态；每 session 创建新的原生会话，实际会话 ID 必须可观察且唯一。
- 镜像只使用显式构建版本，运行 `--pull=never`，固定本地 image digest，禁止 `latest`。
- 容器只读根文件系统、非 root 用户、移除 capabilities、进程/内存/CPU 限制；bridge 网络
  不是完整出站白名单。模型配置只注入本 trial 网关令牌，没有主机服务商凭证。
- 容器退出后可信主机分别采集目录类型清单与文本内容。schema 2 使用
  `file_inventory_complete` 表示目录清单完整性，`files_complete` 表示文本采集完整性。
  二进制文件可以出现在完整目录清单中，但不进入文本快照；不能把“不含文本”等同不存在。
  symlink、特殊文件、凭据路径或枚举遗漏会如实标记，缺失判定优先使用显式目录清单。
- 文本采集每文件上限 2,000,000 字节、总量 20,000,000 字节；目录枚举最多 10,000 项、
  20 秒。原生进程 stdout/stderr 合计采集上限 32,000,000 字节；超限会停止采集并保留
  不完整/错误状态，不能把截断输出当正常完成。模型声称创建文件不等于主机采集到文件。
- 文件路径含临时凭据时跳过该路径，并记录不含原名的 `credential_in_path` 原因；
  不通过重命名隐藏遗漏，避免路径碰撞或错误地判定文件不存在。
- `output_dir/native/<run_id>/` 保存脱敏后的原生 stdout/stderr、可读日志/记忆、SQLite 表
  导出，以及 SHA256 manifest。不直接复制可能含密钥的二进制数据库；遗漏明确记录。
- 强制停止失败且无法通过成功的容器列表查询确认已删除时，返回 `execution_unknown`、
  保留执行状态，不采集或删除仍可能被写入的目录。Docker CLI 非零退出同样需要停止确认。

## 两个被测条件

**OpenClaw**：官方 npm `2026.9.6`。CLI 使用 `agent --local --session-id ... --message ...
--model ... --json`；动态 probe 检查实际安装版本的参数。指定相同模型，自建配置禁用模型
回退；语义 embedding 检索关闭，保留工作区 Markdown 原生记忆。这是明确的特定配置，不能
冒充全部默认功能。`--local` 的后台整合没有完整观测，settle 保留 unknown。
该版本使用顶层 `memory.search.enabled=false`；旧 `agents.defaults.memorySearch` 字段已被
实际配置校验拒绝。探针除 help/version 外执行 `openclaw config validate --json`，不启动模型。

**Hermes**：官方源码固定提交 `2ffa4977baf9874c498453a09447dc399b0fd215`。CLI 使用
`chat --query-file ... --model ... --provider custom --format stream-json`；限定
terminal/file/memory/session_search 工具集；每次 invocation 创建新会话，不使用 resume。
记忆和历史搜索都属于保留通道。辅助模型配置同一网关，没有其他 provider 凭证。
离线探针使用其原生 `load_config` / `validate_config_structure`，检查主模型的
`api_mode=chat_completions`、`fallback_model=[]` 和所有原生辅助任务的网关路由。

原生 `max-turns` 不等于工具调用上限。本模块不会将其当作 max_tool_actions 已执行的证据。
动作预算由主机网关执行，真实模型身份和 token 用量应由可信网关补充；缺失不填零。

## 已完成的环境验证

2026-09-28，Mac ARM64 的独立 `colima-kmb` 环境中，两个镜像均实际构建完成，非 root、
只读根文件系统和独立 public 挂载下的 CLI 参数探针及原生配置检查均通过。
记录：`reports/openclaw-terminal-v2-cli-probe.json`、`reports/hermes-settle-v2-cli-probe.json`；固定镜像元数据在
`docs/agent-images.json`。Hermes 的 CLI 自报版本为 `vunknown (2026.9.24)`，如实保留该字符串，
使用已验证的完整源码 commit 和 image digest 追溯安装身份。
本机反代已完成模型调用及工具往返。`reports/real-local-proxy-smoke-v2` 中，两款智能体均完成
update-001 的三次新会话，最终产物、目录清单与网关收尾通过，A/B/C 共六条评分通过。
首次实验的故障证据和旧评分保留，见 反代接入记录（档案或本地工作区路径：`local-proxy-reconnection.md`）。
12题×两款智能体的 pilot 正在运行，尚无完整六维结论；随机哨兵隔离和 openKylin 实测仍待完成。

镜像 tag 不是不可变身份，以实际 digest 为准。Hermes 已另建 `settle-v2` 并通过无模型
CLI/原生配置探针，使整份 worker SHA256 与 OpenClaw `terminal-v2` 及当前源码一致；
Hermes 执行分支与 `settle-v1` 相同。运行中的 `real-pilot-v1` 仍实际使用旧 `settle-v1`，
不能倒写成新镜像。旧 tag/digest/worker 哈希链保留在镜像记录中；新镜像的零模型探针
不能继承旧镜像的真实运行身份，也不保证未完全锁定的上游依赖可逐字节重建。

## 后续验证与重跑条件

1. 完成 pilot，按新协议对冻结证据统一离线重评；运行中已加载的旧协议不倒写。
2. 执行新 subject 随机哨兵隔离探针，并验证跨会话允许的记忆通道；禁止私人资料导入。
3. 用受控反例校准评分器，区分口头成功、文件事实、权限故障、超时和不可观察状态。
4. 在真实智能体上继续核对预算拒绝后行为；离线网关拒绝测试不代表已完成该真实控制实验。
5. 在真实 openKylin 3.0 Desktop 重新检查运行环境、固定镜像和工具往返，并保存桌面录屏。

换镜像或 CLI 后重跑无模型探针和最小真实集成。Hermes 必须检查 `result.exit_code`、
`result.error` 与标题/cleanup/记忆队列收尾；OpenClaw 必须观察合法终态。
进程退出码 0、不同 session ID、模型口头完成均不能单独替代这些检查。

## 官方资料与接口检查日期：2026-09-28

- [OpenClaw 仓库与 MIT 许可](https://github.com/openclaw/openclaw)
- [OpenClaw CLI](https://docs.openclaw.ai/cli/agent)
- [OpenClaw 自定义模型端点](https://docs.openclaw.ai/gateway/config-tools/custom-providers)
- [OpenClaw 记忆](https://docs.openclaw.ai/concepts/memory)
- [Hermes 仓库与 MIT 许可](https://github.com/NousResearch/hermes-agent)
- [Hermes CLI](https://hermes-agent.nousresearch.com/docs/reference/cli-commands/)
- [Hermes profile 隔离边界](https://hermes-agent.nousresearch.com/docs/user-guide/profiles/)
- [Hermes 自定义 provider](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/providers.md)
- [openKylin 系统镜像](https://www.openkylin.top/downloads/)
- [KylinAgent 与 Hermes 后端关系](https://gitee.com/openkylin/kylin-agent/blob/master/docs/HERMES_AGENT_INTEGRATION_CN.md)
