# Mac 上的 openKylin 桌面验证环境

2026-09-28，已通过 Lima 2.1.2 / Apple Virtualization.framework 实际启动 openKylin 3.0 ARM64 **Live 桌面**，并将专用虚拟机调至 6 GiB，安装官方 `openssh-server` 后用本项目公钥完成宿主到客体 SSH 验证。系统版本、内核、磁盘与内存已经通过 SSH 机器采集；图形窗口和终端保持打开。专用12 GiB数据盘已初始化并挂载，官方Docker已安装、固定智能体镜像已导入；项目的 `uv sync --frozen` 和60题校验已通过，并完成两智能体单题开发smoke与客体隔离探针。尚未安装完整系统；正式选型36次运行刚启动，不能视为已完成。

7,348,721,664 字节 ISO 已完成下载并通过 aria2 与独立脚本的完整校验：官方 MD5 为 `62180a4fe3a9236a6ed1a93f9af7b52d`，本地 SHA256 为 `32b5bee3f56ee0d789766789d9da80ba4374e7c4b615449d845af4b770808ec9`。来源与校验记录在 `.runtime/openkylin-vm/image-verified.json` 和 `download-source-switch.json`，重复校验保留同一镜像的下载来源。

这个环境与 Colima 中的 Debian 容器验证分开。部署方式是在 openKylin 主系统运行 kmb Python/CLI，两个智能体使用已冻结的 Debian 容器用户空间，共享 openKylin 客体内核；锁定Python依赖已安装并通过60题数据校验、doctor与两款CLI探针；不能把这种部署描述成“两个智能体原生安装于 openKylin”。两智能体update-001真实smoke已完成：均正常结束且三会话身份不同，OpenClaw文件正确，Hermes缺文件；三版均据此判定，六条评分无错误。正式选型36次运行已启动，holdout尚未执行。

## 已观察到的客体状态

- `/etc/os-release`：`NAME=openKylin`、`ID=openkylin`、`VERSION_ID=3.0`、`VERSION_CODENAME=huanghe`。
- `uname -rm`：`7.0.0-2-generic aarch64`；Python 为 `3.12.2`。
- Live桌面与SSH用户为 `openkylin`。专用运行用户 `kmb` 已由数据盘初始化脚本实际创建，UID/GID均为10001，home为 `/mnt/kmb/home`；不能将其与Live登录用户或Lima配置中的同名声明混为一谈。
- 专用 `/dev/vda` 为12 GiB盘，已直接格式化为ext4（整盘文件系统、未建立分区），标签 `KMB_DATA`，挂载入口 `/mnt/kmb` 在本系统解析为 `/var/mnt/kmb`。`sda` 为约6.8 GiB只读 `/cdrom`，`vdb` 为只读约160 KiB `cidata`，两者未改动。
- 最初4 GiB配置内存不足，随后改为6 GiB，客体采集 MemTotal 为6,186,831,872字节。该次采集的约526 MiB可用内存属于释放前快照；之后已停止并 runtime mask `kytensor.service`，释放约2 GiB，并启用 `/mnt/kmb/swapfile` 的2 GiB交换空间。这些措施不增加物理内存，当前余量仍需在运行前复查，不能凭桌面启动宣称benchmark可运行。
- 最初 `/usr/sbin/sshd` 不存在。现已从官方 `https://archive.openkylin.top/openkylin` 的签名 `huanghe` 仓库安装 `openssh-server`、`openssh-client` 与 `openssh-sftp-server`，版本均为 **1:10.2p1-ok3**。`ssh` 服务 active；`sshd -T` 确认仅允许 `openkylin` 用户、公钥认证，关闭密码/交互密码、root 登录、agent/X11 转发，`GatewayPorts no`。只授权独立 Lima home 自动生成的公钥，未读入或导入个人 SSH 密钥。
- 经review并执行的 `scripts/prepare-openkylin-data.py` 已核对Live系统、设备大小/只读标志、空白签名和挂载边界，再初始化专用盘。`home/project/runtime/reports/cache/transfer/bin` 七个目录均归kmb所有、权限0700，挂载根目录归root所有、权限0755。既有盘只能在ext4、`KMB_DATA`标签及身份/挂载检查匹配时复用，不得把初始化流程当成任意磁盘格式化命令。

