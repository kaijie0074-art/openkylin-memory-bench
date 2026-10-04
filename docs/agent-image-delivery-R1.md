# 原镜像交付检查 · R1

2026-10-04，归档 `agent-images-v2.tar.gz` 已在新的 openKylin 3.0 ARM64 客体校验并导入。归档为 648,569,693 字节，SHA-256 为 `8b433d6d0ec70946b12cb5e83d088c7b298fbe889c2040b6386fd033d12ec607`。本记录不表示已公开托管。

## 摘要对应

| 智能体 | OCI manifest 摘要 | openKylin Docker classic/config 摘要 |
|---|---|---|
| OpenClaw | `648bf328d5b26a0bae290b11c8171d57fd89e9a5e7a6afc116ec74e6227e7d46` | `678ce0c64001c05afd76afce2ce5816f12fbe01036c68ed316bd30ce5ca8742d` |
| Hermes | `f11b22f1aff4bea5266da7097e05d9ea937a92e49218e52514c502aad606497e` | `1d0709138a34cde8dd0c305de53a20911c9a204a699df239a62730792eebe22d` |

归档内 manifest 实际引用上述 config；新客体 `docker image inspect` 得到 config 摘要，与工程 v3 协议一致。两套摘要表示不同对象，不能仅凭不同摘要认定镜像版本变化。版本和来源的历史记录见 [agent-images.json](agent-images.json)。

## 检查范围

AI 只读审查覆盖配置 Env、history、全部 18 个历史层的文件元数据，以及 npm 安装日志、pip 缓存、上游 `.npmrc`、Git 配置等风险目标的内容模式检查。未发现已确认的用户隐私泄露；`/trial/workspace` 为空，镜像用户主目录只有默认 shell 文件。项目 worker 的 SHA 与冻结源码一致。

这不是对全部二进制、Git 对象或任意形式秘密的完整认证。依赖 `bottleneck/.env` 仅含回环地址与端口，不能把“未发现用户配置”写成“镜像没有任何 .env”。

两款上游项目的 MIT LICENSE 保留在镜像内。Debian、npm、Python 的传递依赖各按自己的许可；整个镜像不能统一宣称为 MIT 或 Apache-2.0。本次未逐组件完成再分发条件核验，因此原镜像暂作为本机验证输入，不随 R1 公共发行上传。公开复现提供 Dockerfile 与固定主要版本；重新构建可能得到新摘要，须记录新条件，不使用旧协议冒称原条件复跑。

## 托管大小

该归档小于 GitHub Release 的单文件 2 GiB 上限，大小不是当前阻塞；公开分发仍需完成上述镜像分发检查。[GitHub 官方发行说明](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)
