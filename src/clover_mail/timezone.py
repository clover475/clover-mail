"""One configured timezone for brief dates and local day boundaries."""

from __future__ import annotations

import os
from zoneinfo import ZoneInfo


def timezone_name() -> str:
    return os.environ.get("CLOVER_MAIL_TIMEZONE", "UTC")


def local_zone() -> ZoneInfo:
    return ZoneInfo(timezone_name())
