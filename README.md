# MoarkCTL

<p align="right"><a href="./README.en.md">English</a> | <strong>简体中文</strong></p>

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="MoarkCTL 发现令牌下的全部模力方舟算力容器，并通过明确目标管理生命周期">
</p>

<p align="center">
  <strong>让 Agent 在需要真机时开机，保存好结果后关机。</strong><br>
  查询、启动、停止与重启模力方舟算力容器；远端 Shell 和文件传输交给 JupyterCTL。
</p>

<p align="center"><code>Python 3.10+</code> · <code>mc</code> 短命令 · 多实例选择 · 全目标状态等待</p>

夜间训练、长时间 benchmark 和多台异构机器叠在一起时，最容易漏掉的是实验结束后的那次关机。网页控制台还开着，Agent 已经没有任务，费用却继续走。

MoarkCTL 把模力方舟算力容器的发现和生命周期操作收进一组短命令。它不碰实例创建、销毁、SSH 或数据盘，只负责平台控制和计费边界。

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
| `MOARK_DEFAULT_INSTANCE` | 先运行 `mc ls`，再填写常用容器的精确名称、完整 ID 或唯一 ID 前缀 | 可选 |
| `MOARK_BASE_URL` | 保持 `https://api.moark.com/v1` | 可选，通常不改 |
| `MOARK_HTTP_TIMEOUT` | 单次 API 请求超时秒数 | 可选，默认 `60` |
| `MOARK_POLL_INTERVAL` | `-w` 的轮询间隔秒数 | 可选，默认 `8` |
| `MOARK_POLL_TIMEOUT` | `-w` 的总超时秒数 | 可选，默认 `600` |

不需要配置实例 ID。`mc ls` 会发现这个令牌有权管理的全部算力容器。`replace-me`、`your-token` 等占位值会直接被拒绝，不会拿去请求 API。

### 4. 先做只读验收

```bash
mc self-test
mc ls
```

`self-test` 只验证配置、令牌和 Moark API 发现能力，不会启动、停止或重启任何容器。输出包含 `config_file`、实例数量和脱敏后的实例摘要。

### 5. 开机与关机

```bash
# 按名称开机，并等待进入 running
mc on ascend-lab -w

# 结果落盘后关机，并等待进入 stopped
mc off ascend-lab -w
```

## 多实例为什么不容易点错

| 保护点 | 行为 |
| --- | --- |
| 先发现 | `mc ls` 读取令牌范围内的全部容器，不依赖写死的实例 ID |
| 明确选择 | 完整 ID、唯一 ID 前缀和平台名称都可作为目标 |
| 拒绝歧义 | 名称重复、前缀不唯一或多实例未指定目标时直接报错 |
| 显式全选 | 操作全部容器必须传 `--all` |
| 全量等待 | `-w` 检查每个目标，不只看列表第一台 |
| 控制边界 | 没有创建、销毁实例或删除数据盘的命令 |

只有一个容器时，不传目标会自动选择它。账户里有多台机器时，可以在完成发现后设置 `MOARK_DEFAULT_INSTANCE`；它接受精确名称、完整 ID 或唯一 ID 前缀。

## 常用短命令

| 短命令 | 完整命令 | 用途 |
| --- | --- | --- |
| `check` | `self-test` | 只读验证配置和 API 发现 |
| `ls` / `st` | `list` / `status` | 查看全部或指定容器 |
| `on` | `start` | 带加速卡开机 |
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

## 和 JupyterCTL 怎么分工

| 工具 | 负责什么 | 常用短命令 |
| --- | --- | --- |
| [MoarkCTL](https://github.com/AkkoYK/MoarkCTL) | 平台生命周期与计费边界 | `mc on`、`mc off`、`mc re` |
| [JupyterCTL](https://github.com/AkkoYK/JupyterCTL) | 机器内部的远端命令、终端与文件 | `jc x`、`jc u`、`jc d` |

`running` 只是平台状态，不代表驱动、加速卡、磁盘、缓存和实验环境已经就绪。开机后用 JupyterCTL 检查真机；关机前确认后台进程已经结束，日志、checkpoint 和结果文件也已保存。

给 Agent 的紧凑开关机流程见 [SKILL.md](SKILL.md)。

## 开发与许可证

```bash
python -m pip install -e '.[dev]'
pytest
```

本项目采用 [Apache License 2.0](LICENSE)，版权归 AkkoYK 所有，详见 [NOTICE](NOTICE)。
