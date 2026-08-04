---
name: moarkctl
description: Schedule every Moark compute container owned by one access token through explicit discovery, stable local aliases, accelerator or accelerator-free startup, detailed provider status, all-target waits, and structured lifecycle receipts.
---

# MoarkCTL

Use `mc`, the short alias for `moarkctl`, to manage the Moark compute window: discover containers, start or reboot them, wait for platform state, and confirm shutdown. Hand the running machine to [JupyterCTL](https://github.com/AkkoYK/JupyterCTL) for commands, terminals, jobs, and files.

## Configure safely

Do not read, print, or request the token in chat. If setup is missing:

1. Run `mc init` to create `~/.config/moarkctl/config.env` with private permissions.
2. Ask the operator to open [Moark Settings → Access Tokens](https://moark.com/kdakztwn/dashboard/settings/tokens), create an appropriately scoped token, and edit the config locally.
3. Ask them to place the raw value in `MOARK_TOKEN` without `Bearer`.
4. Leave `MOARK_DEFAULT_INSTANCE` empty until discovery. Initial discovery requires no instance ID.
5. If the API name is empty or unstable, ask the operator to map the full discovered ID locally. `MOARK_INSTANCE_NPU_910B=NHRNUEKVXXGAEI1U` creates the selector `npu-910b`. Never guess or abbreviate the mapped ID.

Use `--env-file PATH` or `MOARKCTL_CONFIG=PATH` only when the user-level path is unsuitable. Never rely on the current working directory for configuration. Every non-init command reports its config path on stderr.

Reject placeholder tokens such as `replace-me` or `your-token`. Treat `auth_error` as a request to verify token validity, workspace, and compute-container permissions without revealing the token. Use `dns_error`, `connect_timeout`, `tls_error`, and `api_error` plus `api_host` and `suggested_action` to choose the next diagnostic step.

## Discover before acting

Run the non-mutating checks:

```bash
mc self-test
mc ls
```

`self-test` performs API discovery only. It must not start, stop, or reboot a container. Select targets by a configured local alias, exact name, full ID, or unique ID prefix. Read `local_aliases` and `alias_errors` from JSON discovery. With several containers, never assume the first list item is intended.

Read `platform_status`, `status_detail`, and `accelerator_health` separately. Preserve `zone`, `disk_usage`, `lifecycle_timestamps`, and API-provided maintenance, alert, and health values from `health_fields`. If `accelerator_health` or `accelerator_attachment` is `unknown`, do not infer it from `running` or the configured accelerator specification.

## Change lifecycle with explicit scope

```bash
mc on TARGET -w
mc on TARGET -c -w
mc off TARGET -w
mc re TARGET -w
```

Use plain `on` for the default accelerator startup. Use `-c` / `--no-accelerator` only when the task explicitly needs CPU, disk, or network access without accelerator resources; it sends the official `with_gpu=false` parameter. If the instance is already running, expect `request_sent=false` and do not claim its accelerator mode changed. Switching modes requires an authorized shutdown followed by a new start.

Pass several selectors for a bounded set. Use `--all` only when the user explicitly intends every token-owned container. Use `-w` so every selected target reaches the requested state; use `-t SECONDS` when provisioning needs a longer wait.

For Harness parsing, add `--json`. Require `schema_version`, `command`, `target`, `started_at`, `elapsed_seconds`, `success`, `error`, and `data`. On failure, use `error.code`, `error.phase`, `error.retryable`, and `error.suggested_action`; never retry solely because a process exited nonzero. Parse the `receipts` array and match every intended target to `resolved_instance_id`. Record `local_alias`, `before`, `after`, `desired`, `request_sent`, `request_accepted`, `request_error`, `waited`, `wait_completed`, and `settled`. For start, also record `start_mode` and `with_accelerator_requested`. Treat `lifecycle_request_rejected` as a failed operation and inspect `request_failures`; do not wait indefinitely for a rejected request. For shutdown cost control, require every receipt to have `request_accepted!=false`, `wait_completed=true`, `after=stopped`, and `settled=true`.

After `on` or `re`, read `next_step` from the receipt. A configured local alias produces an exact handoff such as `jc -i npu-910b doctor --json`; run it once Jupyter is ready because the token may have rotated. This is a handoff to JupyterCTL, not permission for MoarkCTL to access Shell or files.

Set `MOARK_DEFAULT_INSTANCE` only after discovery, and only when a local alias, exact name, full ID, or unique ID prefix is stable enough to omit the selector safely.

## Run a complete research-compute session

1. Run `mc ls` and record the intended IDs and current states without exposing credentials.
2. Run `mc on TARGET -w` only when remote compute is needed.
3. Use JupyterCTL to check accelerator health, disk, caches, artifacts, and active processes. Treat `running` as a platform state, not machine readiness.
4. Run the bounded remote task and verify persisted outputs and acceptance evidence.
5. Check that background work has ended. Then run `mc off TARGET -w` and verify `stopped`.

Keep workload acceptance tied to persisted artifacts and task-specific evidence. A vanished interactive terminal is not a shutdown signal, and a successful shutdown receipt records lifecycle completion rather than experiment success. MoarkCTL exposes discovery, status, start, shutdown, reboot, and waits; JupyterCTL handles remote execution and files, while the platform console retains instance and volume administration.
