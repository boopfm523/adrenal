"""Runtime-only Garmin fixtures built from explicitly synthetic values.

No exported Garmin file is committed. The official SDK produces valid FIT bytes in
memory for each test, and every human-readable fixture carries the project marker.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

from garmin_fit_sdk import Encoder, Profile

from tests.fixtures.synthetic import SYNTHETIC_MARKER

SYNTHETIC_START = datetime(2026, 1, 2, 8, 0, tzinfo=UTC)
_MESG_NUM: Any = Profile["mesg_num"]


def synthetic_fit() -> bytes:
    encoder = Encoder()
    messages = [
        {
            "mesg_num": _MESG_NUM["FILE_ID"],
            "type": "activity",
            "manufacturer": "garmin",
            "product": 123,
            "serial_number": 42,
            "time_created": SYNTHETIC_START,
        },
        {
            "mesg_num": _MESG_NUM["DEVICE_INFO"],
            "timestamp": SYNTHETIC_START,
            "manufacturer": "garmin",
            "product_name": f"Synthetic Device {SYNTHETIC_MARKER}",
            "serial_number": 42,
        },
        {
            "mesg_num": _MESG_NUM["RECORD"],
            "timestamp": SYNTHETIC_START,
            "heart_rate": 75,
        },
        {
            "mesg_num": _MESG_NUM["HRV_VALUE"],
            "timestamp": SYNTHETIC_START,
            "value": 47,
        },
        {
            "mesg_num": _MESG_NUM["HSA_BODY_BATTERY_DATA"],
            "timestamp": SYNTHETIC_START,
            "level": 64,
            "processing_interval": 60,
        },
        {
            "mesg_num": _MESG_NUM["HSA_STEP_DATA"],
            "timestamp": SYNTHETIC_START,
            "steps": 321,
            "processing_interval": 60,
        },
        {
            "mesg_num": _MESG_NUM["SLEEP_LEVEL"],
            "timestamp": SYNTHETIC_START,
            "sleep_level": "light",
        },
        {
            "mesg_num": _MESG_NUM["SLEEP_LEVEL"],
            "timestamp": SYNTHETIC_START + timedelta(hours=7),
            "sleep_level": "awake",
        },
        {
            "mesg_num": _MESG_NUM["SLEEP_ASSESSMENT"],
            "overall_sleep_score": 82,
        },
        {
            "mesg_num": _MESG_NUM["SESSION"],
            "start_time": SYNTHETIC_START,
            "timestamp": SYNTHETIC_START + timedelta(hours=1),
            "sport": "running",
            "sub_sport": "generic",
            "sport_profile_name": SYNTHETIC_MARKER,
            "total_elapsed_time": 3600,
            "total_distance": 10_000,
            "total_calories": 500,
            "avg_heart_rate": 150,
            "max_heart_rate": 180,
        },
    ]
    for message in messages:
        encoder.write_mesg(message)
    return bytes(encoder.close())


def synthetic_activity_csv(*, explicit_distance_unit: bool = True) -> bytes:
    distance = "Distance (km)" if explicit_distance_unit else "Distance"
    return (
        f"Activity Type,Date,Title,Time,{distance},Calories,Avg HR,Max HR\n"
        f"Cycling,2026-01-03 09:30:00,{SYNTHETIC_MARKER},01:15:00,"
        "25.5,600,140,172\n"
    ).encode()


def synthetic_archive() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("DI_CONNECT/DI-GOLF/ACTIVITY.fit", synthetic_fit())
        archive.writestr("DI_CONNECT/DI-GOLF/Activities.csv", synthetic_activity_csv())
    return output.getvalue()
