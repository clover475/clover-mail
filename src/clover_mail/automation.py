"""Install a per-user launchd schedule only after local readiness checks pass."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .apple_mail import doctor
from .archive import Archive, DEFAULT_DATA_DIR
from .email_delivery import SMTPConfig
from .feishu import FeishuConfig
from .mail_center import _load_state
from .mimo import MiMoConfig

LABEL = "com.clover.mail"
AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def launch_agent_definition(*, project_root: Path, python_path: Path, env_file: Path,
                            local_only: bool = False) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [str(python_path), "-m", "clover_mail",
                             "run-local-once" if local_only else "run-once"],
        "WorkingDirectory": str(project_root),
        "EnvironmentVariables": {
            "PYTHONPATH": str(project_root / "src"),
            "CLOVER_MAIL_ENV_FILE": str(env_file),
        },
        "RunAtLoad": True,
        "StartInterval": 900,
        "Umask": 63,
        "StandardOutPath": str(DEFAULT_DATA_DIR / "runner.log"),
        "StandardErrorPath": str(DEFAULT_DATA_DIR / "runner-error.log"),
    }


def _runtime_file() -> Path:
    selected = os.environ.get("CLOVER_MAIL_ENV_FILE")
    if not selected:
        raise ValueError("CLOVER_MAIL_ENV_FILE is required to install the schedule")
    env_file = Path(selected).expanduser().resolve()
    if not env_file.is_file():
        raise ValueError("CLOVER_MAIL_ENV_FILE does not exist")
    return env_file


def _install(env_file: Path, *, local_only: bool) -> Path:
    project_root = Path(__file__).resolve().parents[2]
    agent = launch_agent_definition(project_root=project_root, python_path=Path(sys.executable),
                                    env_file=env_file, local_only=local_only)
    AGENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Replacing local-only with full delivery updates the same label cleanly.
    if AGENT_PATH.exists():
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(AGENT_PATH)],
                       capture_output=True, text=True, timeout=20)
    AGENT_PATH.write_bytes(plistlib.dumps(agent))
    AGENT_PATH.chmod(0o600)
    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(AGENT_PATH)],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode != 0:
        raise ValueError("launchctl could not bootstrap Clover Mail; inspect the private runner log")
    return AGENT_PATH


def install_local_launch_agent() -> Path:
    env_file = _runtime_file()
    doctor()
    MiMoConfig.from_environment()
    FeishuConfig.from_environment()
    if not _load_state().get("table_id"):
        raise ValueError("Feishu Mail Center must be created before scheduling")
    archive = Archive()
    try:
        if not archive.count() or not archive.analyzed_count():
            raise ValueError("complete one real import and analysis before scheduling local backfill")
    finally:
        archive.close()
    return _install(env_file, local_only=True)


def install_launch_agent() -> Path:
    env_file = _runtime_file()
    doctor()
    MiMoConfig.from_environment()
    FeishuConfig.from_environment()
    SMTPConfig.from_environment()
    if not _load_state().get("table_id"):
        raise ValueError("Feishu Mail Center must be created before scheduling")
    archive = Archive()
    try:
        delivered = archive.db.execute(
            "SELECT COUNT(*) FROM daily_briefs WHERE feishu_sent_at IS NOT NULL AND email_sent_at IS NOT NULL"
        ).fetchone()[0]
        if not archive.count() or not archive.analyzed_count() or not archive.record_mapping() or not delivered:
            raise ValueError("complete one real import, analysis, Feishu publish, and two-channel brief before scheduling")
    finally:
        archive.close()
    return _install(env_file, local_only=False)