证据位于 `.runtime/openkylin-vm/`：`live-desktop.png` 是专用 VM 窗口截图；`guest-system-evidence.png` 同时显示 OS、内核、磁盘、内存、Python 与 SSH 文件检查；`guest-observation.json` 是由 AI 根据截图转录的结构化记录；`boot-evidence/first-native-cli/` 保留首次串口/宿主日志与 SHA256 manifest。结构化记录不是客体程序自行导出的测量文件，应与原截图一起使用。

## 隔离和资源

- 状态全在项目 `.runtime/openkylin-vm/`，独立 Lima home 与实例 `kylin`。
- 2 CPU、6 GiB RAM、12 GiB稀疏raw数据盘；通过ISO的 `Try without installing` 启动真实openKylin Live桌面。ext4数据盘承载工程、环境、镜像、缓存和报告，另有2 GiB swapfile；没有安装完整桌面系统。硬件加速VZ，无需新增QEMU/UTM。
- 禁用宿主目录共享、SSH agent、个人 SSH 公钥导入、代理环境继承、containerd 自动安装和 Rosetta。
- `ssh.overVsock=false`：Lima 2.1.2 在显示 GUI 前默认等待 SSH 端口，最长约 600 秒；Live 未开启 SSH 时需跳过此探测，之后仍可配置常规 usernet SSH。
- Lima配置使用合成用户名 `kmb`；Live桌面/SSH仍使用 `openkylin`，实际创建的kmb账户只用于本项目运行，不复用个人资料。客体可写存储是专用虚拟磁盘，安装ISO保持只读。
- ISO 约 6.84 GiB。实际读取镜像里的 `casper/filesystem.size` 得到 33,144,934,400 字节，约 30.87 GiB，撤回此前 15–25 GiB 的估算。当前宿主空间下不进行完整系统安装，也不删除/压缩用户文件；采用Live桌面与12 GiB专用数据盘；swap、镜像、依赖缓存和报告共用该盘，剩余容量须按实际占用检查。宿主空间低于 20 GiB 时脚本拒绝启动，运行时仍需观察实际占用。
- Lima 创建阶段可能先复制/克隆 ISO，脚本仅将本次新建的那份副本换成本项目原 ISO 的硬链接；不替换已有未知介质。此后 `iso` 与原文件共用数据，VZ 只读挂载，不保留第二份大文件。重启时 Lima 检测到已有磁盘，跳过镜像下载。
- Mac 总内存 16 GiB；安装期间避免和 4 GiB Colima 大负载并行。停机协调由主任务处理。

## 操作入口

`scripts/openkylin-vm.py prepare` 生成并校验隔离配置，不下载或启动系统。`verify` 校验完整 ISO，记录 MD5 与 SHA256。`start` 复核镜像后启动；`status` 和 `stop` 只操作专用 Lima home。脚本不删除或覆盖既有虚拟磁盘，不更改默认 Colima context。

目标安装介质是 `openKylin-Desktop-V3.0-20260905-arm64.iso`，精确大小 7,348,721,664 字节。下载与独立全文件校验已由主任务完成。官方英文下载页中的 `md5ById[132]` 对应该镜像，MD5 为 `62180a4fe3a9236a6ed1a93f9af7b52d`，已固定为脚本默认校验值。校验通过时将镜像来源、校验页、镜像 ID、MD5 与本地 SHA256 绑定记录。

Lima 用 cloud-init/SSH 判断就绪。本次 `start --timeout=45s` 因 SSH 未就绪返回 1，但 VM 保持 Running，随后实际出现桌面。不能仅按该返回码判定启动失败。首次正常 stop 的 ACPI 请求 30 秒后未完成；确认 Live 无安装、数据盘实占 0 B 后才对这个实例 force stop，保存原日志并换入口重启。不要将此过程照搬为已有试验/文件的停机策略。

## 图形工具入口和重现方式

