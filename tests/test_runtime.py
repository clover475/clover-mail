from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from clover_mail.runtime import load_runtime_env


class RuntimeTests(unittest.TestCase):
    def test_private_runtime_file_can_reference_existing_mimo_key(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "provider.env"
            source.write_text('export MIMO_API_KEY="old-test-key"\nMIMO_API_KEY=new-test-key\nMIMO_BASE_URL=https://api.xiaomimimo.com/v1\n')
            source.chmod(0o600)
            runtime = root / "runtime.env"
            runtime.write_text(f"CLOVER_MAIL_MIMO_ENV_FILE={source}\nMIMO_MODEL=mimo-v2.6-flash\n")
            runtime.chmod(0o600)
            with patch.dict(os.environ, {"CLOVER_MAIL_ENV_FILE": str(runtime), "MIMO_API_KEY": "stale-process-key"}):
                load_runtime_env()
                self.assertEqual(os.environ["MIMO_API_KEY"], "new-test-key")
                self.assertEqual(os.environ["MIMO_MODEL"], "mimo-v2.6-flash")

    def test_rejects_world_readable_key_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "provider.env"
            source.write_text("MIMO_API_KEY=test-key\n")
            source.chmod(0o644)
            runtime = root / "runtime.env"
            runtime.write_text(f"CLOVER_MAIL_MIMO_ENV_FILE={source}\n")
            runtime.chmod(0o600)
            with patch.dict(os.environ, {"CLOVER_MAIL_ENV_FILE": str(runtime)}):
                with self.assertRaisesRegex(ValueError, "owner-only"):
                    load_runtime_env()

    def test_selective_remote_image_setting_reaches_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder) / "runtime.env"
            runtime.write_text("CLOVER_MAIL_REMOTE_IMAGES=selective\n")
            runtime.chmod(0o600)
            with patch.dict(os.environ, {"CLOVER_MAIL_ENV_FILE": str(runtime)}, clear=True):
                load_runtime_env()
                self.assertEqual(os.environ["CLOVER_MAIL_REMOTE_IMAGES"], "selective")


if __name__ == "__main__":
    unittest.main()
