---
name: moarkctl
description: Manage all Moark compute containers owned by one access token, including safe target selection, start, shutdown, reboot, and multi-instance status waits.
---

# MoarkCTL

Use this skill when an Agent must control the lifecycle of compute containers on Moark. Use `mc`; it is the short alias for `moarkctl`.

MoarkCTL does not provide SSH, remote Shell execution, file synchronization, uploads, downloads, instance creation, or instance deletion. Use [JupyterCTL](https://github.com/AkkoYK/JupyterCTL) for work inside a running container.

## Configure when setup is missing

Ask the operator to create and fill the private configuration locally. Never ask them to paste the access token into chat.

1. Ask the operator to sign in to [Moark](https://moark.com) and switch to the workspace that owns the intended compute containers.
2. Open **Settings → Access Tokens**. The URL has the form `https://moark.com/<workspace>/dashboard/settings/tokens`; the workspace segment is account-specific.
3. Create a token. If the page exposes permission scopes, require compute-container read plus start, stop, and reboot permissions, and no broader permission than the task needs.
4. Copy the template, restrict it, and place the raw token in `MOARK_TOKEN` without a `Bearer` prefix:

   ```bash
   cp moarkctl.env.example .moarkctl.env
   chmod 600 .moarkctl.env
   # .moarkctl.env: MOARK_TOKEN=replace-me
   ```

An instance ID is not a required environment variable. Run `mc ls` after configuration; the token discovers every manageable container in its workspace. Set `MOARK_DEFAULT_INSTANCE` only after listing the containers, using an exact name, full ID, or unique ID prefix. Keep `MOARK_BASE_URL=https://api.moark.com/v1` unless the platform explicitly supplies another endpoint.

Treat `mc ls` as the non-mutating configuration check. On HTTP 401 or 403, ask the operator to verify the workspace, token validity, and compute-container permissions without revealing the token.

## Discover before acting

Run:

```bash
mc ls
```

The access token discovers every manageable container, so an instance ID is not required in configuration. Select a target by exact platform name, full ID, or unique ID prefix. When several instances exist, never assume that the first list item is the intended target.

## Lifecycle commands

```bash
mc on TARGET -w
mc off TARGET -w
mc re TARGET -w
```

Use several selectors to manage a bounded set:

```bash
mc on TARGET_A TARGET_B -w
mc off TARGET_A TARGET_B -w
```

Use `--all` only when the task explicitly covers every token-owned container:

```bash
mc off --all -w
```

`on` means start with the accelerator attached. `-w` waits for all selected instances, not just the first. Use `-t SECONDS` when provisioning is expected to exceed the configured timeout.

## Safe research workflow

1. Run `mc ls` and record the intended target IDs and current states without exposing the token.
2. Run `mc on TARGET -w` only when remote compute is needed.
3. Use JupyterCTL to recheck accelerator type and health, disk, caches, artifacts, and active processes. A `running` platform state is not a readiness result.
4. Run the bounded remote task. Keep process health, persisted artifacts, evaluation, and target-device evidence separate.
5. Confirm results and logs are persisted. Then run `mc off TARGET -w` and verify `stopped`.

Do not stop an instance merely because an interactive terminal disappeared. Check the remote process and persisted outputs first. Do not treat a successful shutdown as evidence that the experiment completed or passed.

## Credentials and errors

Never read or print `.moarkctl.env`. Do not paste `MOARK_TOKEN` into commands, logs, or chat. If an API call fails, report the HTTP status and redacted detail. Re-run `mc ls` before retrying a lifecycle change so that stale IDs or names are not reused blindly.