裸 `limactl` 进程的窗口没有可供 CUA 定位的应用标识。本项目创建了私有 `.runtime/openkylin-vm/KMB openKylin.app`：官方已安装 Lima 二进制的逐字副本，加上应用元数据和只指向 Lima 公共资源的符号链接。官方二进制未修改。复制前后 SHA256 均为 `a887ffccbd56d4440356225c4ac79dd59341fdffc2fd9788bd86dcd95ecee6dd`，详情见 `app-wrapper.json`。

以下只构造包装，不启动 VM；应从项目根目录执行。已有未知包装会拒绝覆盖。

```python
import hashlib, pathlib, plistlib, shutil
state = pathlib.Path('.runtime/openkylin-vm').resolve()
app = state / 'KMB openKylin.app'
source = pathlib.Path(shutil.which('limactl')).resolve()
target = app / 'Contents/MacOS/limactl'
target.parent.mkdir(parents=True, exist_ok=True)
if target.exists():
    assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest()
else:
    shutil.copy2(source, target)
info = {
    'CFBundleExecutable': 'limactl', 'CFBundleIdentifier': 'local.kmb.openkylin-vm',
    'CFBundleName': 'KMB openKylin', 'CFBundleDisplayName': 'KMB openKylin',
    'CFBundlePackageType': 'APPL', 'CFBundleVersion': '2.1.2',
    'CFBundleShortVersionString': '2.1.2', 'LSUIElement': False,
    'NSHighResolutionCapable': True,
}
plist = app / 'Contents/Info.plist'
if plist.exists():
    assert plistlib.loads(plist.read_bytes()) == info
else:
    plist.write_bytes(plistlib.dumps(info))
link = app / 'Contents/share/lima'
link.parent.mkdir(exist_ok=True)
if link.exists():
    assert link.resolve() == pathlib.Path('/opt/homebrew/share/lima').resolve()
else:
    link.symlink_to('/opt/homebrew/share/lima', target_is_directory=True)
```

`scripts/openkylin-vm.py start` 当前仍调用标准 CLI。需要 CUA 图形操作时，应先用脚本 `verify`，确认实例已停止，再使用包装中的同一二进制启动：

```python
import os, pathlib, subprocess
state = pathlib.Path('.runtime/openkylin-vm').resolve()
env = {**os.environ, 'LIMA_HOME': str(state / 'lima')}
for key in list(env):
    if key.startswith(('KMB_', 'OPENAI_', 'ANTHROPIC_')) or key in {
        'SSH_AUTH_SOCK', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
        'http_proxy', 'https_proxy', 'all_proxy',
    }:
        env.pop(key, None)
subprocess.run([str(state / 'KMB openKylin.app/Contents/MacOS/limactl'),
                '--tty=false', 'start', '--timeout=45s', 'kylin'], env=env, timeout=75)
```

CUA 用 `cua.getApp('local.kmb.openkylin-vm')` 选择窗口。客体界面在宿主 AX 树中只显示一个 scroll area，因此需要基于专用窗口截图操作。已验证菜单点击和逐键 `pressKey` 输入；`typeText` 在这条 VZ 链路上不可靠，宿主剪贴板也不能假设与客体共享。逐键发送 ASCII 时空格、斜线、减号使用 `space`、`slash`、`minus`。每条命令后先观察结果再输入下一条，避免截图中出现后续输入与输出交错。动作后的 AX 树通常不变，需下一次截图确认客体实际变化。遇到用户改动窗口的提示，先重新获取状态。

当前保留最大化的客体终端，SSH已建立；专用盘、Docker和镜像准备实况见下节。模型代理已使用SSH反向转发到客体loopback，不在桌面粘贴真实密钥。

### SSH 连接和可接续状态

当前端点为 `127.0.0.1:61973`，用户 `openkylin`；端口可能在重启后变化，应读取私有 Lima `list --json` 的 `sshLocalPort`。连接示例从项目根目录执行：

```sh
ssh -F /dev/null \
  -i .runtime/openkylin-vm/lima/_config/user \
  -o IdentitiesOnly=yes -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=.runtime/openkylin-vm/guest-known-hosts \
  -p 61973 openkylin@127.0.0.1
```

