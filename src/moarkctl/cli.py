#!/usr/bin/env python3
"""A small lifecycle controller for Moark compute containers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import time
from typing import Any, Iterable
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "https://api.moark.com/v1"
VERSION = "0.6.0"
MACHINE_SCHEMA_VERSION = "1.0"
CONFIG_ENV_VAR = "MOARKCTL_CONFIG"
INSTANCE_ALIAS_PREFIX = "MOARK_INSTANCE_"
DEFAULT_HTTP_TIMEOUT = 60.0
DEFAULT_POLL_INTERVAL = 8.0
DEFAULT_POLL_TIMEOUT = 600.0
PLACEHOLDER_VALUES = {
    "change-me",
    "changeme",
    "replace-me",
    "token-here",
    "your-token",
    "your_token",
}
INITIAL_CONFIG = """# MoarkCTL user configuration
# Create an access token under Moark workspace Settings -> Access Tokens.
# Paste only the raw token value. Do not add a Bearer prefix.
MOARK_TOKEN=

# Optional. Run `mc ls` first, then use an exact name, full ID, or unique ID
# prefix when one container should be the default lifecycle target.
MOARK_DEFAULT_INSTANCE=

# Optional local aliases. The suffix becomes lowercase and `_` becomes `-`.
# MOARK_INSTANCE_NPU_910B=NHRNUEKVXXGAEI1U

MOARK_BASE_URL=https://api.moark.com/v1
MOARK_HTTP_TIMEOUT=60
MOARK_POLL_INTERVAL=8
MOARK_POLL_TIMEOUT=600
"""
SECRET_KEYS = {
    "authorization",
    "cookie",
    "key",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
}
HEALTH_FIELD_KEYS = (
    "accelerator_health",
    "gpu_health",
    "device_health",
    "accelerator_status",
    "gpu_status",
    "device_status",
    "health",
    "health_status",
    "health_info",
    "node_health",
    "node_status",
    "node_alerts",
    "maintenance",
    "maintenance_status",
    "alerts",
    "alarms",
    "warnings",
    "abnormal_reason",
)
PLATFORM_STATUS_DETAILS = {
    "restarting": {
        "label_zh": "重启中",
        "category": "transitioning",
        "transitional": True,
        "terminal": False,
        "failure": False,
    },
    "pending": {
        "label_zh": "等待中",
        "category": "transitioning",
        "transitional": True,
        "terminal": False,
        "failure": False,
    },
    "running": {
        "label_zh": "运行中",
        "category": "active",
        "transitional": False,
        "terminal": False,
        "failure": False,
    },
    "stopped": {
        "label_zh": "已停止",
        "category": "inactive",
        "transitional": False,
        "terminal": True,
        "failure": False,
    },
    "failed": {
        "label_zh": "失败",
        "category": "error",
        "transitional": False,
        "terminal": True,
        "failure": True,
    },
}
LIFECYCLE_TIMESTAMP_FIELDS = (
    "created_at",
    "updated_at",
    "expired_at",
    "started_at",
    "stopped_at",
)


class MoarkCtlError(RuntimeError):
    """Expected configuration, selection, API, or lifecycle failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "operation_failed",
        api_host: str = "",
        suggested_action: str = "",
        phase: str = "",
        retryable: bool | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.api_host = api_host
        self.suggested_action = suggested_action
        self.phase = phase
        self.retryable = (
            code in {"dns_error", "connect_timeout", "network_error", "api_error"}
            if retryable is None
            else retryable
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": str(self),
                "api_host": self.api_host,
                "phase": self.phase or None,
                "retryable": self.retryable,
                "suggested_action": self.suggested_action,
            }
        }


@dataclass(frozen=True)
class MoarkConfig:
    token: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    http_timeout: float = DEFAULT_HTTP_TIMEOUT
    poll_interval: float = DEFAULT_POLL_INTERVAL
    poll_timeout: float = DEFAULT_POLL_TIMEOUT
    default_instance: str = ""
    config_file: str = ""
    aliases: dict[str, str] = field(default_factory=dict)


def default_config_path() -> Path:
    override = os.environ.get(CONFIG_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    xdg_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg_home).expanduser() if xdg_home else Path.home() / ".config"
    return base / "moarkctl" / "config.env"


def resolve_config_path(path: str | os.PathLike[str] | None) -> Path:
    return Path(path).expanduser() if path else default_config_path()


def initialize_config(
    path: str | os.PathLike[str] | None,
    *,
    force: bool,
) -> Path:
    config_path = resolve_config_path(path)
    config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_TRUNC if force else os.O_EXCL
    try:
        descriptor = os.open(config_path, flags, 0o600)
    except FileExistsError as exc:
        raise MoarkCtlError(
            f"config already exists: {config_path}; edit it or pass --force"
        ) from exc
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(INITIAL_CONFIG)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return config_path


def is_placeholder(value: str) -> bool:
    normalized = value.strip().strip("'\"").casefold()
    return (
        normalized in PLACEHOLDER_VALUES
        or (normalized.startswith("<") and normalized.endswith(">"))
        or normalized.startswith("your-")
    )


