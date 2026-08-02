#!/usr/bin/env python3
"""A small lifecycle controller for Moark compute containers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "https://api.moark.com/v1"
DEFAULT_ENV_FILE = ".moarkctl.env"
DEFAULT_HTTP_TIMEOUT = 60.0
DEFAULT_POLL_INTERVAL = 8.0
DEFAULT_POLL_TIMEOUT = 600.0
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


class MoarkCtlError(RuntimeError):
    """Expected configuration, selection, API, or lifecycle failure."""


@dataclass(frozen=True)
class MoarkConfig:
    token: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    http_timeout: float = DEFAULT_HTTP_TIMEOUT
    poll_interval: float = DEFAULT_POLL_INTERVAL
    poll_timeout: float = DEFAULT_POLL_TIMEOUT
    default_instance: str = ""


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


def config_from_env(env_file: str = DEFAULT_ENV_FILE) -> MoarkConfig:
    load_env_file(env_file)
    token = os.environ.get("MOARK_TOKEN", "")
    if not token:
        raise MoarkCtlError("MOARK_TOKEN is required")
    base_url = os.environ.get("MOARK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MoarkCtlError("MOARK_BASE_URL must be an absolute http(s) URL")
    return MoarkConfig(
        token=token,
        base_url=base_url,
        http_timeout=_positive_float("MOARK_HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT),
        poll_interval=_positive_float(
            "MOARK_POLL_INTERVAL", DEFAULT_POLL_INTERVAL
        ),
        poll_timeout=_positive_float("MOARK_POLL_TIMEOUT", DEFAULT_POLL_TIMEOUT),
        default_instance=os.environ.get("MOARK_DEFAULT_INSTANCE", ""),
    )


def scrub_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if key.casefold() in SECRET_KEYS else scrub_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_secrets(item) for item in value]
    return value


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
            "User-Agent": "MoarkCTL/0.2",
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
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.http_timeout
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = self._redact(
                exc.read(4096).decode("utf-8", errors="replace")
            )
            raise MoarkCtlError(f"Moark HTTP {exc.code}: {detail[:1000]}") from exc
        except urllib.error.URLError as exc:
            raise MoarkCtlError(
                f"Moark network error: {self._redact(exc.reason)}"
            ) from exc
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise MoarkCtlError("Moark returned non-JSON data") from exc

    def instances(self, ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        joined_ids = ",".join(ids or [])
        result = self.request(
            "GET", "/compute/instances", query={"ids": joined_ids or None}
        )
        if not isinstance(result, list):
            raise MoarkCtlError(
                f"unexpected instance list response: {scrub_secrets(result)!r}"
            )
        return [item for item in result if isinstance(item, dict)]

    def lifecycle(self, action: str, ids: Iterable[str]) -> None:
        target_ids = list(ids)
        if not target_ids:
            return
        query: dict[str, Any] = {"ids": ",".join(target_ids)}
        if action == "start":
            query["with_gpu"] = "true"
        self.request("POST", f"/compute/instances/{action}", query=query)


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


def instance_summary(instance: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "id": instance_id(instance),
        "name": instance_name(instance),
        "status": instance_status(instance),
        "accelerator": accelerator,
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
    for selector in requested:
        folded = selector.casefold()
        exact = [
            item
            for item in instances
            if instance_id(item) == selector
            or instance_name(item).casefold() == folded
        ]
        matches = exact
        if not matches:
            matches = [
                item for item in instances if instance_id(item).startswith(selector)
            ]
        if not matches:
            raise MoarkCtlError(f"no compute instance matches {selector!r}")
        if len(matches) > 1:
            matched_ids = ", ".join(instance_id(item) for item in matches)
            raise MoarkCtlError(
                f"compute instance selector {selector!r} is ambiguous: {matched_ids}"
            )
        item = matches[0]
        item_id = instance_id(item)
        if item_id not in selected_ids:
            selected.append(item)
            selected_ids.add(item_id)
    return selected


def print_instances(instances: list[dict[str, Any]], *, as_json: bool) -> None:
    summaries = [instance_summary(item) for item in instances]
    if as_json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if not summaries:
        print("No instances.")
        return
    print("id\tname\tstatus\taccelerator\tbilling")
    for item in summaries:
        print(
            "\t".join(
                str(item[key] or "-")
                for key in ("id", "name", "status", "accelerator", "billing")
            )
        )


def select_for_action(
    client: MoarkClient, args: argparse.Namespace
) -> list[dict[str, Any]]:
    return resolve_instances(
        client.instances(),
        args.selectors,
        all_instances=args.all_instances,
        default_selector=client.config.default_instance,
    )


def wait_for_status(
    client: MoarkClient,
    ids: Iterable[str],
    desired: str,
    *,
    timeout: float,
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
            print(" | ".join(f"{item_id}={status}" for item_id, status in snapshot))
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


def command_list(client: MoarkClient, args: argparse.Namespace) -> None:
    instances = client.instances()
    if args.selectors:
        instances = resolve_instances(
            instances,
            args.selectors,
            all_instances=False,
        )
    print_instances(instances, as_json=args.as_json)


def command_lifecycle(
    client: MoarkClient,
    args: argparse.Namespace,
    *,
    api_action: str,
    desired: str,
) -> None:
    targets = select_for_action(client, args)
    pending = targets
    if api_action != "reboot":
        pending = [item for item in targets if instance_status(item) != desired]
    target_ids = [instance_id(item) for item in targets]
    pending_ids = [instance_id(item) for item in pending]
    if pending_ids:
        client.lifecycle(api_action, pending_ids)
        print(f"{api_action} requested: {', '.join(pending_ids)}")
    else:
        print(f"already {desired}: {', '.join(target_ids)}")
    if args.wait:
        final = wait_for_status(
            client,
            target_ids,
            desired,
            timeout=args.timeout or client.config.poll_timeout,
        )
        print_instances(final, as_json=args.as_json)


def _add_lifecycle_parser(
    sub: argparse._SubParsersAction,
    name: str,
    aliases: list[str],
    help_text: str,
    action: str,
) -> None:
    parser = sub.add_parser(name, aliases=aliases, help=help_text)
    parser.add_argument("selectors", nargs="*", help="instance id, id prefix, or name")
    parser.add_argument(
        "-a", "--all", action="store_true", dest="all_instances"
    )
    parser.add_argument("-w", "--wait", action="store_true")
    parser.add_argument("-t", "--timeout", type=float)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.set_defaults(action=action)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control Moark compute-container lifecycle"
    )
    parser.add_argument(
        "-e",
        "--env-file",
        default=DEFAULT_ENV_FILE,
        help=f"credential env file (default: {DEFAULT_ENV_FILE})",
    )
    parser.add_argument("--version", action="version", version="MoarkCTL 0.2.0")
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser(
        "list", aliases=["ls", "status", "st"], help="list token-owned instances"
    )
    listing.add_argument("selectors", nargs="*", help="optional id, id prefix, or name")
    listing.add_argument("--json", action="store_true", dest="as_json")
    listing.set_defaults(action="list")

    _add_lifecycle_parser(sub, "start", ["on"], "start with accelerator", "start")
    _add_lifecycle_parser(
        sub, "shutdown", ["off", "stop"], "stop billing compute", "shutdown"
    )
    _add_lifecycle_parser(sub, "reboot", ["re"], "reboot instances", "reboot")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = config_from_env(args.env_file)
        client = MoarkClient(config)
        if args.action == "list":
            command_list(client, args)
            return 0
        if args.action == "start":
            command_lifecycle(
                client, args, api_action="start", desired="running"
            )
            return 0
        if args.action == "shutdown":
            command_lifecycle(
                client, args, api_action="shutdown", desired="stopped"
            )
            return 0
        if args.action == "reboot":
            command_lifecycle(
                client, args, api_action="reboot", desired="running"
            )
            return 0
        raise MoarkCtlError(f"unsupported command: {args.action}")
    except MoarkCtlError as exc:
        print(f"mc: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