`user` 是本项目 Lima 私钥，只留在宿主；`user.pub` 才用于客体授权。`guest-known-hosts` 是独立的本地首次连接记录，没有改动个人 SSH 配置。Live 用户已有免密码 sudo，未设置或修改任何登录密码。Lima 自动探针仍假设合成 `kmb` 用户，因此其就绪状态不等同于实际 `openkylin` SSH 可用性。

建立通道时临时服务只绑定宿主 `127.0.0.1:8768`，仅提供一条随机路径对应的合成引导脚本（2037 字节）及其内嵌专用公钥，无目录浏览、任意路径读取、模型密钥或私钥。客体经 `192.168.5.2` 成功下载，SHA256 与宿主一致：`7ea76dcf631d6faf2a4da0f00f6c332c381aff6100108411519bd688db58eefc`。执行前做了 OS/Live 启动/用户检查；安装使用独立临时 APT source/list，不覆盖 ISO 原 sources，不禁用签名或 TLS 校验。安装期间用临时 `policy-rc.d` 阻止服务提前启动，公钥限制配置通过 `sshd -t/-T` 后才启动，随后清除这个临时策略。HTTP 服务已在 SSH 验证后停止。

CUA→VZ 输入实测会丢失 Shift 修饰：冒号可能变分号、大写变小写，`typeText` 也出现同样情况。可用无组合键的 shell 八进制替代，例如以下命令会生成冒号再传给 curl（实际随机路径见本机 `bootstrap-server.json`；服务已经停止，重开需使用专用 server 脚本）：

```sh
curl -f --max-time 15 192.168.5.2`printf '\072'`8768/实际随机路径 -o /tmp/kmb.sh
sha256sum /tmp/kmb.sh
sudo -n sh /tmp/kmb.sh
```

每次 app/VM 重启后重新获取 CUA 对象，必要时 reset 后重新选择 app。桌面底部菜单的点击反馈不稳定时，已验证可右击桌面→“打开终端”；不要误点安装器。动作后即时截图可能仍是前一帧，应另一次观察确认。原始引导代码与实测日志保留在 `.runtime/openkylin-vm/prepare-ssh-bootstrap.py`、`bootstrap-server.py`，没有被新入口改写。可复用的受版本控制入口现为 `scripts/openkylin-bootstrap.py`，使用方法与验证边界见下节。

新增证据：`guest-machine-evidence.json` 是通过真实 SSH 执行 Python 得到的机器测量；`guest-ssh-connection.json` 记录连接参数与验收结果；`guest-bootstrap.log`、`guest-ssh-packages.txt`、`guest-memory-processes.txt` 保留包管理、服务配置与进程结果；`guest-ssh-bootstrap-success.png` 是专用窗口截图；`memory-resize.json` 记录调整，原日志/4 GiB 配置保存在 `boot-evidence/before-memory-6g/`。

**当前仍是Live内存overlay**：系统软件、账户记录、SSH配置/授权、Docker服务override和runtime mask随Live重启丢失；ext4盘内的工程、镜像层、缓存、报告及swapfile内容保留。文件保留不等于服务会自动恢复：重启后必须重新核对系统/设备、挂载专用盘，恢复UID/GID10001账户、SSH与官方软件，重新设置服务数据路径、启用swap并重建转发。不能称为完整安装或“一键最终复现”。

## 客体部署实况与复现边界

以下是2026-09-28已执行步骤的说明，不自动重跑任何命令；依赖同步、数据检查、doctor、双CLI探针与0模型网关连通检查已通过，update-001真实smoke和ABC评分已完成；详见reports/openkylin-smoke-v1与docs/validation-status.json。该次实际执行脚本位于私有 `.runtime/openkylin-vm/`，保留为历史，不直接重复执行；后续恢复改用下节受版本控制的分阶段入口。两者不读取或展示本地模型密钥文件。

