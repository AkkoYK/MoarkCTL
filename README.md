# MoarkCTL

<p align="right"><a href="./README.en.md">English</a> | <strong>简体中文</strong></p>

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="MoarkCTL 发现令牌下的全部模力方舟算力容器，并通过明确目标管理生命周期">
</p>

<p align="center">
  <strong>让 Agent 按任务启停模力方舟算力，把开机时间留给真正需要真机的环节。</strong><br>
  一个访问令牌管理全部算力容器，支持带卡或无卡开机、详细状态与全目标等待。
</p>

<p align="center"><code>Python 3.10+</code> · <code>mc</code> 短命令 · 多实例选择 · 全目标状态等待</p>

算子验证、模型微调和长时间 benchmark 往往由本地准备与远端执行交替组成。MoarkCTL 让 Agent 自己安排算力窗口：真机任务开始前开机，等待平台进入目标状态，结果保存后关机并确认计费边界。

一个访问令牌即可发现名下全部算力容器。`mc` 用本地别名稳定选择机器，支持带卡或无卡开机，返回详细平台状态和逐目标生命周期回执。工具专注于模力方舟的平台控制；机器内部的命令、任务和文件由 [JupyterCTL](https://github.com/AkkoYK/JupyterCTL) 接续处理。

## 一分钟跑通

### 1. 安装

推荐用 `pipx`，安装后同时得到 `moarkctl` 和短命令 `mc`：

```bash
cd MoarkCTL
pipx install .
```

也可以装进当前 Python 环境：

```bash
python -m pip install .
```

### 2. 创建私有配置

```bash
mc init
```

它会创建 `~/.config/moarkctl/config.env`，并在 POSIX 系统上把权限设为 `600`。配置不再依赖当前工作目录；每个非 init 命令都会在 stderr 打印实际使用的配置路径。

需要放在别处时，可以二选一：

```bash
mc --env-file /secure/path/moarkctl.env ls
MOARKCTL_CONFIG=/secure/path/moarkctl.env mc ls
```

`mc init --force` 会覆盖已有配置，只在确认旧内容不再需要时使用。

### 3. 获取并填写访问令牌

1. 登录模力方舟，切换到持有目标算力容器的工作空间。
2. 打开[设置 → 访问令牌](https://moark.com/kdakztwn/dashboard/settings/tokens)。
3. 创建令牌并立即复制。如果页面提供权限范围，至少授予算力容器读取、启动、停止和重启权限，不要开放与本工具无关的权限。
4. 编辑 `~/.config/moarkctl/config.env`，把令牌原文填入 `MOARK_TOKEN`。不要加 `Bearer`，也不要把令牌放进命令、README 或聊天记录。

```bash
MOARK_TOKEN=
MOARK_DEFAULT_INSTANCE=
MOARK_BASE_URL=https://api.moark.com/v1
```

| 环境变量 | 如何填写 | 是否必填 |
| --- | --- | --- |
| `MOARK_TOKEN` | 访问令牌页面生成的原始值，不含 `Bearer` | 必填 |
| `MOARK_DEFAULT_INSTANCE` | 先运行 `mc ls`，再填写本地别名、精确名称、完整 ID 或唯一 ID 前缀 | 可选 |
| `MOARK_INSTANCE_<本地名>` | 把本地名中的 `-` 写成 `_`，值填 `mc ls` 返回的完整实例 ID | 可选，平台名称为空时推荐 |
| `MOARK_BASE_URL` | 保持 `https://api.moark.com/v1` | 可选，通常不改 |
| `MOARK_HTTP_TIMEOUT` | 单次 API 请求超时秒数 | 可选，默认 `60` |
| `MOARK_POLL_INTERVAL` | `-w` 的轮询间隔秒数 | 可选，默认 `8` |
| `MOARK_POLL_TIMEOUT` | `-w` 的总超时秒数 | 可选，默认 `600` |

首次发现不需要配置实例 ID，`mc ls` 会列出这个令牌有权管理的全部算力容器。平台返回的 `name` 为空或不稳定时，可以在发现后加一条本地映射：

```bash
MOARK_INSTANCE_NPU_910B=NHRNUEKVXXGAEI1U
MOARK_DEFAULT_INSTANCE=npu-910b
```

环境变量后缀会转成小写，并把 `_` 变为 `-`，所以上面的容器可稳定使用 `npu-910b` 选择。`mc ls` 会在 `local_aliases` 中显示映射结果；指向不存在或不唯一目标的别名会被报告，不能用于生命周期操作。`replace-me`、`your-token` 等占位值会直接被拒绝，不会拿去请求 API。

### 4. 先做只读验收

```bash
mc self-test
mc ls
```

`self-test` 只验证配置、令牌和 Moark API 发现能力，不会启动、停止或重启任何容器。输出包含 `config_file`、实例数量和脱敏后的实例摘要。`platform_status` 是平台生命周期状态；`status_detail` 会进一步说明它属于运行、过渡、停止还是故障状态。`accelerator_health` 只在 API 提供设备健康字段时取值，否则明确为 `unknown`，不会把 `running` 冒充成 NPU / GPU 健康。

### 5. 开机与关机

```bash
# 按本地别名开机，并等待进入 running
mc on npu-910b -w

# 只需要 CPU、磁盘或网络时，无卡开机并等待进入 running
mc on npu-910b -c -w
# -c 等同于 --no-accelerator；也兼容 --no-gpu / --cpu-only

# 结果落盘后关机，并等待进入 stopped
mc off npu-910b -w
```

普通 `mc on` 保持原有行为，向官方接口发送 `with_gpu=true`；只有显式传 `-c` 才发送 `with_gpu=false`，参数语义与[模力方舟官方 OpenAPI](https://moark.com/docs/openapi/v1) 一致。如果实例已经是 `running`，命令不会为了切换模式而重启它，回执会给出 `request_sent=false` 和 `start_mode=not_requested_already_running`。要从有卡切到无卡或反向切换，应先确认任务和结果已经保存，再关机并按目标模式重新开机。

## 多实例操作保持明确

| 保护点 | 行为 |
| --- | --- |
| 先发现 | `mc ls` 读取令牌范围内的全部容器，建立当前实例清单 |
| 明确选择 | 本地别名、完整 ID、唯一 ID 前缀和平台名称都可作为目标 |
| 拒绝歧义 | 名称重复、前缀不唯一或多实例未指定目标时直接报错 |
| 显式全选 | 操作全部容器必须传 `--all` |
| 全量等待 | `-w` 逐一检查每个目标，直到全部完成或明确超时 |
| 聚焦生命周期 | 实例创建、销毁和数据盘删除仍由平台控制台管理 |

只有一个容器时，不传目标会自动选择它。账户里有多台机器时，可以在完成发现后设置 `MOARK_DEFAULT_INSTANCE`；它接受本地别名、精确名称、完整 ID 或唯一 ID 前缀。

## 常用短命令

| 短命令 | 完整命令 | 用途 |
| --- | --- | --- |
| `check` | `self-test` | 只读验证配置和 API 发现 |
| `ls` / `st` | `list` / `status` | 查看全部或指定容器 |
| `on` | `start` | 开机；默认带卡，`-c` 为无卡开机 |
| `off` | `shutdown` | 关停算力容器 |
| `re` | `reboot` | 重启算力容器 |

同时管理一组明确目标：

```bash
mc on ascend-lab nvidia-987 -w
mc off ascend-lab nvidia-987 -w
```

操作全部容器必须显式传 `--all`：

```bash
mc off --all -w
```

`-w` 是 `--wait`，`-t SECONDS` 可以覆盖本次等待时间。需要结构化列表时使用 `mc ls --json`。

## 实例状态会显示什么

`mc ls` / `mc st` 的普通输出会显示状态代码及中文含义、状态类别、算力专区、加速卡规格、设备健康、系统盘/数据盘使用率、最后更新时间和计费类型。`--json` 还会保留：

- `status_detail`：`pending`、`running`、`restarting`、`stopped`、`failed` 的分类，以及是否处于过渡态、终态或故障态；
- `zone`、`disk_usage.system_disk_rate`、`disk_usage.data_disk_rate`；
- `lifecycle_timestamps`：创建、更新、到期、启动和停止时间，同时保留平台原值与 UTC ISO 时间；
- `health_fields`：平台额外返回的维护、告警和健康字段。

官方实例列表提供加速卡规格，当前接口尚未提供“本次开机后是否实际挂载加速卡”的遥测，因此 `accelerator_attachment` 保持 `unknown`。无卡启动回执会准确记录本次请求发送了 `with_gpu=false`，实际挂载状态以平台后续提供的遥测为准。

生命周期命令也支持 `--json`：

```bash
mc on npu-910b -w --json
mc off npu-910b -w --json
```

输出始终是一个带 `receipts` 数组的对象。每个目标都有 `selector`、`resolved_instance_id`、`local_alias`、`before`、`after`、`desired`、`request_sent`、`request_accepted`、`request_error`、`waited`、`wait_completed` 和 `settled`。启动回执还带有 `start_mode` 与 `with_accelerator_requested`。未传 `-w` 时，`after` 是请求后的一次实际观察值；即使传了 `-w`，只要平台明确拒绝某个目标，请求也会立即以 `lifecycle_request_rejected` 结束，而不会把未完成的轮询写成 `settled=true`。要把关机完成当作计费边界，仍应要求每个回执都满足 `request_accepted!=false`、`wait_completed=true`、`after=stopped` 和 `settled=true`。使用本地别名启动或重启时，回执会直接给出 `next_step: jc -i npu-910b doctor --json`，便于在 Jupyter Token 可能轮换后验收控制通道。

所有机器可读 JSON 使用同一套顶层协议：`schema_version`、`command`、`target`、`started_at`、`elapsed_seconds`、`success`、`error` 和 `data`。常用结果字段仍镜像在顶层，已有脚本可以逐步迁移到 `data`。

## 可判定的网络错误

MoarkCTL 会把失败分为 `dns_error`、`connect_timeout`、`tls_error`、`auth_error`、`api_error` 或通用 `network_error`。错误只显示脱敏后的 API 主机名和建议动作，不会输出令牌。带 `--json` 的命令发生错误时，会稳定返回 `error.code`、`error.phase`、`error.retryable`、`error.api_host` 与 `error.suggested_action`，便于 Harness 决定是刷新令牌、检查网络还是稍后重试。

## 和 JupyterCTL 怎么分工

| 工具 | 负责什么 | 常用短命令 |
| --- | --- | --- |
| [MoarkCTL](https://github.com/AkkoYK/MoarkCTL) | 平台生命周期与计费边界 | `mc on`、`mc off`、`mc re` |
| [JupyterCTL](https://github.com/AkkoYK/JupyterCTL) | 机器内部的远端命令、终端与文件 | `jc x`、`jc u`、`jc d` |

`platform_status=running` 表示容器已经进入平台运行态。`mc ls --json` 会把 API 提供的维护、告警与健康字段完整保存在 `health_fields` 中；当前接口缺少设备健康信息时，`accelerator_health` 保持 `unknown`。随后用 JupyterCTL 检查驱动、加速卡、磁盘、缓存和实验环境。关机前确认后台进程已经结束，日志、checkpoint 和结果文件也已保存。

给 Agent 的紧凑开关机流程见 [SKILL.md](SKILL.md)。

## 开发与许可证

```bash
python -m pip install -e '.[dev]'
pytest
```

本项目采用 [Apache License 2.0](LICENSE)，版权归 AkkoYK 所有，详见 [NOTICE](NOTICE)。