def instance_aliases_from_env() -> dict[str, str]:
    aliases: dict[str, str] = {}
    alias_sources: dict[str, str] = {}
    for key, raw_value in os.environ.items():
        if not key.startswith(INSTANCE_ALIAS_PREFIX):
            continue
        suffix = key[len(INSTANCE_ALIAS_PREFIX) :]
        alias = re.sub(r"_+", "-", suffix.casefold()).strip("-")
        value = raw_value.strip()
        if not alias or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", alias):
            raise MoarkCtlError(
                f"{key} does not form a valid local alias",
                code="configuration_invalid",
                phase="config",
                suggested_action=(
                    "Use only letters, digits, and underscores after "
                    "MOARK_INSTANCE_."
                ),
            )
        if alias == "all":
            raise MoarkCtlError(
                f"{key} uses the reserved alias 'all'",
                code="configuration_invalid",
                phase="config",
                suggested_action="Choose another local alias.",
            )
        if not value or is_placeholder(value):
            raise MoarkCtlError(
                f"{key} is empty or still a placeholder",
                code="configuration_invalid",
                phase="config",
                suggested_action="Run 'mc ls', then paste an exact instance ID.",
            )
        if alias in aliases and aliases[alias] != value:
            raise MoarkCtlError(
                f"{key} conflicts with {alias_sources[alias]} after alias normalization",
                code="configuration_invalid",
                phase="config",
                suggested_action="Keep exactly one environment variable per local alias.",
            )
        aliases[alias] = value
        alias_sources[alias] = key
    return dict(sorted(aliases.items()))


def load_env_file(path: str | os.PathLike[str]) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    if os.name == "posix" and env_path.stat().st_mode & 0o077:
        raise MoarkCtlError(
            f"credential file is too permissive: {env_path}; run chmod 600"
        )
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise MoarkCtlError(f"{name} must be numeric") from exc
    if value <= 0:
        raise MoarkCtlError(f"{name} must be positive")
    return value