| 环节 | 已执行或正在进行的行为 | 复现时要核对 |
|---|---|---|
| 专用数据盘 | `scripts/prepare-openkylin-data.py` 已初始化/挂载 `/dev/vda`，创建kmb及七个0700目录 | 脚本只接受专用12 GiB盘；已有未知文件系统、用户、目录或嵌套挂载会拒绝 |
| 内存 | 暂停并runtime mask `kytensor.service`，启用2 GiB `/mnt/kmb/swapfile` | 重启后mask和swap启用状态不保留；运行前读取内存/交换空间，而非沿用旧快照 |
| 官方容器软件 | `install-docker.sh` 使用独立官方签名APT源安装 `docker.io=24.0.7-ok3`、`containerd=1.5.9-ok6`、`runc=1.1.0-ok1`，0 upgrade/removal | 包版本和签名校验、安装策略及数据盘挂载；不将本机源替换成未知源 |
| Docker存储 | `configure-docker.sh` 将dockerd明确指向 `/etc/docker/daemon.json`，data-root为 `/mnt/kmb/docker`；containerd root为 `/mnt/kmb/containerd` | 必须检查实际 `DockerRootDir`，只写通用路径配置不足以证明生效 |
| 运行权限 | kmb已加入docker组；容器仍执行评测器规定的非root/唯一主体目录挂载策略 | docker组能控制客体daemon，不代表账户受容器级权限限制；这里仅用于专用实验客体 |
| 固定镜像 | 已导入，归档SHA与镜像config SHA核对一致，未重建智能体 | Docker24报告的image ID是config hash；Colima记录的是manifest ID，两种摘要不能直接按字符串判漂移 |
| 项目与Python | `install-project.sh` 解包冻结工程及校验过的uv 0.11.15 Linux ARM64包；`sync-project.sh` 已完成 `uv sync --frozen --no-dev --python /usr/bin/python3` | `UV_PYTHON_DOWNLOADS=never`，使用客体Python3.12.2；同步退出0，安装83包，数据校验60题无错误，doctor确认openKylin3.0与本地Docker |

镜像可追溯链应保留源manifest ID、导入归档SHA和目标config SHA的对应关系。正式客体运行记录目标daemon实际报告的身份；不因已核对内容相同就倒写Mac实验中的镜像字段。

openKylin的Docker入口位于 `/opt/system/bin/docker`，其原服务默认读取 `/opt/system/conf/docker/daemon.json`。本次未修改发行版unit正文，而是在 `/etc/systemd/system/docker.service.d/kmb-data.conf` 清除原ExecStart并指定 `/opt/system/bin/dockerd ... --config-file=/etc/docker/daemon.json`，然后daemon-reload并启动。containerd状态目录仍为 `/run/containerd`。这些服务文件位于Live overlay，重启必须恢复，不能仅保留盘内镜像就认为容器环境完整。

工程、uv二进制和缓存分别置于 `/mnt/kmb/project`、`/mnt/kmb/bin`、`/mnt/kmb/cache/uv`。脚本PATH显式包含 `/mnt/kmb/bin` 与 `/opt/system/bin`；同步脚本仅为依赖下载临时使用客体 `127.0.0.1:17897` 的HTTP(S)代理，与模型服务8080隧道分开。`uv sync --frozen` 会读取锁文件并安装依赖，不等于离线执行；随后脚本才用 `uv run --offline --no-sync kmb dataset validate` 检验已装环境。本次同步及随后数据校验已实际通过；不代表智能体任务成功。

模型链路为客体 `127.0.0.1:8080` 经SSH反向隧道到Mac既有代理8080。客体Docker bridge地址为 `172.17.0.1`；这是容器访问客体主机网关时的地址，不是Mac地址。后续每次TrialGateway仍留在openKylin主机侧，容器只拿临时trial token；真实上游配置不进入镜像或公共任务。隧道、模型可用性和容器工具往返都需要在正式客体运行前检查，不能从转发已建立推断benchmark通过。

平台描述须同时注明：openKylin 3.0 Live主系统/内核、Debian用户空间的OpenClaw/Hermes固定容器、Mac宿主与外部模型代理。最终真实运行、同主体会话记忆、不同主体隔离、评分及人审结果必须来自对应客体的新证据。

## 受版本控制的恢复入口（已验证一次真实冷重启）

`scripts/openkylin-bootstrap.py`、`scripts/openkylin-restore.py` 及 `prepare-openkylin-data.py` 的首次初始化门槛经过本地测试。2026-09-28在正式选型与评分结束、证据备份后，另行完成一次正常关机和官方 Live 冷启动恢复。没有中断或重跑正式试验。下列命令解释恢复流程；实际记录见文末，不可未经空闲检查直接重复执行。

