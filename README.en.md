# MoarkCTL

<p align="right"><strong>English</strong> | <a href="./README.md">简体中文</a></p>

<p align="center">
  <img src="./assets/readme/hero-en.svg" width="100%" alt="MoarkCTL discovers every token-owned Moark compute container and manages lifecycle through explicit targets">
</p>

<p align="center">
  <strong>Let agents start real compute when they need it, then shut it down after the results are safe.</strong><br>
  List, start, stop, and reboot Moark compute containers; leave remote shell and file transfer to JupyterCTL.
</p>

<p align="center"><code>Python 3.10+</code> · <code>mc</code> short commands · Multi-instance targeting · Wait for every target</p>

Overnight training, long benchmarks, and several heterogeneous machines make one expensive mistake easy: forgetting the final shutdown. The browser console stays open, the agent has no work left, and billing continues.

MoarkCTL puts Moark discovery and lifecycle control behind a small set of short commands. It does not create or destroy instances, access SSH, or touch data volumes. Its scope is the platform lifecycle and billing boundary.

## Get running in one minute

### 1. Install

`pipx` is recommended. It installs both `moarkctl` and the short alias `mc`:

```bash
cd MoarkCTL
pipx install .
```

Or install into the current Python environment:

```bash
python -m pip install .
```

### 2. Create the private configuration

```bash
mc init
```

This creates `~/.config/moarkctl/config.env` and sets mode `0600` on POSIX systems. Configuration no longer depends on the current directory. Every non-init command prints the exact config path to stderr.

To keep the file elsewhere, use either form:

```bash
mc --env-file /secure/path/moarkctl.env ls
MOARKCTL_CONFIG=/secure/path/moarkctl.env mc ls
```

`mc init --force` replaces an existing file. Use it only when the old configuration is no longer needed.

### 3. Create and enter the access token

1. Sign in to Moark and switch to the workspace that owns the intended compute containers.
2. Open [Settings → Access Tokens](https://moark.com/kdakztwn/dashboard/settings/tokens).
3. Create a token and copy it immediately. If scopes are available, grant compute-container read, start, stop, and reboot permissions without unrelated privileges.
4. Edit `~/.config/moarkctl/config.env` and put the raw value in `MOARK_TOKEN`. Do not add `Bearer` or place the token in a command, README, or chat message.

```bash
MOARK_TOKEN=
MOARK_DEFAULT_INSTANCE=
MOARK_BASE_URL=https://api.moark.com/v1
```

| Variable | What to enter | Required? |
| --- | --- | --- |
| `MOARK_TOKEN` | Raw value from the access-token page, without `Bearer` | Required |
| `MOARK_DEFAULT_INSTANCE` | After `mc ls`, an exact name, full ID, or unique ID prefix | Optional |
| `MOARK_BASE_URL` | Keep `https://api.moark.com/v1` | Optional; normally unchanged |
| `MOARK_HTTP_TIMEOUT` | Timeout for one API request, in seconds | Optional; defaults to `60` |
| `MOARK_POLL_INTERVAL` | Poll interval used by `-w`, in seconds | Optional; defaults to `8` |
| `MOARK_POLL_TIMEOUT` | Total lifecycle wait timeout, in seconds | Optional; defaults to `600` |

No instance ID is required in configuration. `mc ls` discovers every compute container the token may manage. Placeholder values such as `replace-me` and `your-token` are rejected before any API request.

### 4. Run the read-only acceptance check

```bash
mc self-test
mc ls
```

`self-test` checks configuration, token authentication, and Moark API discovery. It never starts, stops, or reboots a container. Its output includes `config_file`, instance count, and sanitized instance summaries.

### 5. Start and stop compute

```bash
# Start by name and wait for running.
mc on ascend-lab -w

# Stop after results are persisted and wait for stopped.
mc off ascend-lab -w
```

## Why multi-instance operations are difficult to mis-target

| Guardrail | Behavior |
| --- | --- |
| Discover first | `mc ls` reads every token-owned container instead of relying on a hard-coded ID |
| Select explicitly | Full IDs, unique ID prefixes, and exact platform names identify targets |
| Reject ambiguity | Duplicate names, non-unique prefixes, and missing targets with multiple containers fail closed |
| Require explicit all | An operation covering every container must include `--all` |
| Wait for every target | `-w` checks every selected container, not only the first item |
| Bound the scope | There are no commands to create or destroy instances or delete volumes |

When the token owns one container, omitting the target selects it automatically. With several containers, set `MOARK_DEFAULT_INSTANCE` only after discovery; it may contain an exact name, full ID, or unique ID prefix.

## Short command reference

| Short command | Full command | Purpose |
| --- | --- | --- |
| `check` | `self-test` | Read-only configuration and discovery test |
| `ls` / `st` | `list` / `status` | List all or selected containers |
| `on` | `start` | Start with the accelerator attached |
| `off` | `shutdown` | Stop a compute container |
| `re` | `reboot` | Reboot a compute container |

Manage a bounded set of explicit targets:

```bash
mc on ascend-lab nvidia-987 -w
mc off ascend-lab nvidia-987 -w
```

An operation covering every token-owned container must include `--all`:

```bash
mc off --all -w
```

`-w` is short for `--wait`. Use `-t SECONDS` to override one wait timeout. Use `mc ls --json` for a structured list.

## Responsibility split with JupyterCTL

| Tool | Responsibility | Common short commands |
| --- | --- | --- |
| [MoarkCTL](https://github.com/AkkoYK/MoarkCTL) | Platform lifecycle and the billing boundary | `mc on`, `mc off`, `mc re` |
| [JupyterCTL](https://github.com/AkkoYK/JupyterCTL) | Commands, terminals, and files inside the machine | `jc x`, `jc u`, `jc d` |

A `running` platform state does not prove that drivers, accelerators, storage, caches, or the research environment are ready. Check the real machine with JupyterCTL after startup. Before shutdown, confirm that background jobs have ended and that logs, checkpoints, and results are persisted.

See [SKILL.md](SKILL.md) for the compact agent workflow.

## Development and license

```bash
python -m pip install -e '.[dev]'
pytest
```

MoarkCTL is licensed under the [Apache License 2.0](LICENSE). Copyright 2026 AkkoYK. See [NOTICE](NOTICE).
