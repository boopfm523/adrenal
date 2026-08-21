"""Interactive, local-only bootstrap for the isolated Garmin Connect token store."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import UTC, datetime
from importlib.metadata import version

from sqlalchemy import select

from healthcurve.config import get_settings
from healthcurve.db import get_session_factory
from healthcurve.identity.models import Owner
from healthcurve.integrations.garmin.connect_client import (
    GarminProviderError,
    PythonGarminReadClient,
)
from healthcurve.integrations.garmin.models import GarminConnection, GarminConnectionState


def connect() -> int:
    settings = get_settings()
    if settings.garmin_email is None or settings.garmin_password is None:
        print("garmin_credentials_not_configured")
        return 2
    if settings.garmin_token_store is None:
        print("garmin_token_store_not_configured")
        return 2

    factory = get_session_factory()
    with factory() as session:
        owners = list(session.scalars(select(Owner.id).limit(2)))
    if len(owners) != 1:
        print("garmin_owner_unavailable")
        return 2
    owner_id = owners[0]

    client = PythonGarminReadClient(
        email=settings.garmin_email.get_secret_value(),
        password=settings.garmin_password.get_secret_value(),
        token_store=settings.garmin_token_store,
        prompt_mfa=lambda: getpass.getpass("Garmin MFA code: "),
    )
    try:
        client.login()
    except GarminProviderError as exc:
        print(exc.reason_code)
        return 1

    now = datetime.now(UTC)
    with factory() as session, session.begin():
        connection = session.scalar(
            select(GarminConnection).where(GarminConnection.owner_id == owner_id).with_for_update()
        )
        if connection is None:
            connection = GarminConnection(
                owner_id=owner_id,
                state=GarminConnectionState.CONNECTED,
                connected_at=now,
                sync_lookback_days=settings.garmin_sync_lookback_days,
                capabilities={},
                client_version=version("garminconnect"),
            )
            session.add(connection)
        else:
            connection.state = GarminConnectionState.CONNECTED
            connection.connected_at = now
            connection.disconnected_at = None
            connection.last_error_code = None
            connection.client_version = version("garminconnect")

    print(json.dumps({"state": "connected", "tokens_stored": True}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HealthCurve Garmin connection bootstrap")
    parser.add_subparsers(dest="command", required=True).add_parser("connect")
    args = parser.parse_args(argv)
    return connect() if args.command == "connect" else 2


if __name__ == "__main__":
    sys.exit(main())