### 1. 重启后的桌面 SSH 引导

Live 重启后，桌面仍须通过已有包装 app 启动，执行一次可见的引导命令。新的宿主入口只读取显式指定的普通 `.pub` 文件，验证 Ed25519 公钥结构；不读取 SSH 私钥、模型 `.env`、任意目录或用户 profile。它不会自己连接客体：

```sh
python3 scripts/openkylin-bootstrap.py render \
  --public-key .runtime/openkylin-vm/lima/_config/user.pub \
  --output .runtime/openkylin-vm/bootstrap-next.sh
python3 scripts/openkylin-bootstrap.py serve \
  --public-key .runtime/openkylin-vm/lima/_config/user.pub \
  --port 8768 --ttl-seconds 600
```

`render` 用排他创建，已有输出不会覆盖。`serve` 只在 `127.0.0.1` 提供一条随机路径，对应同一份生成脚本，输出 SHA256、长度、路径和端口；没有文件浏览或任意文件参数，未知 URL 返回 404，非 GET 不支持。请求有连接超时，服务 TTL 最长 1800 秒。

桌面用 curl 下载后，先比较 SHA256，再执行 `sudo -n sh`。脚本限定 openKylin 3.0 ARM64 Live 与既有 `openkylin` 用户，安装官方签名仓库的固定 SSH 包 `1:10.2p1-ok3`，禁止密码/root 登录，并输出客体 SSH 主机公钥指纹供核对。不重新设置密码、不修改私钥。已有授权文件或本脚本配置内容不匹配时拒绝覆盖。已有 SSH 服务/监听 socket 活跃时也拒绝重配，应先使用并检查现有连接。安装策略以 O_EXCL/O_NOFOLLOW 排他创建，拒绝悬空链接，清理时只删除本次 device/inode 对应文件。脚本限定 AuthorizedKeysFile/Command、TrustedUserCAKeys、Principals、Ed25519 算法和 AuthenticationMethods publickey，并逐项核对 agent/X11 等有效设置；对标准配置 Include 闭包之外的引用、symlink、活动 Match 规则或本脚本未接纳的 `keyword=value` 写法失败退出，并用具体连接上下文核对有效配置；授权文件在检查通过后才安装。SSH 公钥注入与主机指纹确认仍属于这个可见引导步骤，不能声称 stock ISO 自带自动 SSH。

每次 Live 启动会生成新的客体 SSH host key。应使用新的本项目 `known_hosts` 记录并与引导显示的指纹核对，保留旧文件；不要设置 `StrictHostKeyChecking=no` 或抹除已有身份记录来跳过变化。Lima 的 SSH 转发端口也需重新读取。实际网络请求只用于固定官方包安装，不包含模型调用。

### 2. 已有专用盘恢复：默认只读

在客体运行受信任脚本，`KMB_RESTORE_UUID` 必须来自保存的首次初始化/磁盘证据，不能仅按盘符或标签猜测。默认 stage 是 `inspect`，任意 stage 缺少 `--execute` 也只读检查并输出状态：

```sh
python3 scripts/openkylin-restore.py --expected-uuid "$KMB_RESTORE_UUID"
python3 scripts/openkylin-restore.py packages --expected-uuid "$KMB_RESTORE_UUID"
```

检查限定 `/dev/vda`、12 GiB、整盘 ext4、标签 `KMB_DATA`、精确 UUID、独占挂载点，并核对 UID/GID 10001、Docker 配置与固定包版本。只允许将专用 Docker 目录下形状精确匹配的 `overlay2/<64位hex>/merged` overlay 子挂载识别为忙碌状态；其他嵌套挂载仍拒绝。UUID/身份/已知配置不符直接停止。它从不格式化磁盘，也不自动停止 Docker；执行前发现任何运行容器，或已知 kmb CLI、worker、batch、subject-isolation probe、calibrate-scorers 入口进程会拒绝变更。恢复脚本之间有排他锁，但它不能阻止外部用户随后启动任务，因此仍需协调整个恢复窗口，不能把一次空闲检查当作全局调度锁。

