from pathlib import Path
import contextlib
import io
import json
import os
import socket
import ssl
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from moarkctl.cli import (  # noqa: E402
    MoarkClient,
    MoarkConfig,
    MoarkCtlError,
    build_parser,
    classify_network_error,
    command_lifecycle,
    config_from_env,
    initialize_config,
    instance_summary,
    main,
    print_instances,
    resolve_instances,
    scrub_secrets,
    wait_for_status,
)


INSTANCES = [
    {
        "id": "ascend-123456",
        "name": "ascend-lab",
        "status": "stopped",
        "gpu_model": "Ascend 910B",
        "gpu_num": 1,
    },
    {
        "id": "nvidia-987654",
        "name": "training",
        "status": "running",
        "gpu_model": "RTX PRO 6000",
        "gpu_num": 1,
    },
]


class FakeLifecycleClient:
    def __init__(self, snapshots=None):
        self.config = MoarkConfig(
            token="hidden",
            poll_interval=0.001,
            poll_timeout=1,
        )
        self.snapshots = list(snapshots or [INSTANCES])
        self.calls = []

    def instances(self, ids=None):
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]

    def lifecycle(self, action, ids, *, with_accelerator=True):
        self.calls.append((action, list(ids), with_accelerator))
        return {"operation_id": "operation-123"}


