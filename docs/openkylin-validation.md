# openKylin 验证路径与待验收项

本文件描述平台验证流程，不是通过证明。当前macOS上的代码、模拟结果或容器结果，均不能替代实际openKylin环境演示。真实平台和两款智能体结果必须来自对应机器上的运行记录。

## 当前选择：先在 Mac 准备

用户已决定先使用 Mac。已准备独立 Lima/VZ 的 ARM64 Live 桌面方案，官方镜像下载与校验、图形启动步骤见 [Mac 环境说明](openkylin-mac-vm.md)。2026-09-28，完整ISO已通过官方MD5与本地SHA256校验；专用VM已启动真实Live桌面并读取OS、内核与Python版本；现已调至6GiB，专用公钥SSH、数据盘、锁定Python依赖、官方Docker与两款智能体CLI探针均通过；update-001两智能体真实smoke已完成，三会话身份均变化，ABC评分无错误：OpenClaw交付通过，Hermes未写要求文件而失败。评测调度器运行在openKylin，智能体运行在该客体的固定Debian容器内。下面的 Windows 清点保留为备选方案，不要求用户现在提供该电脑。

## 2026-09-28 实际冷恢复验收

专用 VM 已正常关机并确认 Stopped，再从相同官方 ISO 冷启动新的 openKylin 3.0 ARM64 Live overlay。**这不是把操作系统安装到数据盘**。旧 Live 中的账户、SSH 与系统软件重新恢复，原有 12 GiB ext4 数据盘、工程、venv、镜像层与实验报告继续使用，没有重新解包工程、同步依赖或 load/build 镜像。本轮未调用模型，也未安装交付 `.deb`。

冷恢复证据独立保存于本机 `.runtime/openkylin-vm/cold-restore-20260928T075026Z/`；总记录为 `cold-restore-result.json`，逐文件摘要为 `evidence-sha256.json`。旧记录没有倒写。关机后对专用数据盘做了 APFS CoW clone `data-disk-before.raw`，保留恢复前状态；**没有对整个磁盘或该 clone 做完整内容哈希**。

| 检查 | 实际结果与证据范围 |
|---|---|
| 桌面引导与 SSH 身份 | 通过 CUA 下载并核对受版本控制 bootstrap 的 SHA256，执行官方固定 SSH 包安装；将客体显示的 Ed25519 指纹与 host keyscan 比对后才进行严格连接。第一次人工转写差一个相邻字符，程序拒绝连接；重新观察折行原始日志后精确匹配，两个核对记录均保留 |
| 自动挂载纠偏 | 新 Live 桌面把 `/dev/vda` 自动挂到 `/run/media/openkylin/KMB_DATA`，默认 inspect 正确拒绝。核对 UUID `e0398de1-f62c-49c6-8d4d-62ccb8f09396`、12 GiB、ext4、标签及无进程占用后，正常 `umount` 此专用挂载点，再执行原 UUID 绑定的 mount stage；没有 force/lazy、格式化、改保护或动 cidata |
| 分阶段恢复 | `mount`、`accounts`、`memory`、`swap`、`packages`、`docker`、`validate` 均退出 0；恢复原 UID/GID 10001、既有 2 GiB swap 与固定官方 Docker/containerd/runc 包。最终 inspect 为 mounted、配置完整、Docker active、无运行容器/runner/overlay，`restore_blocked=false` |
| Python 与数据 | 既有 venv 可用，60 题静态校验无错误；对公开 `data/` 文件及 `pyproject.toml`、`uv.lock` 共 184 个文件做 SHA256，与宿主冻结内容一致。此检查不涵盖整盘、私有配置或所有报告 |
| 两款智能体 | OpenClaw 与 Hermes 的离线 CLI/config probe 均为 `probe_passed`；Docker 报告的两项 image config SHA 与重启前一致。没有真实任务或模型往返，因此不是新基准成绩 |
| 私有配置 | 仅检查 `.env.local` 的 stat：UID/GID 10001、0600、335 字节；未读取或显示内容，未据此声称内容校验通过 |
| 平台 | doctor 确认 `openkylin` 3.0、aarch64 与客体 Docker；智能体仍运行于固定 Debian 用户空间容器中，不能称为两款智能体原生安装于 openKylin |

本次严格 SSH 入口如下，旧 `guest-known-hosts` 保留不覆盖。指纹核对证据是新目录中的 `host-key-verification-v2.json` 与 `guest-bootstrap.log`，CUA 图像保留在任务工具记录中：

```sh
ssh -F /dev/null \
  -i .runtime/openkylin-vm/lima/_config/user \
  -o IdentitiesOnly=yes -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=.runtime/openkylin-vm/cold-restore-20260928T075026Z/guest-known-hosts \
  -p 58313 openkylin@127.0.0.1
```

