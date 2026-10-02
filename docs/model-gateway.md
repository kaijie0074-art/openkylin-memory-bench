# 真实智能体的模型网关

`TrialGateway` 每个 trial 新建随机端口和随机 Bearer token。宿主机持有显式
`KMB_BASE_URL`、`KMB_API_KEY`、`KMB_MODEL`；容器只收到本次 trial 的临时凭据。
当前上游必须是 `chat_completions`，不自动改模型或转换成 Responses API。

```python
with TrialGateway(provider, max_tool_actions=20, timeout_seconds=600) as gateway:
    evidence = await run_task(task, agent, output, gateway.container_config())
    settled = gateway.drain()  # 主体停止后，不再接收新的模型调用，等待已接纳调用结算。
    # 桥接层合并网关事件、用量和身份；settled=False 不能标 completed，再冻结证据。
```

默认监听 `0.0.0.0` 随机端口，容器地址为 `host.docker.internal`；这是为了支持
Docker/Colima 的宿主机连接，尚不能假定绑定宿主机 loopback 就可被 VM 访问。
每个请求均校验一次随机 token，使用恒定时间比较；退出上下文后服务关闭。
该监听方式会让同一局域网看到临时端口，内容仍需 trial token。容器需要预先配置
`host.docker.internal` 到宿主机的解析。单独有 HTTP 网关不能阻断容器其他外网访问；
运行配置也必须禁止注入别的模型凭据、禁用辅助 provider。

只允许 `POST /v1/chat/completions` 和 `GET /v1/models`；后者只列配置的固定模型。
请求模型不匹配、协议不支持的字段或任意其他路径都会被拒绝。不接受重定向、
分块上传或超过 2 MiB 的请求，完整上游响应上限为 16 MiB。一次只转发一个请求，
每次调用受 trial 剩余时限和 provider 超时限制；请求不会自动重试。

上游一律 `stream=false`。收到完整响应后，先累计 `tool_calls` 数量再向容器放行。
超出预算的整条响应不会转发，也不会执行其中工具；后续请求返回预算耗尽。
这限制的是模型提出、可以放行的工具调用数量，并不能证明客户端实际执行了多少个工具。
客户端若请求 stream，网关把已检查完整响应转换成 assistant/text/tool_calls chunk、
finish chunk、可用的 usage chunk 和 `[DONE]`。这不是实时逐 token 流式输出。

网关记录 `model_request`、`model_response` 和收尾的 `gateway_drain`。请求中的
tool-role 结果仍是客户端报告，不能因此冒充可信的实际执行轨迹。原生日志中的
`native_tool_report` 也来自被测进程自报；客观动作成功需要独立观察或确能证明该事实的文件产物。
网关不产生 `action`/`tool_call` 执行事件，也不设置 `actions_complete=true`。
被预算拦截的响应会标记 `forwarded=false`，上游已产生的 usage 仍计入费用。
任何调用缺失某项 token 用量，该项汇总保留 `None`，不会按 0 处理。

正常成功响应保留业务文本、URL 与工具参数，再按客户端需要返回 JSON 或转换成 SSE；
不会用日志脱敏器改写交给智能体的内容。若响应包含已知的真实上游密钥，整条响应以
`upstream_credential_in_response` 拒绝，而不是删改后继续运行。已产生的用量仍计入。

证据采用独立脱敏副本：隐藏上游密钥、trial token、完整上游地址、敏感字段，以及其他
URL 中的凭据、查询参数和片段。上游错误正文、请求头和异常详情不转发或打印；上游返回非成功 HTTP 响应时，仅在证据中额外保存数字状态码，便于区分代理故障与本地超时。
因此日志不是上游原始字节副本；报告应保留脱敏边界，不能据此声称业务响应也被改写。

模型身份分别记录请求名、实际返回名、provider origin 和完整 endpoint 的 SHA256，
以及可观察的推理参数集合。多个返回模型保留为集合，不合并成一个确定模型。
调用次数和工具提案数属于运行结果，不是固定配置；正式 selection/holdout 另冻结配置和预算。

主体结束后，`drain()` 拒绝新的模型调用，并在剩余墙钟预算与 provider 超时内等待已接纳调用；`GET /v1/models` 在服务关闭前仍可读取。
未能确认收尾时，桥接层标记 `execution_unknown`；这也不能用来推定不可见的后台记忆已写完。

网关测试使用 `httpx.MockTransport` 作为上游，仅在本机发 HTTP 请求。通过这些测试
只说明受控协议、预算、证据和错误处理工作。另有两款智能体的单题真实 Mac/Debian
smoke 记录，见 反代接入记录（档案或本地工作区路径：`local-proxy-reconnection.md`）；它不替代完整 pilot 或 openKylin 验收。