正式试验运行时，真实客体先完成过一次默认只读 inspect：固定包和配置匹配、Docker/runner 活跃、`docker_overlay_mounts=1`、`restore_blocked=true`、`execute=false`，退出码0。该次结果只验证运行中环境可以安全诊断；当时未执行恢复阶段。即使后续 Docker/进程快照显示空闲，只要观察到 overlay 挂载，恢复仍会拒绝变更，以覆盖容器退出时的清理间隙。

| 显式 stage（加 `--execute` 才变更） | 行为与保护 |
|---|---|
| `mount` | 仅挂载已绑定 UUID 的现有盘；挂载点有内容或被其他设备占用则拒绝，不初始化任何盘 |
| `accounts` | 恢复原 UID/GID 10001 的 kmb 记录，复用原 home；七个数据目录必须仍属于原 UID、权限0700，不递归改写已有工程或报告 |
| `packages` | 独立官方 APT source/list；固定 Docker/containerd/runc 版本，预演发现升级/删除时拒绝；临时禁止安装后自动启动服务，保留未知现有策略；不替换发行版全局软件源 |
| `docker` | 只创建缺失且已知的配置、恢复组成员及启动停止状态的服务；已有配置须完全匹配；实际按已验证目录的 canonical path 核对 `DockerRootDir`，允许本系统已确认的 `/mnt`→`/var/mnt` 别名，不接纳任意路径；不自动 stop/restart，不 pull/build/load 镜像 |
| `swap` | 只重新启用已存在的2 GiB、root所有、0600且签名正确的 swapfile；不创建、截断或重新 mkswap |
| `memory` | 可选的明确操作：只停止并 runtime mask Live 的 `kytensor.service`；执行前同样要求没有运行实验，不停止 Docker |
| `project` | 仅对空的项目目录安装显式提供、SHA256匹配的工程包；先完整检查成员，拒绝路径穿越、链接、设备、重复成员、运行报告和本地 `.env`；已有非空 project 一律保留 |
| `dependencies` | 核验显式提供的 uv ARM64 归档 SHA256，只提取 uv，要求版本0.11.15；既有二进制不同则拒绝。以 kmb 与空环境运行 `uv sync --frozen --no-dev --python /usr/bin/python3`，默认 `--offline`，不下载Python、不读取模型配置 |
| `validate` | 以 kmb 和空环境执行现有 venv 的数据校验；不运行 agent 或模型，不替代双CLI探针、模型往返、评分与holdout验收 |

典型已有盘恢复顺序为 `mount` → `accounts` → 按内存决定 `memory`/`swap` → `packages` → `docker` → `validate`。每阶段独立执行和核对输出，例如：

```sh
sudo python3 scripts/openkylin-restore.py mount \
  --expected-uuid "$KMB_RESTORE_UUID" --execute
```

成功阶段在盘内 root 所有的 `.restore-events/` 新增独立记录，包含 UUID、stage、时间与“没有执行 benchmark”的范围说明。不会覆盖首次 `data-initialization.json`、旧报告或其他运行记录。失败并不意味着之前的阶段被回滚，应先 inspect 后按具体错误处理；不要通过删除未知文件来强迫继续。

原工程、venv、uv、镜像层和缓存都在专用盘中，正常恢复不需要再次解包或同步。只有明确需要补建依赖环境时才执行 `dependencies` 并提供已核验的 uv 归档；缺缓存时默认离线失败。允许下载需要额外显式 `--allow-downloads`，可选 `--package-proxy http://127.0.0.1:17897` 只接受不含凭据的 loopback 地址，不继承宿主/客体代理或模型环境。官方包版本以后若不可用，脚本失败而不是改用 latest；目前尚未提供完整的离线 APT 仓库。

### 3. 首次空盘与工程安装明确分开

首次准备新空盘才使用：

```sh
sudo python3 scripts/prepare-openkylin-data.py --initialize-blank
```