class MoarkConfigTests(unittest.TestCase):
    def test_init_creates_fixed_private_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / ".config" / "moarkctl" / "config.env"

            created = initialize_config(config_path, force=False)

            self.assertEqual(created, config_path)
            self.assertIn("MOARK_TOKEN=", created.read_text(encoding="utf-8"))
            if os.name == "posix":
                self.assertEqual(created.stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(MoarkCtlError, "already exists"):
                initialize_config(config_path, force=False)

    def test_config_repr_hides_token(self):
        config = MoarkConfig(token="top-secret")

        self.assertNotIn("top-secret", repr(config))

    def test_env_file_loads_token_and_default_selector(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".moarkctl.env"
            env_file.write_text(
                "MOARK_TOKEN=secret\n"
                "MOARK_DEFAULT_INSTANCE=npu-910b\n"
                "MOARK_INSTANCE_NPU_910B=ascend-123456\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            keys = [
                "MOARK_TOKEN",
                "MOARK_DEFAULT_INSTANCE",
                "MOARK_INSTANCE_NPU_910B",
            ]
            previous = {key: os.environ.pop(key, None) for key in keys}
            try:
                config = config_from_env(str(env_file))
                self.assertEqual(config.token, "secret")
                self.assertEqual(config.default_instance, "npu-910b")
                self.assertEqual(
                    config.aliases, {"npu-910b": "ascend-123456"}
                )
            finally:
                for key in keys:
                    os.environ.pop(key, None)
                    if previous[key] is not None:
                        os.environ[key] = previous[key]

    def test_env_file_requires_private_permissions(self):
        if os.name != "posix":
            self.skipTest("POSIX permission check")
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".moarkctl.env"
            env_file.write_text("MOARK_TOKEN=secret\n", encoding="utf-8")
            env_file.chmod(0o644)

            with self.assertRaisesRegex(MoarkCtlError, "chmod 600"):
                config_from_env(str(env_file))

    def test_placeholder_token_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "config.env"
            env_file.write_text("MOARK_TOKEN=replace-me\n", encoding="utf-8")
            env_file.chmod(0o600)
            previous = os.environ.pop("MOARK_TOKEN", None)
            try:
                with self.assertRaisesRegex(MoarkCtlError, "placeholder"):
                    config_from_env(env_file)
            finally:
                os.environ.pop("MOARK_TOKEN", None)
                if previous is not None:
                    os.environ["MOARK_TOKEN"] = previous


class MoarkSelectionTests(unittest.TestCase):
    def test_exact_name_and_id_prefix_select_multiple_instances(self):
        selected = resolve_instances(
            INSTANCES,
            ["ascend-lab", "nvidia-9"],
            all_instances=False,
        )

        self.assertEqual(
            [item["id"] for item in selected],
            ["ascend-123456", "nvidia-987654"],
        )

    def test_all_selects_every_token_owned_instance(self):
        selected = resolve_instances(INSTANCES, [], all_instances=True)

        self.assertEqual(selected, INSTANCES)

    def test_no_selector_is_rejected_for_multiple_instances(self):
        with self.assertRaisesRegex(MoarkCtlError, "multiple compute instances"):
            resolve_instances(INSTANCES, [], all_instances=False)

    def test_optional_default_does_not_require_an_instance_id(self):
        selected = resolve_instances(
            INSTANCES,
            [],
            all_instances=False,
            default_selector="ascend-lab",
        )

        self.assertEqual(selected[0]["id"], "ascend-123456")

    def test_local_alias_resolves_an_instance_with_an_empty_api_name(self):
        unnamed = [{"id": "NHRNUEKVXXGAEI1U", "name": "", "status": "stopped"}]

        selected = resolve_instances(
            unnamed,
            ["npu-910b"],
            all_instances=False,
            aliases={"npu-910b": "NHRNUEKVXXGAEI1U"},
        )

        self.assertEqual(selected, unnamed)

    def test_ambiguous_prefix_is_rejected(self):
        instances = INSTANCES + [{"id": "ascend-999999", "status": "stopped"}]

        with self.assertRaisesRegex(MoarkCtlError, "ambiguous"):
            resolve_instances(instances, ["ascend-"], all_instances=False)


class MoarkCliTests(unittest.TestCase):
    def test_network_classifier_covers_timeout_tls_and_generic_failures(self):
        self.assertEqual(
            classify_network_error(socket.timeout("timed out"))[0],
            "connect_timeout",
        )
        self.assertEqual(
            classify_network_error(ssl.SSLError("certificate verify failed"))[0],
            "tls_error",
        )
        self.assertEqual(
            classify_network_error(OSError("connection refused"))[0],
            "network_error",
        )

    @mock.patch("moarkctl.cli.MoarkClient.instances")
    def test_json_command_returns_structured_error(self, instances):
        instances.side_effect = MoarkCtlError(
            "cannot resolve host",
            code="dns_error",
            api_host="api.moark.com",
            suggested_action="Check DNS.",
            phase="api_request",
        )
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "config.env"
            env_file.write_text("MOARK_TOKEN=secret\n", encoding="utf-8")
            env_file.chmod(0o600)
            previous = os.environ.pop("MOARK_TOKEN", None)
            stdout = io.StringIO()
            try:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                    exit_code = main(
                        ["--env-file", str(env_file), "ls", "--json"]
                    )
            finally:
                os.environ.pop("MOARK_TOKEN", None)
                if previous is not None:
                    os.environ["MOARK_TOKEN"] = previous

        report = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["schema_version"], "1.0")
        self.assertEqual(report["command"], "list")
        self.assertFalse(report["success"])
        self.assertEqual(report["error"]["code"], "dns_error")
        self.assertEqual(report["error"]["phase"], "api_request")
        self.assertTrue(report["error"]["retryable"])
        self.assertEqual(report["error"]["suggested_action"], "Check DNS.")
        self.assertEqual(report["error"]["api_host"], "api.moark.com")

    @mock.patch("moarkctl.cli.urllib.request.urlopen")
    def test_dns_error_is_structured_and_exposes_only_api_host(self, urlopen):
        urlopen.side_effect = urllib.error.URLError(
            socket.gaierror(8, "nodename nor servname provided")
        )
        client = MoarkClient(
            MoarkConfig(
                token="secret",
                base_url="https://api.moark.com/v1",
            )
        )

        with self.assertRaises(MoarkCtlError) as caught:
            client.instances()

        self.assertEqual(caught.exception.code, "dns_error")
        self.assertEqual(caught.exception.phase, "api_request")
        self.assertEqual(caught.exception.api_host, "api.moark.com")
        self.assertIn("DNS", caught.exception.suggested_action)
        self.assertNotIn("secret", json.dumps(caught.exception.as_dict()))

    @mock.patch("moarkctl.cli.urllib.request.urlopen")
    def test_http_401_is_classified_as_auth_error(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError(
            "https://api.moark.com/v1/compute/instances",
            401,
            "Unauthorized",
            None,
            io.BytesIO(b"token expired"),
        )
        client = MoarkClient(MoarkConfig(token="secret"))

        with self.assertRaises(MoarkCtlError) as caught:
            client.instances()

        self.assertEqual(caught.exception.code, "auth_error")
        self.assertIn("Access Tokens", caught.exception.suggested_action)

    def test_instance_summary_separates_platform_and_accelerator_health(self):
        summary = instance_summary(
            {
                **INSTANCES[1],
                "zone": "ascend-zone-a",
                "system_disk_rate": 12.5,
                "data_disk_rate": 47.25,
                "created_at": 1774601072000,
                "updated_at": 1774604672000,
                "device_health": "healthy",
                "maintenance_status": "normal",
                "alerts": [],
                "node": {"telemetry": {"health": "ok", "token": "hidden"}},
            }
        )
        unknown = instance_summary(INSTANCES[0])

        self.assertEqual(summary["platform_status"], "running")
        self.assertEqual(summary["status_detail"]["category"], "active")
        self.assertTrue(summary["status_detail"]["known"])
        self.assertEqual(summary["zone"], "ascend-zone-a")
        self.assertEqual(summary["disk_usage"]["system_disk_rate"], 12.5)
        self.assertEqual(summary["disk_usage"]["data_disk_rate"], 47.25)
        self.assertEqual(
            summary["lifecycle_timestamps"]["created_at"]["raw"],
            1774601072000,
        )
        self.assertTrue(
            summary["lifecycle_timestamps"]["updated_at"]["iso"].endswith("Z")
        )
        self.assertEqual(summary["accelerator_attachment"], "unknown")
        self.assertEqual(summary["accelerator_health"], "healthy")
        self.assertEqual(summary["accelerator_health_source"], "device_health")
        self.assertEqual(summary["health_fields"]["maintenance_status"], "normal")
        self.assertEqual(
            summary["health_fields"]["node"]["telemetry"]["health"], "ok"
        )
        self.assertNotIn("token", json.dumps(summary["health_fields"]))
        self.assertEqual(unknown["accelerator_health"], "unknown")

    def test_human_status_includes_provider_state_details(self):
        instance = {
            **INSTANCES[0],
            "status": "restarting",
            "zone": "npu-zone",
            "system_disk_rate": 18,
            "data_disk_rate": 33,
            "updated_at": 1774604672000,
        }
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            print_instances([instance], aliases={"npu-910b": "ascend-123456"})

        output = stdout.getvalue()
        self.assertIn("category", output)
        self.assertIn("zone", output)
        self.assertIn("disk_rate(system/data)", output)
        self.assertIn("restarting/重启中", output)
        self.assertIn("transitioning", output)
        self.assertIn("npu-zone", output)
        self.assertIn("18/33", output)

    def test_json_lifecycle_returns_stable_receipts_without_progress_noise(self):
        stopped = [INSTANCES[0]]
        starting = [{**INSTANCES[0], "status": "starting"}]
        client = FakeLifecycleClient([stopped, starting])
        args = build_parser().parse_args(["on", "ascend-lab", "--json"])
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            report = command_lifecycle(
                client,
                args,
                api_action="start",
                desired="running",
            )

        self.assertEqual(stdout.getvalue(), "")
        receipt = report["receipts"][0]
        self.assertEqual(receipt["selector"], "ascend-lab")
        self.assertEqual(receipt["resolved_instance_id"], "ascend-123456")
        self.assertEqual(receipt["before"], "stopped")
        self.assertEqual(receipt["after"], "starting")
        self.assertEqual(receipt["operation_id"], "operation-123")
        self.assertIsInstance(receipt["elapsed_seconds"], float)
        self.assertEqual(
            report["next_step"],
            "jc -i <jupyter-instance-name> doctor --json",
        )

    def test_no_accelerator_start_is_explicit_in_request_and_receipt(self):
        stopped = [INSTANCES[0]]
        pending = [{**INSTANCES[0], "status": "pending"}]
        client = FakeLifecycleClient([stopped, pending])
        args = build_parser().parse_args(
            ["on", "ascend-lab", "-c", "--json"]
        )

        report = command_lifecycle(
            client,
            args,
            api_action="start",
            desired="running",
        )

        receipt = report["receipts"][0]
        self.assertFalse(args.with_accelerator)
        self.assertEqual(
            client.calls, [("start", ["ascend-123456"], False)]
        )
        self.assertEqual(report["start_mode"], "without_accelerator")
        self.assertTrue(report["start_request_sent"])
        self.assertIn("does not report", report["accelerator_notice"])
        self.assertEqual(receipt["start_mode"], "without_accelerator")
        self.assertFalse(receipt["with_accelerator_requested"])
        self.assertTrue(receipt["request_sent"])
        self.assertEqual(receipt["accelerator_attachment"], "unknown")

    def test_start_mode_does_not_claim_a_request_for_an_already_running_instance(self):
        running = [{**INSTANCES[0], "status": "running"}]
        client = FakeLifecycleClient([running])
        args = build_parser().parse_args(
            ["on", "ascend-lab", "--no-gpu", "--json"]
        )

        report = command_lifecycle(
            client,
            args,
            api_action="start",
            desired="running",
        )

        receipt = report["receipts"][0]
        self.assertEqual(client.calls, [])
        self.assertFalse(report["start_request_sent"])
        self.assertFalse(receipt["request_sent"])
        self.assertEqual(
            receipt["start_mode"], "not_requested_already_running"
        )
        self.assertIsNone(receipt["with_accelerator_requested"])

    def test_waiting_json_lifecycle_suppresses_poll_lines(self):
        stopped = [INSTANCES[0]]
        starting = [{**INSTANCES[0], "status": "starting"}]
        running = [{**INSTANCES[0], "status": "running"}]
        client = FakeLifecycleClient([stopped, starting, running])
        args = build_parser().parse_args(
            ["on", "ascend-lab", "--wait", "--json"]
        )
        stdout = io.StringIO()

        with mock.patch("moarkctl.cli.time.sleep"), contextlib.redirect_stdout(stdout):
            report = command_lifecycle(
                client,
                args,
                api_action="start",
                desired="running",
            )

        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(report["receipts"][0]["after"], "running")
        self.assertTrue(report["receipts"][0]["settled"])

    @mock.patch("moarkctl.cli.MoarkClient.lifecycle")
    @mock.patch("moarkctl.cli.MoarkClient.instances")
    def test_main_json_lifecycle_uses_local_alias_and_machine_envelope(
        self, instances, lifecycle
    ):
        stopped = {
            "id": "NHRNUEKVXXGAEI1U",
            "name": "",
            "status": "stopped",
            "gpu_model": "Ascend 910B",
            "gpu_num": 1,
        }
        instances.side_effect = [[stopped], [{**stopped, "status": "starting"}]]
        lifecycle.return_value = {"operation_id": "operation-456"}
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "config.env"
            env_file.write_text(
                "MOARK_TOKEN=secret\n"
                "MOARK_INSTANCE_NPU_910B=NHRNUEKVXXGAEI1U\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            keys = ["MOARK_TOKEN", "MOARK_INSTANCE_NPU_910B"]
            previous = {key: os.environ.pop(key, None) for key in keys}
            stdout = io.StringIO()
            try:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                    exit_code = main(
                        [
                            "--env-file",
                            str(env_file),
                            "on",
                            "npu-910b",
                            "--json",
                        ]
                    )
            finally:
                for key in keys:
                    os.environ.pop(key, None)
                    if previous[key] is not None:
                        os.environ[key] = previous[key]

        report = json.loads(stdout.getvalue())
        receipt = report["receipts"][0]
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["schema_version"], "1.0")
        self.assertEqual(report["command"], "start")
        self.assertTrue(report["success"])
        self.assertIsNone(report["error"]["code"])
        self.assertEqual(report["target"]["resolved_instance_ids"], [stopped["id"]])
        self.assertEqual(receipt["selector"], "npu-910b")
        self.assertEqual(receipt["local_alias"], "npu-910b")
        self.assertEqual(
            receipt["next_step"], "jc -i npu-910b doctor --json"
        )
        self.assertEqual(report["next_step"], "jc -i npu-910b doctor --json")
        lifecycle.assert_called_once_with(
            "start", [stopped["id"]], with_accelerator=True
        )

    @mock.patch("moarkctl.cli.MoarkClient.lifecycle")
    @mock.patch("moarkctl.cli.MoarkClient.instances")
    @mock.patch("moarkctl.cli.config_from_env")
    def test_main_reports_per_instance_lifecycle_rejection_without_false_settlement(
        self, config, instances, lifecycle
    ):
        stopped = INSTANCES[0]
        config.return_value = MoarkConfig(token="hidden")
        instances.side_effect = [[stopped], [stopped]]
        lifecycle.return_value = [
            {
                "id": stopped["id"],
                "success": False,
                "error": "accelerator inventory unavailable",
            }
        ]
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            exit_code = main(
                ["--env-file", "/tmp/moarkctl.env", "on", "ascend-lab", "-w", "--json"]
            )

        report = json.loads(stdout.getvalue())
        receipt = report["receipts"][0]
        self.assertEqual(exit_code, 1)
        self.assertFalse(report["success"])
        self.assertEqual(report["error"]["code"], "lifecycle_request_rejected")
        self.assertFalse(report["operation_success"])
        self.assertFalse(report["wait_completed"])
        self.assertEqual(len(report["request_failures"]), 1)
        self.assertFalse(receipt["request_accepted"])
        self.assertFalse(receipt["settled"])
        self.assertEqual(
            receipt["request_error"], "accelerator inventory unavailable"
        )

    @mock.patch("moarkctl.cli.MoarkClient.instances", return_value=INSTANCES)
    def test_self_test_discovers_without_lifecycle_mutation(self, instances):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "config.env"
            env_file.write_text("MOARK_TOKEN=secret\n", encoding="utf-8")
            env_file.chmod(0o600)
            previous = os.environ.pop("MOARK_TOKEN", None)
            stdout = io.StringIO()
            stderr = io.StringIO()
            try:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    exit_code = main(["--env-file", str(env_file), "self-test"])
            finally:
                os.environ.pop("MOARK_TOKEN", None)
                if previous is not None:
                    os.environ["MOARK_TOKEN"] = previous

        report = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["schema_version"], "1.0")
        self.assertEqual(report["command"], "self-test")
        self.assertTrue(report["success"])
        self.assertTrue(report["ok"])
        self.assertEqual(report["instance_count"], 2)
        self.assertIn(str(env_file.resolve()), stderr.getvalue())
        instances.assert_called_once_with()

    def test_short_lifecycle_aliases(self):
        start = build_parser().parse_args(["on", "ascend-lab", "-w"])
        cpu_start = build_parser().parse_args(
            ["on", "ascend-lab", "-c", "-w", "--json"]
        )
        stop = build_parser().parse_args(["off", "--all", "-w"])
        reboot = build_parser().parse_args(["re", "nvidia-9"])

        self.assertEqual(start.action, "start")
        self.assertEqual(start.selectors, ["ascend-lab"])
        self.assertTrue(start.wait)
        self.assertTrue(start.with_accelerator)
        self.assertFalse(cpu_start.with_accelerator)
        self.assertTrue(cpu_start.as_json)
        self.assertEqual(stop.action, "shutdown")
        self.assertTrue(stop.all_instances)
        self.assertEqual(reboot.action, "reboot")

    def test_client_start_sends_official_with_gpu_query_parameter(self):
        client = MoarkClient(MoarkConfig(token="secret"))

        with mock.patch.object(client, "request", return_value=[]) as request:
            client.lifecycle(
                "start", ["instance-1"], with_accelerator=False
            )

        request.assert_called_once_with(
            "POST",
            "/compute/instances/start",
            query={"ids": "instance-1", "with_gpu": "false"},
        )

    def test_secret_scrubber_covers_nested_values(self):
        value = {
            "id": "safe",
            "ssh_props": {"password": "hidden"},
            "access_token": "also-hidden",
        }

        scrubbed = scrub_secrets(value)

        self.assertEqual(scrubbed["id"], "safe")
        self.assertEqual(scrubbed["ssh_props"]["password"], "***")
        self.assertEqual(scrubbed["access_token"], "***")

    def test_multi_instance_action_sends_every_selected_id(self):
        stopped = [INSTANCES[0], {**INSTANCES[1], "status": "stopped"}]
        client = FakeLifecycleClient([stopped])
        args = build_parser().parse_args(["on", "ascend-lab", "training"])

        with contextlib.redirect_stdout(io.StringIO()):
            command_lifecycle(
                client,
                args,
                api_action="start",
                desired="running",
            )

        self.assertEqual(
            client.calls,
            [("start", ["ascend-123456", "nvidia-987654"], True)],
        )

    def test_wait_checks_all_selected_instances(self):
        first = [
            {**INSTANCES[0], "status": "running"},
            {**INSTANCES[1], "status": "starting"},
        ]
        second = [
            {**INSTANCES[0], "status": "running"},
            {**INSTANCES[1], "status": "running"},
        ]
        client = FakeLifecycleClient([first, second])

        with contextlib.redirect_stdout(io.StringIO()):
            final = wait_for_status(
                client,
                ["ascend-123456", "nvidia-987654"],
                "running",
                timeout=1,
            )

        self.assertEqual([item["status"] for item in final], ["running", "running"])
        self.assertEqual(client.snapshots, [second])

    def test_client_error_redaction_hides_plain_and_encoded_token(self):
        client = MoarkClient(MoarkConfig(token="token with spaces"))

        redacted = client._redact("token with spaces token%20with%20spaces")

        self.assertNotIn("token with spaces", redacted)
        self.assertNotIn("token%20with%20spaces", redacted)


if __name__ == "__main__":
    unittest.main()
