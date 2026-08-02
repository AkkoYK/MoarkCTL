# MoarkCTL

<p align="right"><strong>English</strong> | <a href="./README.md">简体中文</a></p>

<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="MoarkCTL discovers all token-owned Moark compute containers and controls their lifecycle through explicit targets">
</p>

<p align="center">
  <strong>Let agents start real compute only when needed, then shut it down after the results are safe.</strong><br>
  List, start, stop, and reboot Moark compute containers without mixing platform control with SSH or file transfer.
</p>

<p align="center"><code>Python 3.10+</code> · <code>mc</code> short commands · Multi-instance targeting · Wait for every target</p>

Overnight training, long benchmarks, and several heterogeneous machines make one expensive mistake especially easy: forgetting to shut a container down after the experiment. The browser console stays open, the agent has no work left, and billing continues.

MoarkCTL puts Moark compute discovery and lifecycle control behind a small set of short commands. It controls only the platform lifecycle; remote shell execution, persistent terminals, and file transfer belong to JupyterCTL.

## Get running in one minute

### 1. Install the short commands

`pipx` is recommended. It installs both `moarkctl` and its short alias, `mc`:

```bash
cd MoarkCTL
pipx install .
```

You can also install it into the current Python environment:

```bash
python -m pip install .
```

### 2. Create and configure an access token

1. Sign in to [Moark](https://moark.com) and switch to the workspace that owns the intended compute containers.
2. Open **Settings → Access Tokens**. The URL has the form `https://moark.com/<workspace>/dashboard/settings/tokens`; the workspace segment depends on the account or organization, so do not copy another user's URL.
3. Create a token and copy it immediately. If the page exposes permission scopes, grant compute-container read plus start, stop, and reboot permissions, without unrelated privileges.
4. Put the raw token in `MOARK_TOKEN`. Do not add a `Bearer` prefix or place the token in a command, README, or chat message.

Copy the template and restrict its permissions:

```bash
cp moarkctl.env.example .moarkctl.env
chmod 600 .moarkctl.env
```

The minimal configuration has one value:

```bash
MOARK_TOKEN=replace-me
```

> No instance ID is required in advance. `mc ls` discovers every compute container that the token may manage.

| Variable | What to enter | Required? |
| --- | --- | --- |
| `MOARK_TOKEN` | The raw token created under **Settings → Access Tokens** in the current workspace, without `Bearer` | Required |
| `MOARK_DEFAULT_INSTANCE` | After running `mc ls`, an exact container name, full ID, or unique ID prefix | Optional |
| `MOARK_BASE_URL` | Keep the official default, `https://api.moark.com/v1` | Optional; normally unchanged |
| `MOARK_HTTP_TIMEOUT` | Timeout for one API request, in seconds | Optional; defaults to `60` |
| `MOARK_POLL_INTERVAL` | Poll interval used by `-w`, in seconds | Optional; defaults to `8` |
| `MOARK_POLL_TIMEOUT` | Total lifecycle wait timeout, in seconds | Optional; defaults to `600` |

Validate the configuration with `mc ls`. It only reads and lists containers; it does not change their state. An HTTP 401 or 403 usually means the token belongs to the wrong workspace, has expired, or lacks compute-container permissions.

### 3. List, start, and stop containers

```bash
# Discover every manageable container.
mc ls

# Start a container by platform name and wait for running.
mc on ascend-lab -w

# After results are persisted, stop it and wait for stopped.
mc off ascend-lab -w
```

## Why multi-instance operations are difficult to mis-target

| Guardrail | Behavior |
| --- | --- |
| Discover first | `mc ls` reads every token-owned container instead of relying on a hard-coded ID |
| Select explicitly | A full ID, unique ID prefix, or exact platform name can identify a target |
| Reject ambiguity | Duplicate names, non-unique prefixes, and missing targets with multiple containers fail closed |
| Require explicit all | An operation covering every container must include `--all` |
| Wait for every target | `-w` checks every selected container rather than only the first list item |
| Avoid destructive scope | There are no commands to create or destroy containers or delete data volumes |

When the token owns exactly one container, omitting the target selects it automatically. For several containers, the optional `MOARK_DEFAULT_INSTANCE` may hold a name, full ID, or unique prefix; configuration never requires every instance ID to be hard-coded.

## Short command reference

| Short command | Full command | Purpose |
| --- | --- | --- |
| `ls` / `st` | `list` / `status` | List all containers or inspect selected ones |
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

`-w` is short for `--wait`. Use `-t SECONDS` to override the wait timeout for one operation. For structured output, run:

```bash
mc ls --json
```

## How MoarkCTL and JupyterCTL split responsibilities

| Tool | Responsibility | Common short commands |
| --- | --- | --- |
| [MoarkCTL](https://github.com/AkkoYK/MoarkCTL) | Platform lifecycle and the billing boundary | `mc on`, `mc off`, `mc re` |
| [JupyterCTL](https://github.com/AkkoYK/JupyterCTL) | Remote commands, persistent terminals, and file transfer | `jc x`, `jc u`, `jc d` |

This standalone version deliberately leaves behind the earlier prototype's SSH, remote execution, project synchronization, and file-transfer features. Separating the responsibilities keeps the Moark token limited to platform control and avoids coupling remote research work to the platform's SSH gateway.

## Agent safety boundaries

- A `running` platform state does not prove that drivers, accelerators, disks, model caches, or the research environment are ready. Recheck the machine with JupyterCTL after startup.
- A closed interactive terminal does not mean the task has finished. Before shutdown, inspect background processes and confirm that logs, checkpoints, and results are persisted.
- `mc off` proves only that the platform reached `stopped`; it does not prove that the experiment completed or passed evaluation.
- Never read or print `.moarkctl.env`, and never place `MOARK_TOKEN` in commands, logs, or chat.

See [SKILL.md](SKILL.md) for the compact start-and-stop workflow intended for coding agents.

## License

MoarkCTL is licensed under the [Apache License 2.0](LICENSE). Copyright 2026 AkkoYK. See [NOTICE](NOTICE).

## Development

```bash
python -m pip install -e '.[dev]'
pytest
```

The test suite covers multi-instance discovery and selection, ambiguity rejection, all-target polling, short-command parsing, and credential redaction.