它保留原有空白签名、12 GiB专用盘、用户与挂载检查。默认不再格式化空盘；对已有盘则要求 `--expected-uuid`，不能同时用 `--initialize-blank`。原始初始化 JSON 保持原字节，首次初始化事件另存 `.data-events/`。已有盘的 `--expected-uuid` 分支现为纯只读：不 mount、不创建账户、不 chown/chmod、不追加记录；已挂载时检查原始记录与目录权限，未挂载时提示转到 UUID 绑定的 restore mount stage。发现记录对应另一块盘则拒绝。后续实际恢复事件由 restore 入口独立追加。

新项目的 `project` stage 需要 `--project-archive` 与 `--project-sha256`。`dependencies` stage 需要 `--uv-archive` 与 `--uv-sha256`。这些值应从交付包 manifest 取得，不能从下载成功或文件大小推断。归档必须来自明确受信任的工程/官方 uv 发布包；禁止解包到已有非空项目。

新盘还需按交付 manifest 手动执行固定镜像归档的校验/导入，并记录目标 daemon 的 config SHA；恢复脚本不隐式更改镜像标签。模型代理反向转发与真实上游配置也由独立流程准备，不由这些安装脚本读取或复制。SSH 成功、软件装好、单元测试通过和冷重启恢复是不同验收项；真实冷恢复应另保留新证据，不能倒写现有实验结果。

## 上游依据

- [openKylin 官方下载页](https://www.openkylin.top/downloads/os-en.html)：3.0 ARM 桌面镜像及硬件定位。
- [Lima VZ 驱动说明](https://lima-vm.io/docs/config/vmtype/vz/)：macOS 原生虚拟化和同架构限制。
- [Lima 2.1.2 VZ 设备实现](https://github.com/lima-vm/lima/blob/v2.1.2/pkg/driver/vz/vm_darwin.go)：`attachDisks` 的只读 ISO/USB 与 `attachDisplay` 图形设备。
- [Lima 2.1.2 磁盘创建](https://github.com/lima-vm/lima/blob/v2.1.2/pkg/driverutil/disk.go)：ISO 作为安装介质时另建空白磁盘。
- [Lima 2.1.2 实例启动](https://github.com/lima-vm/lima/blob/v2.1.2/pkg/instance/start.go)：已有 `image`/`disk` 跳过镜像下载。
- [Lima FAQ](https://lima-vm.io/docs/faq/)：标准自动化客户机的 cloud-init/SSH 要求和 plain 模式背景。

- [openKylin 官方软件仓库 Huanghe 目录](https://archive.openkylin.top/openkylin/dists/huanghe/)：Release 声明 Suite/Codename `huanghe`、Version `3.0`、包含 `arm64` 与 `main cross pty`。实际安装保留 APT 签名校验。

## 实际冷恢复记录：2026-09-28

选型36次运行与108次评分全部结束并备份后，正常关机、为专用盘创建APFS克隆，再启动同一官方Live ISO。克隆未做完整磁盘哈希，不能声称逐字节独立核验。新的Live系统覆盖层不保留旧系统安装；原专用数据盘保留工程、venv、镜像与证据。

恢复先经CUA输入专用公钥并目视核对新host key；首次指纹误转录被严格比对拒绝，纠正后才连接。新SSH端口为58313，known_hosts独立保存在 `.runtime/openkylin-vm/cold-restore-20260928T075026Z/guest-known-hosts`。旧61973端口及旧host key只属历史记录。

桌面自动将数据盘挂到 `/run/media/openkylin/KMB_DATA`，保护脚本拒绝异地挂载；核对精确UUID且无用户占用后正常卸载，再完成 mount、accounts、memory、swap、packages、docker、validate。未格式化、未重装工程或venv、未重新导入镜像。双智能体CLI探针通过，60题静态检查无错误，184个公开数据/元数据文件哈希与宿主一致，两镜像config SHA与重启前一致；不是完整数据盘逐文件校验。

恢复记录 `.runtime/openkylin-vm/cold-restore-20260928T075026Z/cold-restore-result.json`，SHA256 `68fec2e31e9fd717404b7f0cc643c7f0649b3580fbf441c53fcd2c80c1ae6cdf`；同目录保存50份证据摘要。恢复阶段模型调用为0，模型反向隧道尚未恢复。此记录不代表安装OS、安装.deb或录屏完成。
