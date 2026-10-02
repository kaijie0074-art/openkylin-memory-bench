# 可审计的离线 .deb 构建

当前已完成真实 openKylin ARM64 构建，**0.1.0-2已通过断网安装、普通用户模拟闭环及卸载重装验收**。首次构建在动态库检查阶段失败，原失败目录保留；修复后的 v2 构建记录见文末。构建器默认只读检查，必须显式传 `--build` 才创建产物。它不连接现有 VM，不调用模型，不联网准备依赖，也不安装系统包或生成的 .deb。

本轮范围固定为 openKylin 3.0、ARM64、系统 `/usr/bin/python3` 的 Python 3.12，以及 uv 0.11.15。不支持把 Mac `.venv`、AMD64 wheel 或 uv 管理的私人解释器复制进去。当前运行闭包由 `uv.lock` 按 Linux/Python 3.12 标记及 extras 计算：82 项依赖加本项目共83包；构建器按锁计算数量，不把83写成未来所有版本的常量。

## 先由执行者准备输入

等正式 selection 资源空闲后，在单独准备的目标环境完成这些步骤。不要在评测进行中构建，不要复用正在运行的源码/venv。下载和系统依赖准备是单独、明确的操作，不由构建器自动执行。

1. 固定源码快照，记录提交和未提交文件哈希；预先构建本项目 wheel。即使同为0.1.0，旧 wheel 也必须与快照中的 `src/kmb/`、`data/` 完整路径与字节一致，否则拒绝。构建器不负责调用未锁定的构建后端重新生成 wheel。
2. 从该快照导出带哈希的依赖：`uv export --locked --offline --no-dev --no-emit-project --format requirements-txt --output-file <新requirements文件>`。在生成文件末尾追加本项目的 `openkylin-memory-bench==0.1.0 --hash=sha256:<本轮wheel实际哈希>`。不修改原始 lock，不使用 `-e .` 或目录安装。
3. 准备专用 wheelhouse：每个目标运行包恰好一个兼容 wheel，包含项目 wheel；全部 SHA256 必须在 requirements 允许集合及 lock 中，并与lock同一下载条目的文件名绑定；文件名兼容标签须与包内WHEEL标签一致，不能靠重命名把Windows/Mac包冒充Linux包。不要放源码包、Mac/AMD64 wheel、额外 wheel、符号链接、凭据或其他文件。标准导出可以保留不生效的版本标记，但实际 wheelhouse 只放目标闭包。当前 NumPy 选择 Python3.12 分支，markdown-it-py 的 linkify extra 也必须齐全。
4. 目标系统准备好 `/usr/bin/python3`、uv0.11.15、`dpkg-deb`、`dpkg-query`、`ldd`。构建器不安装这些工具。系统解释器和原生动态库依赖仍由目标 OS 提供。

## 检查与显式构建

所有参数换成已准备的真实路径。输出目录必须不存在；默认检查不创建它，也不启动任何子命令。

```bash
python3 scripts/build-deb.py \
  --source-root /path/to/frozen-source \
  --wheelhouse /path/to/linux-arm64-wheelhouse \
  --requirements /path/to/complete-hashed-requirements.txt \
  --wheel /path/to/linux-arm64-wheelhouse/openkylin_memory_bench-0.1.0-py3-none-any.whl \
  --output /path/to/new-deb-build
```

`--dry-run`是默认行为的显式写法。确认输入检查后，在规定的目标 Linux 环境对同一组参数增加 `--build`。两者不能同时使用。Mac 上的输入检查不能证明 Linux ABI 兼容；显式构建在平台不符时会拒绝。

构建器执行的顺序：

1. 校验源码、项目 wheel、完整运行闭包及每个 wheel 哈希；检查 Python 与 uv 版本。
2. 将已验证输入复制到新输出目录，复制后再检查哈希。
3. 用 `uv venv --relocatable` 创建无 seed 的私有环境；用 `uv pip install --offline --no-index --require-hashes --no-deps --no-editable --only-binary :all: --link-mode copy` 安装完整闭包。没有从索引补包或现场编译的退路。
4. 将 venv 移到封装位置，再检查解释器前缀、精确包集合、`uv pip check`；核对 wheel 的全部 dist-info 文件（安装器重写的 RECORD 除外），保留许可证和包内原生库。
5. 加入固定 `-I` 启动器 `kmb`、`kmb-batch`，及批量脚本所需的 data 链接。两个入口无需激活环境或安装 uv。
6. 读取本次已校验、已安装到 staging 的 ELF 头；本机ARM64 ELF执行 `ldd`，缺库即失败。唯一例外是已实际确认的debugpy1.8.22附带x86_64调试注入辅助库：精确路径、ELF头及SHA256均匹配才保留原字节、不执行ldd，并单独记录其不可用于ARM64的边界；其他异架构一律拒绝。外部库必须归属系统包，其实际版本进入 Depends 和构建记录。清除调用环境中的加载器覆盖变量，不借用构建者的私人共享库。
7. 检查构建路径、editable、外部缓存链接、已配置凭据字节；仅封装应用环境、脚本、许可证、离线配置示例和清单，不收 reports、runtime、个人代理文档或 `.env`。
8. `dpkg-deb`生成架构专用包。没有联网的安装脚本，也不会自动启动 Docker、导入镜像或运行评测。