恢复验收结束时桌面保持运行，临时 bootstrap HTTP 服务已停止。Docker bridge Gateway 仍为 `172.17.0.1`。**模型 reverse 隧道未恢复**：客体 `127.0.0.1:8080` 未监听，后续真实任务前须独立重建并验证；本轮 zero-model probe 和 doctor 使用清空的配置环境，没有加载模型密钥。正式实验、人工复核与比赛演示继续按各自验收记录处理。

## 备选设备清点

Windows设备可由AI协助运行只读脚本：

```powershell
powershell.exe -NoProfile -File .\scripts\windows-doctor.ps1
```

脚本只读取CPU架构、核心数、RAM、本地固定磁盘空闲空间、固件虚拟化能力与当前hypervisor存在信号；JSON输出到标准输出。不安装虚拟机、不更改BIOS或Windows设置、不联网、不写文件、不读取浏览器或智能体凭据。若系统策略阻止脚本，记录该情况，不自动修改执行策略。

固件能力为true不等于虚拟机已安装，也不证明openKylin镜像可以启动。CPU架构、目标系统镜像架构、智能体镜像架构需一致或明确记录仿真方式。硬件资源需求应由选定镜像与小规模pilot实测确定。

3D打印设备不是这道评测题的必要依赖；借用其他工作室电脑时，应先明确机器架构、可用时间和是否能稳定保留独立实验环境。

## 在真实openKylin系统上检查

由AI或执行同伴先单独准备Python、uv、项目依赖和所需隔离运行环境。随后执行：

```bash
bash scripts/validate-openkylin.sh
```

脚本要求 `/etc/os-release` 的 `ID=openkylin`，以离线、不自动同步依赖的方式调用 `kmb doctor` 与 `kmb dataset validate`。未安装依赖就明确失败；不会自动安装、调用模型、启动智能体或制造平台通过报告。

`.env.example` 中的 `KMB_DOCKER_CONTEXT=colima-kmb` 是当前macOS工作站示例。Linux上应移除此设置或改为机器上确实存在的context。不能把配置文件中的名字当作服务已存在的证据。

## 三层验收必须分开

| 层次 | 要取得的证据 | 不能据此声称 |
|---|---|---|
| 平台识别 | 系统ID/版本、CPU架构、Python和Docker环境检查 | 两款智能体已跑通 |
| 集成诊断 | 固定镜像可启动、模型协议可用、会话与记忆通道可观察、预算控制可确认 | 诊断样本就是正式基准成绩 |
| 正式实验 | 同一矩阵的真实OpenClaw/Hermes运行、冻结证据、A/B/C评分；工程验证与独立人审分别注明 | 微型任务结果代表所有长期记忆场景 |

使用 `probe-model` 检查上游模型配置时，需事先明确将调用指定服务并可能消耗费用。真实CLI运行根据主配置建立宿主网关，KMB_AGENT_*由网关内部生成临时地址与token，容器不获得真实上游密钥。简单JSON探测成功不能替代实际智能体工具调用、多会话及预算验证。

## 正式运行前固定的信息

- openKylin版本与架构、宿主/虚拟机边界、Docker context。
- 两款独立智能体的版本、源码或镜像摘要、启动模式及原生记忆路径。
- 请求模型名、返回模型名、provider origin、完整endpoint指纹、API风格及可观察推理参数；返回身份不可见时记unknown。
- 任务/判据版本、split、运行次数、超时与动作预算。
- 同一主体哪些状态跨会话保留，哪些状态被重置；是否存在历史搜索等额外通道。
- 裁判版本、系统提示、规则实现、证据引用格式与选型指标。

不得挂载真实用户profile或整个项目目录给智能体。每次试验使用新主体；私有rubric、模拟答案、其他试验结果和凭据不进入公共任务输入。

## 当前交付与后续研究

截至 2026-10-02，v3 已完成 36 次真实选型、8 项控制、工程冻结、108 次真实留出及 324 条评分。ARM64 deb0.1.0-3 的断网安装/重装、安装版离线复评、真实桌面连续原片与 207.4 秒节选均完成。完整状态见 [交付核对](competition-deliverables.md)和 [v3 结果](engineering-v3-results.md)。上文 2026-09-28 冷恢复记录是历史状态，后续模型隧道与真实运行已分别完成，不能将历史“未恢复”当作当前阻塞。

当前是工程验证，不需要补造第二位审阅者。独立双人人审、数月真实历史、x86_64 和 AgentOS SDK 深度集成属于后续研究。无人工校准或不可观察的隐藏记忆始终保留限制；不宣称主办方已验收。

## 报告应该怎样写

可以写：“在指定openKylin版本上，按固定协议完成了这些任务，原始证据和失败项见对应文件。”只有实际完成后才能填写具体版本与数量。

不能写：“doctor通过，所以openKylin比赛验收通过”“模拟器达到100%，所以智能体记忆可靠”“几轮短任务说明可以记住数月”。比赛完整验收还包括主办方的交付、展示与评审要求，技术脚本不会自动替代这些判断。
