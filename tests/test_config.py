import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from zebra_print_bridge import __main__ as cli
from zebra_print_bridge.config import DEFAULTS, ConfigError, load_config

ROOT = Path(__file__).resolve().parent.parent


class LoadConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, content) -> str:
        path = Path(self.tmp.name, "config.json")
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return str(path)

    def load(self, content):
        return load_config(self.write(content))

    def test_missing_file_gives_defaults(self):
        config = load_config(Path(self.tmp.name, "absent.json"))
        self.assertEqual({k: config[k] for k in DEFAULTS}, DEFAULTS)
        self.assertEqual(config["_warnings"], [])

    def test_example_config_is_valid(self):
        config = load_config(ROOT / "config.example.json")
        self.assertEqual(config["_warnings"], [])

    def test_valid_values(self):
        config = self.load(
            {
                "port": 9101,
                "read_timeout": 3,
                "history_max_mb": 0.5,
                "labelary_url": None,
                "network_printers": [{"host": "10.0.0.5", "port": "6101", "name": "Dock"}],
                "overrides": {"usb:X": {"name": "Front desk"}},
                "usb": {"enabled": False},
                "log_level": "debug",
            }
        )
        self.assertEqual(config["port"], 9101)
        self.assertEqual(config["_warnings"], [])

    def test_unknown_keys_warn_with_a_suggestion(self):
        config = self.load({"allowed_orgins": ["https://a.example"], "network_printers": [{"host": "h", "nmae": "x"}]})
        self.assertEqual(len(config["_warnings"]), 2)
        self.assertIn("'allowed_orgins' is ignored (did you mean 'allowed_origins'?)", config["_warnings"][0])
        self.assertIn("network_printers[0].'nmae'", config["_warnings"][1])
        self.assertIn("config.json", config["_warnings"][0])

    def test_wrong_types_are_errors(self):
        cases = {
            "port": ("9100", "port must be a whole number"),
            "usb": (True, "usb must be an object"),
            "allowed_origins": ("https://a.example", "allowed_origins must be a list"),
            "origin_approval": ("yes", "origin_approval must be true or false"),
            "read_timeout": (-1, "read_timeout must be a number"),
            "default_printer": (5, "default_printer must be text or null"),
        }
        for key, (value, message) in cases.items():
            with self.subTest(key=key), self.assertRaises(ConfigError) as ctx:
                self.load({key: value})
            self.assertIn(message, str(ctx.exception))

    def test_detailed_errors(self):
        cases = [
            ({"network_printers": [{"port": 9100}]}, "network_printers[0].host is required"),
            ({"network_printers": [{"host": "h", "port": 70000}]}, "network_printers[0].port must be between"),
            ({"network_printers": ["10.0.0.5"]}, "network_printers[0] must be an object"),
            ({"overrides": {"usb:X": "Dock"}}, "overrides['usb:X'] must be an object"),
            ({"encoding": "utf-9"}, "encoding 'utf-9' is not a known text encoding"),
            ({"cups_queues": [1]}, "cups_queues must be a list of texts"),
            ({"port": 70000}, "port must be between 1 and 65535"),
        ]
        for content, message in cases:
            with self.subTest(content=content), self.assertRaises(ConfigError) as ctx:
                self.load(content)
            self.assertIn(message, str(ctx.exception))

    def test_every_error_is_reported_at_once(self):
        with self.assertRaises(ConfigError) as ctx:
            self.load({"port": "x", "usb": True, "network_printers": [{"name": "Dock"}], "encoding": "utf-9"})
        for message in ("port must be", "usb must be", "network_printers[0].host is required", "encoding 'utf-9'"):
            self.assertIn(message, str(ctx.exception))

    def test_invalid_json_names_the_line(self):
        with self.assertRaises(ConfigError) as ctx:
            self.load('{\n  "port": 9100,\n}')
        self.assertIn("invalid JSON at line 2", str(ctx.exception))

    def test_not_an_object(self):
        with self.assertRaises(ConfigError):
            self.load([1, 2])

    def test_service_exits_with_code_2_and_a_readable_message(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = cli.main(["--config", self.write({"port": "x"}), "--list"])
        self.assertEqual(code, 2)
        self.assertIn("Invalid configuration", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


class OrganizationConfigTest(unittest.TestCase):
    """/etc/zebra-print-bridge/config.json for every user, ~/.config/... on top."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.system = Path(tmp.name, "etc.json")
        self.user = Path(tmp.name, "user.json")

    def load(self, system=None, user=None):
        for path, content in ((self.system, system), (self.user, user)):
            if content is not None:
                path.write_text(json.dumps(content), encoding="utf-8")
        return load_config(self.user, system_path=self.system)

    def test_organization_file_alone(self):
        config = self.load(system={"allowed_origins": ["https://*.example.com"], "read_timeout": 4})
        self.assertEqual(config["allowed_origins"], ["https://*.example.com"])
        self.assertEqual(config["read_timeout"], 4)
        self.assertEqual(config["_config_files"], [str(self.system)])
        self.assertEqual(config["_config_file"], str(self.user))  # where a user would write

    def test_user_wins_key_by_key_and_adds_websites(self):
        config = self.load(
            system={"allowed_origins": ["https://*.example.com"], "read_timeout": 4, "labelary_url": None},
            user={"allowed_origins": ["https://dev.local", "https://*.example.com"], "read_timeout": 1},
        )
        self.assertEqual(config["allowed_origins"], ["https://*.example.com", "https://dev.local"])
        self.assertEqual(config["read_timeout"], 1)
        self.assertIsNone(config["labelary_url"])  # kept from the organization
        self.assertEqual(config["_config_files"], [str(self.system), str(self.user)])

    def test_errors_name_both_files(self):
        with self.assertRaises(ConfigError) as ctx:
            self.load(system={"port": "x"}, user={})
        self.assertIn(f"{self.system} + {self.user}", str(ctx.exception))
        self.system.write_text("{", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.user, system_path=self.system)
        self.assertIn(str(self.system), str(ctx.exception))

    def test_same_file_is_read_once(self):
        self.system.write_text(json.dumps({"allowed_origins": ["https://a.example"]}), encoding="utf-8")
        config = load_config(self.system, system_path=self.system)
        self.assertEqual(config["_config_files"], [str(self.system)])
        self.assertEqual(config["allowed_origins"], ["https://a.example"])

    def test_tests_never_read_the_machine_file(self):
        self.assertNotEqual(str(load_config(self.user)["_config_files"]), "['/etc/zebra-print-bridge/config.json']")
        self.assertIn("nonexistent", os.environ["ZEBRA_PRINT_BRIDGE_SYSTEM_CONFIG"])


if __name__ == "__main__":
    unittest.main()
