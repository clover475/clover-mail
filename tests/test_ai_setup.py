from __future__ import annotations

import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts/configure_ai.py"
SPEC = importlib.util.spec_from_file_location("configure_ai", SCRIPT)
assert SPEC and SPEC.loader
configure = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure)


class AISetupTests(unittest.TestCase):
    def test_endpoint_requires_https_without_userinfo_or_tracking_query(self):
        self.assertEqual(configure._validate_endpoint(" https://api.example.com/v1/ "),
                         "https://api.example.com/v1")
        for endpoint in ("http://api.example.com", "https://user:pass@example.com",
                         "https://api.example.com?token=secret", "https://api.example.com/#fragment"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                configure._validate_endpoint(endpoint)

    def test_private_atomic_update_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings" / "runtime.env"
            path.parent.mkdir(mode=0o700)
            path.write_text("CLOVER_MAIL_TIMEZONE=Asia/Singapore\n")
            path.chmod(0o600)
            configure._write_settings(path, {"CLOVER_MAIL_AI_PROVIDER": "anthropic",
                                             "CLOVER_MAIL_AI_API_KEY": "synthetic-only"})
            self.assertIn("CLOVER_MAIL_TIMEZONE=Asia/Singapore", path.read_text())
            self.assertEqual(path.read_text().count("CLOVER_MAIL_AI_PROVIDER="), 1)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_refuses_public_permissions_and_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runtime.env"
            path.write_text("CLOVER_MAIL_TIMEZONE=UTC\n")
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                configure._write_settings(path, {"CLOVER_MAIL_AI_API_KEY": "synthetic"})
            linked = Path(folder) / "linked.env"
            linked.symlink_to(path)
            with self.assertRaises(ValueError):
                configure._write_settings(linked, {"CLOVER_MAIL_AI_API_KEY": "synthetic"})


if __name__ == "__main__":
    unittest.main()