输出保留 `inputs/`、`stage/`、`.deb`（仅成功时）及 `build-result.json`。失败时保留诊断状态，不复用该输出目录。成功状态是 **`built_not_installed`**，`linux_installation_verified`仍为false。记录含输入哈希、wheel版本及哈希、系统/解释器/工具身份、动态库来源和产物哈希；不记录原始环境或子进程原始输出。若最后打包命令失败但留下不完整文件，该文件仍不合格，应以结果状态和完整验收为准。

## 后续真实验收

必须在另一份干净 openKylin 环境验收，不用构建环境中的 uv 缓存、工程目录或用户包。先准备包声明的系统依赖，再断网安装；核对 dpkg 状态、动态库、安装路径和普通用户权限。

从工程目录之外执行 `kmb --help`、`kmb dataset validate/list`、`kmb doctor`、`kmb demo --output <新目录>`。数据应为60题、24/18/18，pilot12。离线 demo 的24份模拟证据和72条评分只验证链路；B版无裁判时的明确配置错误不是“ABC全部通过”。

复制 `/usr/share/doc/openkylin-memory-bench/offline-example.json` 到普通用户工作目录，执行 `kmb-batch --config <副本> --output <新目录> --dry-run`，再移除 `--dry-run`。应得到12份模拟证据、12条A版评分及HTML报告；重复输出必须拒绝。确认无网络、无模型请求，再测卸载重装及升级，保留用户输出。

真实两智能体还需要独立准备 Docker、同架构固定镜像、隔离目录及可达模型端点。`.deb`安装成功不代表这些条件满足，也不代表竞赛、双人人审或 holdout 验收完成。AMD64或其他Python版本需要另行构建和实测，当前不作兼容承诺。

## 当前验证依据

本机 uv0.11.15 的详细帮助支持 relocatable venv；已有新临时目录的一包探针验证搬迁后 `sys.prefix`、activate 和入口脚本不引用旧 staging，且项目许可保留。探针未安装依赖，基础解释器仍为Mac路径，因此只是机制验证。`tests/test_deb_builder.py`使用小型自造wheel和mock，覆盖拒绝边界；测试通过不得替代Linux构建及干净环境安装记录。

2026-09-28首次真实guest构建已完成83包离线安装、搬迁和346项dist-info文件核对，随后在debugpy辅助文件的ldd处失败。只读SSH诊断确认stage共有44个ELF：43个AArch64文件ldd成功且无缺库；剩余文件为 `debugpy/_vendored/pydevd/pydevd_attach_to_process/attach_linux_amd64.so`，ELF64 little-endian ET_DYN、machine62，其SHA256 `8fafec366c6a3b38d3429c7a9ad9957fd029f170eac9fd37bdecbbbebb3e9441` 与锁定通用wheel原件一致。ARM64上对该文件ldd实际退出1，报告“not a dynamic executable”。最小修复仅登记并跳过这个外部进程调试辅助库的本机动态检查，保留文件和许可；不承诺此调试注入功能可用，不删除上游文件，也不扩大其他架构兼容范围。后续 v2 已在新目录完整重建成功，安装验收另行记录。

## 已完成的真实构建

2026-09-28使用源码提交`87065d9`，在openKylin 3.0 ARM64/Python3.12.2/uv0.11.15上完成v2构建：83包离线安装与搬迁，346项包元数据检查，43个ARM64 ELF动态依赖检查通过。保留且登记一个锁定debugpy x86_64辅助文件，未声称该辅助功能在ARM64可用。首轮失败目录与记录保留。

产物`dist/openkylin-memory-bench_0.1.0-1_arm64.deb`共80,651,854字节，SHA256 `d968acfed8e0d139ae4d8c090ce943c9e4d3f5983d5831644189351bd6d48f91`；完整构建记录位于`reports/deb-build-v2/build-result.json`。当前状态是`built_not_installed`，不是干净系统安装、真实两智能体或竞赛验收通过。

## 安装前发现的版本指纹问题