def config_from_env(
    env_file: str | os.PathLike[str] | None = None,
) -> MoarkConfig:
    config_path = resolve_config_path(env_file)
    load_env_file(config_path)
    token = os.environ.get("MOARK_TOKEN", "")
    if not token:
        raise MoarkCtlError(
            f"MOARK_TOKEN is required; run 'mc init' and edit {config_path}"
        )
    if is_placeholder(token):
        raise MoarkCtlError(
            f"MOARK_TOKEN is still a placeholder in {config_path}"
        )
    base_url = os.environ.get("MOARK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MoarkCtlError("MOARK_BASE_URL must be an absolute http(s) URL")
    if parsed.hostname and parsed.hostname.casefold().endswith(".invalid"):
        raise MoarkCtlError("MOARK_BASE_URL uses the reserved .invalid domain")
    return MoarkConfig(
        token=token,
        base_url=base_url,
        http_timeout=_positive_float("MOARK_HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT),
        poll_interval=_positive_float(
            "MOARK_POLL_INTERVAL", DEFAULT_POLL_INTERVAL
        ),
        poll_timeout=_positive_float("MOARK_POLL_TIMEOUT", DEFAULT_POLL_TIMEOUT),
        default_instance=os.environ.get("MOARK_DEFAULT_INSTANCE", ""),
        config_file=str(config_path.resolve()),
        aliases=instance_aliases_from_env(),
    )


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def machine_error(
    *,
    code: str | None = None,
    phase: str | None = None,
    retryable: bool = False,
    suggested_action: str | None = None,
    message: str | None = None,
    api_host: str = "",
) -> dict[str, Any]:
    return {
        "code": code,
        "phase": phase,
        "retryable": retryable,
        "suggested_action": suggested_action,
        "message": message,
        "api_host": api_host,
    }


def machine_envelope(
    *,
    command: str,
    target: Any,
    started_at: str,
    started_monotonic: float,
    success: bool,
    payload: Any,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "schema_version": MACHINE_SCHEMA_VERSION,
        "command": command,
        "target": target,
        "started_at": started_at,
        "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
        "success": success,
        "error": error or machine_error(),
        "data": payload,
    }
    if isinstance(payload, dict):
        reserved = {
            "schema_version",
            "command",
            "target",
            "started_at",
            "elapsed_seconds",
            "success",
            "error",
            "data",
        }
        result.update(
            {key: value for key, value in payload.items() if key not in reserved}
        )
    return result


def scrub_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if key.casefold() in SECRET_KEYS else scrub_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_secrets(item) for item in value]
    return value


def api_hostname(base_url: str) -> str:
    return urllib.parse.urlsplit(base_url).hostname or ""


def classify_network_error(reason: Any) -> tuple[str, str]:
    text = str(reason).casefold()
    if isinstance(reason, socket.gaierror) or any(
        marker in text
        for marker in (
            "name or service not known",
            "nodename nor servname",
            "temporary failure in name resolution",
        )
    ):
        return (
            "dns_error",
            "Check DNS, VPN/proxy settings, and MOARK_BASE_URL, then retry.",
        )
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in text:
        return (
            "connect_timeout",
            "Check network reachability or increase MOARK_HTTP_TIMEOUT, then retry.",
        )
    if isinstance(reason, ssl.SSLError) or any(
        marker in text
        for marker in ("certificate verify failed", "tls", "ssl")
    ):
        return (
            "tls_error",
            "Check the system clock, certificate chain, and HTTPS proxy settings.",
        )
    return (
        "network_error",
        "Check network reachability, VPN/proxy settings, and the API host.",
    )


def operation_id_from_response(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in (
        "operation_id",
        "operationId",
        "request_id",
        "requestId",
        "task_id",
        "taskId",
    ):
        candidate = value.get(key)
        if candidate is not None and str(candidate):
            return str(candidate)
    for key in ("data", "operation", "result"):
        nested = operation_id_from_response(value.get(key))
        if nested:
            return nested
    return None


def lifecycle_results_by_id(value: Any) -> dict[str, dict[str, Any]]:
    candidates = value
    if isinstance(value, dict):
        for key in ("results", "data", "items"):
            if isinstance(value.get(key), list):
                candidates = value[key]
                break
    if not isinstance(candidates, list):
        return {}
    results: dict[str, dict[str, Any]] = {}
    for item in candidates:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id") or item.get("instance_id")
        if item_id is not None and str(item_id):
            results[str(item_id)] = scrub_secrets(item)
    return results


def extract_health_fields(value: Any) -> Any:
    keywords = (
        "health",
        "alert",
        "alarm",
        "warning",
        "maintenance",
        "fault",
        "abnormal",
    )
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            folded = key.casefold()
            if key in HEALTH_FIELD_KEYS or any(word in folded for word in keywords):
                result[key] = scrub_secrets(item)
                continue
            nested = extract_health_fields(item)
            if nested not in ({}, []):
                result[key] = nested
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            nested = extract_health_fields(item)
            if nested not in ({}, []):
                result.append(nested)
        return result
    return {}


class MoarkClient:
    def __init__(self, config: MoarkConfig):
        self.config = config

    def _redact(self, value: Any) -> str:
        text = str(value)
        if self.config.token:
            text = text.replace(self.config.token, "***")
            encoded = urllib.parse.quote(self.config.token, safe="")
            text = text.replace(encoded, "***")
        text = re.sub(
            r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,}\]]+",
            r"\1***",
            text,
        )
        return text

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
    ) -> Any:
        url = f"{self.config.base_url}{path}"
        query_items = {
            key: value
            for key, value in (query or {}).items()
            if value is not None and value != ""
        }
        if query_items:
            url += "?" + urllib.parse.urlencode(query_items)
        payload = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.config.token}",
            "User-Agent": f"MoarkCTL/{VERSION}",
        }
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=payload,
            headers=headers,
            method=method.upper(),
        )
        host = api_hostname(self.config.base_url)
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.http_timeout
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = self._redact(
                exc.read(4096).decode("utf-8", errors="replace")
            )
            if exc.code in {401, 403}:
                raise MoarkCtlError(
                    f"Moark HTTP {exc.code}: {detail[:1000]}",
                    code="auth_error",
                    api_host=host,
                    suggested_action=(
                        "Create or refresh the Moark access token under workspace "
                        "Settings -> Access Tokens, then update MOARK_TOKEN."
                    ),
                    phase="api_request",
                ) from exc
            raise MoarkCtlError(
                f"Moark HTTP {exc.code}: {detail[:1000]}",
                code="api_error",
                api_host=host,
                suggested_action=(
                    "Retry after checking the request and Moark platform status."
                ),
                phase="api_request",
            ) from exc
        except urllib.error.URLError as exc:
            code, action = classify_network_error(exc.reason)
            raise MoarkCtlError(
                f"Moark network error: {self._redact(exc.reason)}",
                code=code,
                api_host=host,
                suggested_action=action,
                phase="api_request",
            ) from exc
        except socket.gaierror as exc:
            code, action = classify_network_error(exc)
            raise MoarkCtlError(
                f"Moark network error: {self._redact(exc)}",
                code=code,
                api_host=host,
                suggested_action=action,
                phase="api_request",
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            code, action = classify_network_error(exc)
            raise MoarkCtlError(
                f"Moark network error: {self._redact(exc)}",
                code=code,
                api_host=host,
                suggested_action=action,
                phase="api_request",
            ) from exc
        except ssl.SSLError as exc:
            code, action = classify_network_error(exc)
            raise MoarkCtlError(
                f"Moark network error: {self._redact(exc)}",
                code=code,
                api_host=host,
                suggested_action=action,
                phase="api_request",
            ) from exc
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise MoarkCtlError(
                "Moark returned non-JSON data",
                code="api_error",
                api_host=host,
                suggested_action="Retry after checking Moark platform status.",
                phase="api_request",
            ) from exc

    def instances(self, ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        joined_ids = ",".join(ids or [])
        result = self.request(
            "GET", "/compute/instances", query={"ids": joined_ids or None}
        )
        if not isinstance(result, list):
            raise MoarkCtlError(
                f"unexpected instance list response: {scrub_secrets(result)!r}",
                code="api_error",
                phase="api_discovery",
                retryable=True,
                suggested_action="Retry after checking Moark platform status.",
            )
        return [item for item in result if isinstance(item, dict)]

    def lifecycle(
        self,
        action: str,
        ids: Iterable[str],
        *,
        with_accelerator: bool = True,
    ) -> Any:
        target_ids = list(ids)
        if not target_ids:
            return None
        query: dict[str, Any] = {"ids": ",".join(target_ids)}
        if action == "start":
            query["with_gpu"] = "true" if with_accelerator else "false"
        return self.request("POST", f"/compute/instances/{action}", query=query)


def instance_id(instance: dict[str, Any]) -> str:
    for key in ("id", "instance_id", "uuid"):
        value = instance.get(key)
        if value is not None and str(value):
            return str(value)
    raise MoarkCtlError(f"instance has no id: {scrub_secrets(instance)!r}")


def instance_name(instance: dict[str, Any]) -> str:
    for key in ("name", "instance_name", "display_name"):
        value = instance.get(key)
        if value is not None and str(value):
            return str(value)
    return ""


def instance_status(instance: dict[str, Any]) -> str:
    return str(instance.get("status") or instance.get("state") or "unknown")


def platform_status_detail(status: str) -> dict[str, Any]:
    code = status.casefold()
    known = PLATFORM_STATUS_DETAILS.get(code)
    if known is None:
        return {
            "code": status,
            "label_zh": "未知",
            "category": "unknown",
            "known": False,
            "transitional": False,
            "terminal": False,
            "failure": False,
        }
    return {"code": code, "known": True, **known}


def timestamp_detail(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {"raw": None, "iso": None}
    try:
        numeric = float(value)
        seconds = (
            numeric / 1000.0
            if abs(numeric) >= 100_000_000_000
            else numeric
        )
        instant = datetime.fromtimestamp(seconds, tz=timezone.utc)
        iso_value = instant.isoformat(timespec="seconds").replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        iso_value = None
    return {"raw": value, "iso": iso_value}


def instance_summary(
    instance: dict[str, Any],
    *,
    local_aliases: Iterable[str] = (),
) -> dict[str, Any]:
    status = instance_status(instance)
    model = (
        instance.get("gpu_model")
        or instance.get("accelerator_model")
        or instance.get("device_model")
        or ""
    )
    count = (
        instance.get("gpu_num")
        or instance.get("accelerator_num")
        or instance.get("device_num")
        or ""
    )
    accelerator = str(model)
    if model and count != "":
        accelerator = f"{model} x{count}"
    health_fields = extract_health_fields(instance)
    accelerator_health = "unknown"
    accelerator_health_source = None
    for key in (
        "accelerator_health",
        "gpu_health",
        "device_health",
        "accelerator_status",
        "gpu_status",
        "device_status",
    ):
        value = instance.get(key)
        if value is not None and str(value):
            accelerator_health = scrub_secrets(value)
            accelerator_health_source = key
            break
    lifecycle_timestamps = {
        key: timestamp_detail(instance.get(key))
        for key in LIFECYCLE_TIMESTAMP_FIELDS
    }
    return {
        "id": instance_id(instance),
        "name": instance_name(instance),
        "local_aliases": sorted(local_aliases),
        "platform_status": status,
        "status": status,
        "status_detail": platform_status_detail(status),
        "zone": instance.get("zone") or "",
        "accelerator": accelerator,
        "accelerator_attachment": "unknown",
        "accelerator_health": accelerator_health,
        "accelerator_health_source": accelerator_health_source,
        "health_fields": health_fields,
        "disk_usage": {
            "system_disk_rate": instance.get("system_disk_rate"),
            "data_disk_rate": instance.get("data_disk_rate"),
        },
        "lifecycle_timestamps": lifecycle_timestamps,
        "billing": instance.get("billing_type") or instance.get("billing") or "",
    }


def _split_selectors(selectors: Iterable[str]) -> list[str]:
    return [
        part.strip()
        for selector in selectors
        for part in selector.split(",")
        if part.strip()
    ]


def resolve_instances(
    instances: list[dict[str, Any]],
    selectors: Iterable[str],
    *,
    all_instances: bool,
    default_selector: str = "",
    aliases: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    requested = _split_selectors(selectors)
    if all_instances and requested:
        raise MoarkCtlError("pass selectors or --all, not both")
    if all_instances:
        if not instances:
            raise MoarkCtlError("the token owns no compute instances")
        return list(instances)
    if not requested and default_selector:
        requested = [default_selector]
    if not requested:
        if len(instances) == 1:
            return list(instances)
        if not instances:
            raise MoarkCtlError("the token owns no compute instances")
        raise MoarkCtlError(
            "multiple compute instances found; pass an id/name selector or --all"
        )

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    alias_map = aliases or {}
    for selector in requested:
        mapped_selector = alias_map.get(selector.casefold(), selector)
        folded = mapped_selector.casefold()
        exact = [
            item
            for item in instances
            if instance_id(item) == mapped_selector
            or instance_name(item).casefold() == folded
        ]
        matches = exact
        if not matches:
            matches = [
                item
                for item in instances
                if instance_id(item).startswith(mapped_selector)
            ]
        if not matches:
            if mapped_selector != selector:
                raise MoarkCtlError(
                    f"local alias {selector!r} maps to {mapped_selector!r}, but no "
                    "compute instance matches it",
                    code="instance_not_found",
                    phase="selection",
                    suggested_action="Run 'mc ls --json' and update the alias mapping.",
                )
            raise MoarkCtlError(
                f"no compute instance matches {selector!r}",
                code="instance_not_found",
                phase="selection",
                suggested_action="Run 'mc ls --json' and check the selector.",
            )
        if len(matches) > 1:
            matched_ids = ", ".join(instance_id(item) for item in matches)
            raise MoarkCtlError(
                f"compute instance selector {selector!r} is ambiguous: {matched_ids}",
                code="ambiguous_selector",
                phase="selection",
                suggested_action="Use an exact instance ID in the alias mapping.",
            )
        item = matches[0]
        item_id = instance_id(item)
        if item_id not in selected_ids:
            selected.append(item)
            selected_ids.add(item_id)
    return selected


def resolve_alias_bindings(
    instances: list[dict[str, Any]],
    aliases: dict[str, str],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    bindings = {instance_id(item): [] for item in instances}
    errors: dict[str, str] = {}
    for alias, selector in aliases.items():
        try:
            target = resolve_instances(
                instances,
                [selector],
                all_instances=False,
                aliases={},
            )[0]
        except MoarkCtlError as exc:
            errors[alias] = str(exc)
            continue
        bindings[instance_id(target)].append(alias)
    for values in bindings.values():
        values.sort()
    return bindings, errors


def summarize_instances(
    instances: list[dict[str, Any]],
    aliases: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    bindings, errors = resolve_alias_bindings(instances, aliases)
    summaries = [
        instance_summary(item, local_aliases=bindings.get(instance_id(item), ()))
        for item in instances
    ]
    return summaries, errors


def print_instances(
    instances: list[dict[str, Any]],
    *,
    aliases: dict[str, str] | None = None,
) -> None:
    summaries, alias_errors = summarize_instances(instances, aliases or {})
    if not summaries:
        print("No instances.")
        return
    print(
        "id\taliases\tname\tstatus\tcategory\tzone\taccelerator_spec\t"
        "accelerator_health\tdisk_rate(system/data)\tupdated_at\tbilling"
    )
    for item in summaries:
        status_detail = item["status_detail"]
        disk_usage = item["disk_usage"]
        updated_at = item["lifecycle_timestamps"]["updated_at"]["iso"]
        item = {
            **item,
            "aliases": ",".join(item["local_aliases"]),
            "status_display": (
                f"{item['status']}/{status_detail['label_zh']}"
                if status_detail["known"]
                else item["status"]
            ),
            "status_category": status_detail["category"],
            "disk_rates": (
                f"{disk_usage['system_disk_rate'] if disk_usage['system_disk_rate'] is not None else '-'}"
                "/"
                f"{disk_usage['data_disk_rate'] if disk_usage['data_disk_rate'] is not None else '-'}"
            ),
            "updated_at_display": updated_at or "-",
        }
        print(
            "\t".join(
                str(item[key] or "-")
                for key in (
                    "id",
                    "aliases",
                    "name",
                    "status_display",
                    "status_category",
                    "zone",
                    "accelerator",
                    "accelerator_health",
                    "disk_rates",
                    "updated_at_display",
                    "billing",
                )
            )
        )
    for alias, detail in alias_errors.items():
        print(f"alias {alias}: {detail}", file=sys.stderr)


def select_for_action(
    client: MoarkClient, args: argparse.Namespace
) -> list[dict[str, Any]]:
    return resolve_instances(
        client.instances(),
        args.selectors,
        all_instances=args.all_instances,
        default_selector=client.config.default_instance,
        aliases=client.config.aliases,
    )


def wait_for_status(
    client: MoarkClient,
    ids: Iterable[str],
    desired: str,
    *,
    timeout: float,
    progress: Any = print,
) -> list[dict[str, Any]]:
    target_ids = list(ids)
    deadline = time.monotonic() + timeout
    previous: tuple[tuple[str, str], ...] | None = None
    while True:
        instances = client.instances(target_ids)
        by_id = {instance_id(item): item for item in instances}
        missing = [item_id for item_id in target_ids if item_id not in by_id]
        if missing:
            raise MoarkCtlError(
                f"Moark did not return selected instances: {', '.join(missing)}"
            )
        snapshot = tuple(
            (item_id, instance_status(by_id[item_id])) for item_id in target_ids
        )
        if snapshot != previous:
            if progress is not None:
                progress(
                    " | ".join(
                        f"{item_id}={status}" for item_id, status in snapshot
                    )
                )
            previous = snapshot
        if all(status == desired for _, status in snapshot):
            return [by_id[item_id] for item_id in target_ids]
        failed = [item_id for item_id, status in snapshot if status == "failed"]
        if failed:
            raise MoarkCtlError(
                f"instances entered failed state: {', '.join(failed)}"
            )
        if time.monotonic() >= deadline:
            states = ", ".join(f"{item_id}={status}" for item_id, status in snapshot)
            raise MoarkCtlError(
                f"timed out waiting for status={desired}: {states}"
            )
        time.sleep(client.config.poll_interval)


def command_list(client: MoarkClient, args: argparse.Namespace) -> dict[str, Any]:
    instances = client.instances()
    if args.selectors:
        instances = resolve_instances(
            instances,
            args.selectors,
            all_instances=False,
            aliases=client.config.aliases,
        )
    summaries, alias_errors = summarize_instances(instances, client.config.aliases)
    report = {
        "instances": summaries,
        "instance_count": len(summaries),
        "aliases": client.config.aliases,
        "alias_errors": alias_errors,
    }
    if not args.as_json:
        print_instances(instances, aliases=client.config.aliases)
    return report


def command_lifecycle(
    client: MoarkClient,
    args: argparse.Namespace,
    *,
    api_action: str,
    desired: str,
) -> dict[str, Any]:
    started = time.monotonic()
    with_accelerator = bool(getattr(args, "with_accelerator", True))
    targets = select_for_action(client, args)
    pending = targets
    if api_action != "reboot":
        pending = [item for item in targets if instance_status(item) != desired]
    target_ids = [instance_id(item) for item in targets]
    pending_ids = [instance_id(item) for item in pending]
    lifecycle_result: Any = None
    if pending_ids:
        if api_action == "start":
            lifecycle_result = client.lifecycle(
                api_action,
                pending_ids,
                with_accelerator=with_accelerator,
            )
        else:
            lifecycle_result = client.lifecycle(api_action, pending_ids)
        if not args.as_json:
            mode = (
                " with accelerator"
                if api_action == "start" and with_accelerator
                else " without accelerator"
                if api_action == "start"
                else ""
            )
            print(f"{api_action}{mode} requested: {', '.join(pending_ids)}")
    elif not args.as_json:
        print(f"already {desired}: {', '.join(target_ids)}")
    request_results = lifecycle_results_by_id(lifecycle_result)
    rejected_ids = {
        item_id
        for item_id, item in request_results.items()
        if item.get("success") is False
    }
    observation_error: MoarkCtlError | None = None
    wait_completed = False
    if args.wait and not rejected_ids:
        final = wait_for_status(
            client,
            target_ids,
            desired,
            timeout=args.timeout or client.config.poll_timeout,
            progress=None if args.as_json else print,
        )
        wait_completed = True
    elif pending_ids:
        try:
            final = client.instances(target_ids)
        except MoarkCtlError as exc:
            observation_error = exc
            final = []
    else:
        final = targets

    final_by_id = {instance_id(item): item for item in final}
    before_by_id = {instance_id(item): item for item in targets}
    requested_selectors = _split_selectors(args.selectors)
    alias_bindings, _ = resolve_alias_bindings(targets, client.config.aliases)

    def selector_for(item: dict[str, Any]) -> str:
        item_id = instance_id(item)
        folded_name = instance_name(item).casefold()
        for selector in requested_selectors:
            mapped = client.config.aliases.get(selector.casefold(), selector)
            if (
                mapped == item_id
                or mapped.casefold() == folded_name
                or item_id.startswith(mapped)
            ):
                return selector
        if args.all_instances:
            return "--all"
        if client.config.default_instance:
            return client.config.default_instance
        return item_id

    operation_id = operation_id_from_response(lifecycle_result)
    elapsed = round(time.monotonic() - started, 3)
    receipts = []
    for item_id in target_ids:
        before_item = before_by_id[item_id]
        final_item = final_by_id.get(item_id)
        local_aliases = alias_bindings.get(item_id, [])
        requested_selector = selector_for(before_item)
        selected_alias = (
            requested_selector.casefold()
            if requested_selector.casefold() in client.config.aliases
            else local_aliases[0]
            if local_aliases
            else None
        )
        receipt = {
            "selector": requested_selector,
            "resolved_instance_id": item_id,
            "name": instance_name(before_item),
            "local_alias": selected_alias,
            "local_aliases": local_aliases,
            "before": instance_status(before_item),
            "after": instance_status(final_item) if final_item else "unknown",
            "desired": desired,
            "operation_id": operation_id,
            "elapsed_seconds": elapsed,
            "waited": bool(args.wait),
            "wait_completed": wait_completed,
            "settled": (
                item_id not in pending_ids
                or (
                    wait_completed
                    and final_item is not None
                    and instance_status(final_item) == desired
                )
            ),
            "request_sent": item_id in pending_ids,
            "accelerator_health": (
                instance_summary(final_item or before_item)["accelerator_health"]
            ),
            "accelerator_attachment": "unknown",
        }
        request_result = request_results.get(item_id)
        receipt["request_result"] = request_result
        receipt["request_accepted"] = (
            bool(request_result.get("success"))
            if request_result is not None and "success" in request_result
            else None
        )
        receipt["request_error"] = (
            request_result.get("error") if request_result is not None else None
        )
        if api_action == "start":
            request_sent = item_id in pending_ids
            receipt["start_mode"] = (
                "with_accelerator"
                if request_sent and with_accelerator
                else "without_accelerator"
                if request_sent
                else "not_requested_already_running"
            )
            receipt["with_accelerator_requested"] = (
                with_accelerator if request_sent else None
            )
        if observation_error is not None:
            receipt["observation_error"] = observation_error.as_dict()["error"]
        if api_action in {"start", "reboot"}:
            doctor_selector = selected_alias or "<jupyter-instance-name>"
            receipt["next_step"] = (
                f"jc -i {doctor_selector} doctor --json"
            )
        receipts.append(receipt)

    next_step = None
    if api_action in {"start", "reboot"}:
        steps = list(
            dict.fromkeys(
                receipt["next_step"]
                for receipt in receipts
                if receipt.get("next_step")
            )
        )
        next_step = steps[0] if len(steps) == 1 else steps
    request_failures = [
        {
            "resolved_instance_id": receipt["resolved_instance_id"],
            "error": receipt["request_error"],
        }
        for receipt in receipts
        if receipt["request_sent"] and receipt["request_accepted"] is False
    ]
    report = {
        "operation": api_action,
        "operation_success": not request_failures,
        "desired": desired,
        "waited": bool(args.wait),
        "wait_completed": wait_completed,
        "receipts": receipts,
        "request_failures": request_failures,
        "next_step": next_step,
    }
    if api_action == "start":
        report["start_mode"] = (
            "with_accelerator" if with_accelerator else "without_accelerator"
        )
        report["start_request_sent"] = bool(pending_ids)
        if not with_accelerator:
            report["accelerator_notice"] = (
                "The instance was requested without accelerator resources. "
                "The instance API does not report actual accelerator attachment."
            )
    if not args.as_json:
        for failure in request_failures:
            print(
                "request rejected: "
                f"{failure['resolved_instance_id']}: {failure['error'] or 'unknown error'}",
                file=sys.stderr,
            )
        if observation_error is not None:
            print(
                "post-request status observation failed: "
                f"{observation_error.code}: {observation_error}",
                file=sys.stderr,
            )
        print_instances(final or targets, aliases=client.config.aliases)
        if next_step:
            if isinstance(next_step, list):
                for step in next_step:
                    print(f"next_step: {step}")
            else:
                print(f"next_step: {next_step}")
    return report


def _add_lifecycle_parser(
    sub: argparse._SubParsersAction,
    name: str,
    aliases: list[str],
    help_text: str,
    action: str,
    *,
    allow_no_accelerator: bool = False,
) -> None:
    parser = sub.add_parser(name, aliases=aliases, help=help_text)
    parser.add_argument(
        "selectors", nargs="*", help="local alias, instance id/prefix, or name"
    )
    parser.add_argument(
        "-a", "--all", action="store_true", dest="all_instances"
    )
    parser.add_argument("-w", "--wait", action="store_true")
    parser.add_argument("-t", "--timeout", type=float)
    parser.add_argument("--json", action="store_true", dest="as_json")
    if allow_no_accelerator:
        parser.add_argument(
            "-c",
            "--no-accelerator",
            "--no-gpu",
            "--cpu-only",
            action="store_false",
            dest="with_accelerator",
            help="start without accelerator resources (with_gpu=false)",
        )
        parser.set_defaults(with_accelerator=True)
    parser.set_defaults(action=action)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control Moark compute-container lifecycle"
    )
    parser.add_argument(
        "-e",
        "--env-file",
        default=None,
        help=(
            "credential env file (default: $MOARKCTL_CONFIG or "
            "~/.config/moarkctl/config.env)"
        ),
    )
    parser.add_argument("--version", action="version", version=f"MoarkCTL {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser(
        "init", help="create the private user configuration"
    )
    init_parser.add_argument(
        "--force", action="store_true", help="replace an existing config file"
    )
    init_parser.set_defaults(action="init")

    self_test = sub.add_parser(
        "self-test", aliases=["check"], help="verify token and API discovery"
    )
    self_test.set_defaults(action="self-test")

    listing = sub.add_parser(
        "list", aliases=["ls", "status", "st"], help="list token-owned instances"
    )
    listing.add_argument(
        "selectors", nargs="*", help="optional local alias, id/prefix, or name"
    )
    listing.add_argument("--json", action="store_true", dest="as_json")
    listing.set_defaults(action="list")

    _add_lifecycle_parser(
        sub,
        "start",
        ["on"],
        "start with or without accelerator resources",
        "start",
        allow_no_accelerator=True,
    )
    _add_lifecycle_parser(
        sub, "shutdown", ["off", "stop"], "stop billing compute", "shutdown"
    )
    _add_lifecycle_parser(sub, "reboot", ["re"], "reboot instances", "reboot")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command_label = args.action
    run_started = time.monotonic()
    run_started_at = utc_timestamp()
    current_target: Any = {"kind": "unknown"}
    structured_expected = args.action in {"init", "self-test"} or bool(
        getattr(args, "as_json", False)
    )

    def emit(
        payload: Any,
        *,
        success: bool = True,
        error: dict[str, Any] | None = None,
    ) -> None:
        print(
            json.dumps(
                machine_envelope(
                    command=command_label,
                    target=current_target,
                    started_at=run_started_at,
                    started_monotonic=run_started,
                    success=success,
                    payload=payload,
                    error=error,
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )

    try:
        config_path = resolve_config_path(args.env_file)
        current_target = {
            "kind": "local_config",
            "path": str(config_path.resolve()),
        }
        if args.action == "init":
            created = initialize_config(config_path, force=args.force)
            emit(
                {
                    "config_file": str(created.resolve()),
                    "created": True,
                    "mode": "0600" if os.name == "posix" else "private",
                }
            )
            return 0

        print(f"mc: config={config_path.resolve()}", file=sys.stderr)
        config = config_from_env(config_path)
        client = MoarkClient(config)
        if args.action == "self-test":
            instances = client.instances()
            summaries, alias_errors = summarize_instances(
                instances, config.aliases
            )
            current_target = {
                "kind": "moark_account_instances",
                "instance_count": len(instances),
            }
            emit(
                {
                    "checks": {
                        "api_discovery": "ok",
                        "config": "ok",
                        "local_aliases": "ok" if not alias_errors else "warning",
                    },
                    "config_file": config.config_file,
                    "instance_count": len(instances),
                    "instances": summaries,
                    "aliases": config.aliases,
                    "alias_errors": alias_errors,
                    "ok": True,
                }
            )
            return 0
        if args.action == "list":
            current_target = {
                "kind": "moark_instances",
                "selectors": list(args.selectors),
            }
            report = command_list(client, args)
            if args.as_json:
                emit(report)
            return 0
        if args.action == "start":
            current_target = {
                "kind": "moark_instances",
                "selectors": list(args.selectors),
            }
            report = command_lifecycle(
                client, args, api_action="start", desired="running"
            )
            current_target = {
                "kind": "moark_instances",
                "selectors": list(args.selectors),
                "resolved_instance_ids": [
                    item["resolved_instance_id"] for item in report["receipts"]
                ],
            }
            operation_success = bool(report["operation_success"])
            if args.as_json:
                emit(
                    report,
                    success=operation_success,
                    error=None if operation_success else machine_error(
                        code="lifecycle_request_rejected",
                        phase="api_action",
                        retryable=False,
                        suggested_action=(
                            "Inspect request_failures and current instance status "
                            "before retrying."
                        ),
                        message="one or more start requests were rejected",
                    ),
                )
            return 0 if operation_success else 1
        if args.action == "shutdown":
            current_target = {
                "kind": "moark_instances",
                "selectors": list(args.selectors),
            }
            report = command_lifecycle(
                client, args, api_action="shutdown", desired="stopped"
            )
            current_target = {
                "kind": "moark_instances",
                "selectors": list(args.selectors),
                "resolved_instance_ids": [
                    item["resolved_instance_id"] for item in report["receipts"]
                ],
            }
            operation_success = bool(report["operation_success"])
            if args.as_json:
                emit(
                    report,
                    success=operation_success,
                    error=None if operation_success else machine_error(
                        code="lifecycle_request_rejected",
                        phase="api_action",
                        retryable=False,
                        suggested_action=(
                            "Inspect request_failures and current instance status "
                            "before retrying."
                        ),
                        message="one or more shutdown requests were rejected",
                    ),
                )
            return 0 if operation_success else 1
        if args.action == "reboot":
            current_target = {
                "kind": "moark_instances",
                "selectors": list(args.selectors),
            }
            report = command_lifecycle(
                client, args, api_action="reboot", desired="running"
            )
            current_target = {
                "kind": "moark_instances",
                "selectors": list(args.selectors),
                "resolved_instance_ids": [
                    item["resolved_instance_id"] for item in report["receipts"]
                ],
            }
            operation_success = bool(report["operation_success"])
            if args.as_json:
                emit(
                    report,
                    success=operation_success,
                    error=None if operation_success else machine_error(
                        code="lifecycle_request_rejected",
                        phase="api_action",
                        retryable=False,
                        suggested_action=(
                            "Inspect request_failures and current instance status "
                            "before retrying."
                        ),
                        message="one or more reboot requests were rejected",
                    ),
                )
            return 0 if operation_success else 1
        raise MoarkCtlError(f"unsupported command: {args.action}")
    except MoarkCtlError as exc:
        error = machine_error(
            code=exc.code,
            phase=exc.phase or command_label,
            retryable=exc.retryable,
            suggested_action=exc.suggested_action or (
                "Inspect the error and active configuration before retrying."
            ),
            message=str(exc),
            api_host=exc.api_host,
        )
        if structured_expected:
            emit({}, success=False, error=error)
        else:
            detail = f"mc: {exc.code}: {exc}"
            if exc.api_host:
                detail += f"; api_host={exc.api_host}"
            if exc.suggested_action:
                detail += f"; suggested_action={exc.suggested_action}"
            print(detail, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
