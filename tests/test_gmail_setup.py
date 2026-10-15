from __future__ import annotations

import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts/configure_gmail.py"
SPEC = importlib.util.spec_from_file_location("configure_gmail", SCRIPT)
assert SPEC and SPEC.loader
gmail = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gmail)


class GmailSetupTests(unittest.TestCase):
    def test_private_atomic_update_preserves_other_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "runtime.env"
            path.write_text("MIMO_MODEL=mimo-v2.6-flash\nCLOVER_MAIL_SMTP_HOST=old\n")
            path.chmod(0o600)
            gmail._write_settings(path, {"CLOVER_MAIL_SMTP_HOST": "smtp.gmail.com",
                                         "CLOVER_MAIL_SMTP_PASSWORD": "synthetic-only"})
            content = path.read_text()
            self.assertIn("MIMO_MODEL=mimo-v2.6-flash", content)
            self.assertEqual(content.count("CLOVER_MAIL_SMTP_HOST="), 1)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_requires_gmail_sender(self):
        self.assertEqual(gmail._email(" demo.sender@gmail.com ", gmail=True), "demo.sender@gmail.com")
        with self.assertRaises(ValueError):
            gmail._email("clover@example.com", gmail=True)


if __name__ == "__main__":
    unittest.main()