已生成的0.1.0-1包缺少原指纹函数查找的uv.lock，导致安装布局指纹与源码布局不同；该包保留为历史构建，不能用于沿用既有校准的正式冻结。修复仅在包装阶段复制已核验的原锁文件到 `/opt/openkylin-memory-bench/venv/lib/python3.12/uv.lock`，立即核对其SHA，并将包修订号升为0.1.0-2。源代码、任务和锁内容不变。新版构建与真实安装仍需另行验证。

## 0.1.0-2 实际安装验收

新版在相同openKylin3.0/Python3.12.2环境构建，源提交3b49c02。包80,814,380字节，SHA256 `704e5fab34d041f5d52aabaa0935565a83bb762792cff6ea57c2dbde90730904`，保留旧0.1.0-1包。构建记录为 `reports/deb-build-v3/build-result.json`。

安装验收在冷启动后的同一VM新Live系统覆盖层完成，旧工程盘虽仍挂载，实际以普通用户的新HOME、工程外cwd、最小环境与仅有loopback的独立网络namespace运行；不把它称为第二台独立机器。`/usr/bin/kmb`和`kmb-batch`无需开发venv。83包版本、8,888个包内文件/链接与原deb归档核对通过；安装指纹严格等于冻结d0c5df0a3a11b5162de1acf4284e613cc669f8088bb3ba54808e57e493dfb6c8。

离线demo产生24份模拟证据和72评分：24条B为明确未配置错误，另外2条C保留客观判定、语义未知，不能说ABC全部成功。批量入口完成12份模拟证据、12条A评分和报告。两个入口重复输出均退出2且原树哈希不变。同版本重装、卸载、再安装通过，普通用户HOME及产物树不变，最终包保持安装；不同版本升级尚未实测。模型调用为0。

最终记录 `reports/deb-install-v4/acceptance.json`；原始传输归档SHA256 `6a0fb066817d672e039573a9e60e447be7d054649136d3f98852ed7d6a630d65`。前三次验收停点保留：路径别名规范化两次、C版离线部分未知预期一次；均修正一次性验收脚本，没有更改产品来迎合验收。新版真实双智能体安装入口复验见下节。

## 安装入口驱动真实智能体

`reports/deb-installed-smoke-v1`：以安装后的 `/usr/bin/kmb`、新HOME/cwd/runtime及既有固定gpt-5.5代理执行开发题update-001。OpenClaw与Hermes各完成3次不同原生会话，均生成正确current/report.txt且完整目录清单中不存在old/report.txt。安装版离线重评分产生6条ABC记录、0程序错误，均为通过；证据哈希和固定镜像摘要核对成功，配置密钥扫描0命中。原生动作仍含智能体自报，不声称独立观测了全部内部动作。

这只是安装功能复验，不替代选型、人审或holdout，不据此评价长期记忆整体可靠性。主验收文件为 `reports/deb-installed-smoke-v1/host-run-verification.json`；客体传输归档SHA256 `701d15cb5d0ca2605de70e5f95c0beaf474b9fb8837284023820a6fae2be49df`。

## 0.1.0-3 工程验证版

2026-10-02，在相同 openKylin 3.0 ARM64 Live 客体中，使用新代码/锁定依赖的静态源码快照构建 `dist/openkylin-memory-bench_0.1.0-3_arm64.deb`。产物 80,827,666 字节，SHA256 `64cc1adb2e0238c9faf46812a63c006bfcb5076fd7703b34aae5e256b5f1fdc1`；记录在 `reports/deb-build-engineering-v3/build-result.json`。83 个 wheel 的名称、版本、哈希与源码负载匹配，43 个 ARM64 ELF 动态依赖检查通过；锁定的 1 个 x86_64 调试辅助文件仍仅登记并保留，不声称可在 ARM64 使用。

同一 VM 的当前 Live 覆盖层里，软件包此前未注册，验收脚本在仅有环回接口的独立网络命名空间中断网安装。普通用户可运行 `kmb` 和 `kmb-batch`，60 题静态校验、12题模拟批处理、包内全部文件/链接核对、同版本重装及卸载重装通过，用户工作区不变。记录为 `reports/deb-install-engineering-v3/acceptance.json`，状态 `passed_installation_and_offline_simulation_only`；这仍是同一 VM，不称为另一台独立机器。

安装后的 `/usr/bin/kmb` 又以工程 v2 数据版本和冻结协议，重读已完成的 108 份真实留出证据及 324 条评分，在客体生成报告；其 `comparison.json` 与开发版逐字节一致。安装版对开发题 `boundary-001` 的两份真实证据离线重跑 A，逐项判断与原始 A 记录一致。这些检查没有重新执行留出智能体，不增加新的正式成绩；它们验证安装版能使用独立提供的工程 v2 数据集读取冻结证据。工程 v2 修订数据集并未塞入 `.deb` 的默认数据目录，正式复现时须从源码交付包显式指定该数据集。
