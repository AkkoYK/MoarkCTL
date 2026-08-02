from pathlib import Path
import contextlib
import io
import os
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from moarkctl.cli import (  # noqa: E402
    MoarkClient,
    MoarkConfig,
    MoarkCtlError,
    build_parser,
    command_lifecycle,
    config_from_env,
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

    def lifecycle(self, action, ids):
        self.calls.append((action, list(ids)))


class MoarkConfigTests(unittest.TestCase):
    def test_config_repr_hides_token(self):
        config = MoarkConfig(token="top-secret")

        self.assertNotIn("top-secret", repr(config))

    def test_env_file_loads_token_and_default_selector(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".moarkctl.env"
            env_file.write_text(
                "MOARK_TOKEN=secret\nMOARK_DEFAULT_INSTANCE=ascend-lab\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            keys = ["MOARK_TOKEN", "MOARK_DEFAULT_INSTANCE"]
            previous = {key: os.environ.pop(key, None) for key in keys}
            try:
                config = config_from_env(str(env_file))
                self.assertEqual(config.token, "secret")
                self.assertEqual(config.default_instance, "ascend-lab")
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

    def test_ambiguous_prefix_is_rejected(self):
        instances = INSTANCES + [{"id": "ascend-999999", "status": "stopped"}]

        with self.assertRaisesRegex(MoarkCtlError, "ambiguous"):
            resolve_instances(instances, ["ascend-"], all_instances=False)


class MoarkCliTests(unittest.TestCase):
    def test_short_lifecycle_aliases(self):
        start = build_parser().parse_args(["on", "ascend-lab", "-w"])
        stop = build_parser().parse_args(["off", "--all", "-w"])
        reboot = build_parser().parse_args(["re", "nvidia-9"])

        self.assertEqual(start.action, "start")
        self.assertEqual(start.selectors, ["ascend-lab"])
        self.assertTrue(start.wait)
        self.assertEqual(stop.action, "shutdown")
        self.assertTrue(stop.all_instances)
        self.assertEqual(reboot.action, "reboot")

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
            [("start", ["ascend-123456", "nvidia-987654"])],
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
