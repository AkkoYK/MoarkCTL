---
name: moarkctl
description: Manage every Moark compute container owned by one access token using explicit target discovery, safe multi-instance selection, lifecycle commands, and all-target status waits.
---

# MoarkCTL

Use `mc`, the short alias for `moarkctl`. Limit this skill to Moark compute-container discovery, start, shutdown, reboot, and status waits. Use [JupyterCTL](https://github.com/AkkoYK/JupyterCTL) for commands, terminals, and files inside a running machine.

## Configure safely

Do not read, print, or request the token in chat. If setup is missing:

1. Run `mc init` to create `~/.config/moarkctl/config.env` with private permissions.
2. Ask the operator to open [Moark Settings → Access Tokens](https://moark.com/kdakztwn/dashboard/settings/tokens), create an appropriately scoped token, and edit the config locally.
3. Ask them to place the raw value in `MOARK_TOKEN` without `Bearer`.
4. Leave `MOARK_DEFAULT_INSTANCE` empty until discovery. No instance ID is required in configuration.

Use `--env-file PATH` or `MOARKCTL_CONFIG=PATH` only when the user-level path is unsuitable. Never rely on the current working directory for configuration. Every non-init command reports its config path on stderr.

Reject placeholder tokens such as `replace-me` or `your-token`. On HTTP 401 or 403, ask the operator to verify token validity, workspace, and compute-container permissions without revealing the token.

## Discover before acting

Run the non-mutating checks:

```bash
mc self-test
mc ls
```

`self-test` performs API discovery only. It must not start, stop, or reboot a container. Select targets by exact name, full ID, or unique ID prefix. With several containers, never assume the first list item is intended.

## Change lifecycle with explicit scope

```bash
mc on TARGET -w
mc off TARGET -w
mc re TARGET -w
```

Pass several selectors for a bounded set. Use `--all` only when the user explicitly intends every token-owned container. Use `-w` so every selected target reaches the requested state; use `-t SECONDS` when provisioning needs a longer wait.

Set `MOARK_DEFAULT_INSTANCE` only after discovery, and only when an exact name, full ID, or unique ID prefix is stable enough to omit the selector safely.

## Follow the research-compute boundary

1. Run `mc ls` and record the intended IDs and current states without exposing credentials.
2. Run `mc on TARGET -w` only when remote compute is needed.
3. Use JupyterCTL to check accelerator health, disk, caches, artifacts, and active processes. Treat `running` as a platform state, not machine readiness.
4. Run the bounded remote task and verify persisted outputs and acceptance evidence.
5. Check that background work has ended. Then run `mc off TARGET -w` and verify `stopped`.

Do not stop a container because an interactive terminal disappeared. Do not treat a successful shutdown as evidence that an experiment completed or passed. MoarkCTL has no SSH, remote execution, file transfer, instance creation, instance deletion, or volume deletion commands.
