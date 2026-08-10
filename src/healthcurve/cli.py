"""Operator commands: create the owner, load medications, connect Telegram.

Run these inside the api container, which is on the private network:

    docker compose run --rm api python -m healthcurve.cli <command>

The medication and regimen loaders read a YAML file so the clinical values are
reviewed as a file rather than typed at a prompt, and so the same file can be applied
to a fresh install after a restore.
"""

from __future__ import annotations

import argparse
import getpass
import sys
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from healthcurve.config import Environment, TelegramMode, get_settings
from healthcurve.db import get_session_factory
from healthcurve.identity import service as auth
from healthcurve.identity.models import Owner
from healthcurve.identity.recovery import OwnerRecoveryError
from healthcurve.identity.recovery import recover_owner_access as recover_owner
from healthcurve.medications import service as meds
from healthcurve.medications.models import (
    ApprovedInstruction,
    InstructionCategory,
    Medication,
    RegimenDoseSlot,
)


def _owner(session: Any) -> Owner:
    owner = session.scalar(select(Owner).limit(1))
    if owner is None:
        sys.exit("No owner exists yet. Run: create-owner")
    return owner


# ---------------------------------------------------------------------------
# create-owner
# ---------------------------------------------------------------------------


def create_owner(args: argparse.Namespace) -> int:
    factory = get_session_factory()
    with factory() as session, session.begin():
        if session.scalar(select(Owner).limit(1)) is not None:
            sys.exit("An owner already exists. HealthCurve is single-owner by design.")

        email = args.email or input("Email: ").strip()
        password = args.password or getpass.getpass("Password (min 12 chars): ")
        if len(password) < 12:
            sys.exit("Password must be at least 12 characters.")
        if args.password is None and password != getpass.getpass("Confirm password: "):
            sys.exit("Passwords did not match.")

        owner = Owner(
            email=email.lower(),
            password_hash=auth.hash_password(password),
            display_name=args.name,
            default_timezone=args.timezone,
        )
        session.add(owner)
        session.flush()
        print(f"Created owner {owner.email} (timezone {owner.default_timezone})")
    return 0


# ---------------------------------------------------------------------------
# recover-owner-access
# ---------------------------------------------------------------------------


def recover_owner_access(args: argparse.Namespace) -> int:
    """Recover an unknown bootstrap login without creating or deleting an owner."""
    settings = get_settings()
    if settings.environment is not Environment.DEV:
        sys.exit("Owner access recovery is available only in development.")

    phrase = "RECOVER DEVELOPMENT OWNER"
    print("This changes the existing development owner's login and signs out every session.")
    print("It does not delete or recreate health, plan, AI, document, or integration data.")
    if input(f"Type {phrase} to continue: ").strip() != phrase:
        sys.exit("Recovery cancelled; nothing was changed.")

    email = args.email or input("New owner email: ").strip()
    password = getpass.getpass("New password (min 12 chars): ")
    if password != getpass.getpass("Confirm new password: "):
        sys.exit("Passwords did not match; nothing was changed.")

    factory = get_session_factory()
    with factory() as session, session.begin():
        owner = _owner(session)
        try:
            revoked = recover_owner(
                session,
                owner,
                environment=settings.environment,
                new_email=email,
                new_password=password,
            )
        except OwnerRecoveryError as exc:
            sys.exit(str(exc))

    print(f"Owner login recovered. Revoked {revoked} existing session(s).")
    return 0


# ---------------------------------------------------------------------------
# load-medications
# ---------------------------------------------------------------------------

MEDICATIONS_TEMPLATE = """\
# HealthCurve medications and regimen.
#
# Fill this in from your prescription and your physician's written instructions, then:
#   docker compose run --rm -v "$PWD/medications.yaml:/tmp/m.yaml" api \\
#       python -m healthcurve.cli load-medications /tmp/m.yaml
#
# Amounts are exact decimals. Write 2.5, not 2.5000000001.

medications:
  - name: Hydrocortisone
    formulation: tablet
    strength: 10
    strength_unit: mg
    default_unit: mg
    default_route: oral

  - name: Fludrocortisone
    formulation: tablet
    strength: 0.1
    strength_unit: mg
    default_unit: mg
    default_route: oral

  # Needed for /injection and the emergency page. Must have route intramuscular.
  - name: Hydrocortisone sodium succinate
    formulation: injection
    strength: 100
    strength_unit: mg
    default_unit: mg
    default_route: intramuscular

# The regimen is created as a DRAFT. It is not in force until you approve it, which
# requires naming the clinician who approved it -- HealthCurve will not treat an
# unapproved schedule as your plan.
regimen:
  version_label: "2026 replacement schedule"
  effective_from: 2026-01-01T00:00:00
  slots:
    - medication: Hydrocortisone
      time: "07:00"
      amount: 10
      unit: mg
    - medication: Hydrocortisone
      time: "12:30"
      amount: 5
      unit: mg
    - medication: Hydrocortisone
      time: "17:00"
      amount: 2.5
      unit: mg
    - medication: Fludrocortisone
      time: "07:00"
      amount: 0.1
      unit: mg

  # Physician-authored text, shown verbatim on the emergency page. Write what your
  # clinician actually told you. Do not paraphrase, and do not invent rules.
  instructions:
    - category: illness
      title: Sick day rules
      authored_by: "Dr Example, Endocrinology"
      authored_on: 2026-01-01
      body: |
        Replace with the exact wording your physician gave you.
    - category: emergency
      title: Emergency injection
      authored_by: "Dr Example, Endocrinology"
      authored_on: 2026-01-01
      body: |
        Replace with the exact wording your physician gave you.
"""


def init_template(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        sys.exit(f"{path} already exists. Use --force to overwrite.")
    path.write_text(MEDICATIONS_TEMPLATE, encoding="utf-8")
    print(f"Wrote {path}. Fill it in, then run: load-medications {path}")
    return 0


def load_medications(args: argparse.Namespace) -> int:
    data: dict[str, Any] = yaml.safe_load(Path(args.path).read_text(encoding="utf-8"))
    factory = get_session_factory()

    with factory() as session, session.begin():
        owner = _owner(session)
        by_name: dict[str, Medication] = {}

        for entry in data.get("medications", []):
            existing = meds.find_medication_by_name(session, owner.id, entry["name"])
            if existing is not None:
                by_name[entry["name"]] = existing
                print(f"  = {entry['name']} (already present)")
                continue

            medication = Medication(
                owner_id=owner.id,
                name=entry["name"],
                normalized_name=meds.normalize_name(entry["name"]),
                formulation=entry.get("formulation"),
                strength=Decimal(str(entry["strength"])) if entry.get("strength") else None,
                strength_unit=entry.get("strength_unit"),
                default_unit=entry.get("default_unit", "mg"),
                default_route=entry.get("default_route", "oral"),
            )
            session.add(medication)
            session.flush()
            by_name[entry["name"]] = medication
            print(f"  + {entry['name']}")

        regimen = data.get("regimen")
        if not regimen:
            print("No regimen section; medications only.")
            return 0

        version = meds.create_draft(
            session,
            owner_id=owner.id,
            version_label=regimen["version_label"],
            effective_from=_as_datetime(regimen["effective_from"]),
            effective_to=(
                _as_datetime(regimen["effective_to"]) if regimen.get("effective_to") else None
            ),
        )

        for slot in regimen.get("slots", []):
            medication = by_name.get(slot["medication"]) or meds.find_medication_by_name(
                session, owner.id, slot["medication"]
            )
            if medication is None:
                sys.exit(f"Slot refers to unknown medication: {slot['medication']}")
            hour, minute = (int(p) for p in str(slot["time"]).split(":"))
            session.add(
                RegimenDoseSlot(
                    regimen_version_id=version.id,
                    medication_id=medication.id,
                    scheduled_local_time=time(hour, minute),
                    amount=Decimal(str(slot["amount"])),
                    unit=slot.get("unit", medication.default_unit),
                    route=slot.get("route", medication.default_route),
                    condition=slot.get("condition"),
                )
            )

        for instruction in regimen.get("instructions", []):
            session.add(
                ApprovedInstruction(
                    regimen_version_id=version.id,
                    category=InstructionCategory(instruction.get("category", "general")),
                    title=instruction["title"],
                    body=instruction["body"],
                    authored_by=instruction["authored_by"],
                    authored_on=_as_date(instruction["authored_on"]),
                )
            )

        session.flush()
        print(f"\nCreated DRAFT regimen {version.id} ({version.version_label}).")
        print("It is not in force yet. Approve it with:")
        print(f'  approve-regimen {version.id} --by "Dr Name" --source "clinic letter 2026-01-01"')
    return 0


def approve_regimen(args: argparse.Namespace) -> int:
    """Record a physician's approval (SAFE-16). A human act, never automated."""
    from healthcurve.medications.models import RegimenVersion

    factory = get_session_factory()
    with factory() as session, session.begin():
        owner = _owner(session)
        version = session.get(RegimenVersion, uuid.UUID(args.version_id))
        if version is None or version.owner_id != owner.id:
            sys.exit("No such regimen version.")
        try:
            meds.approve_version(session, version, approved_by=args.by, approval_source=args.source)
        except meds.PlanError as exc:
            sys.exit(str(exc))
        print(f"Approved {version.version_label}, effective from {version.effective_from}.")
    return 0


# ---------------------------------------------------------------------------
# development-only legacy synthetic medication cleanup
# ---------------------------------------------------------------------------


def purge_synthetic_medication_bootstrap(args: argparse.Namespace) -> int:
    """Preview, then optionally purge only the exact legacy sample data."""
    from healthcurve.development_cleanup import (
        SyntheticBootstrapCleanupError,
        execute_synthetic_bootstrap_cleanup,
        preview_synthetic_bootstrap,
    )

    settings = get_settings()
    if settings.environment is not Environment.DEV:
        sys.exit("Synthetic medication bootstrap cleanup is available only in development.")

    factory = get_session_factory()
    try:
        with factory() as session, session.begin():
            owner = _owner(session)
            preview = preview_synthetic_bootstrap(session, owner_id=owner.id)
            print("Exact legacy synthetic medication bootstrap preview")
            print(f"  profile: {preview.profile_version}")
            print(
                "  regimen_version IDs: "
                + ", ".join(str(row_id) for row_id in preview.regimen_version_ids)
            )
            print(
                "  regimen_dose_slot IDs: "
                + ", ".join(str(row_id) for row_id in preview.regimen_dose_slot_ids)
            )
            print(
                "  approved_instruction IDs: "
                + ", ".join(str(row_id) for row_id in preview.approved_instruction_ids)
            )
            print(
                "  medication IDs: " + ", ".join(str(row_id) for row_id in preview.medication_ids)
            )
            print("  rows:")
            print(f"    plan.regimen_version={preview.counts.regimen_versions}")
            print(f"    plan.regimen_dose_slot={preview.counts.regimen_dose_slots}")
            print(f"    plan.approved_instruction={preview.counts.approved_instructions}")
            print(f"    plan.medication={preview.counts.medications}")
            print("  retained references:")
            print(f"    fact.dose_event={preview.references.doses}")
            print(f"    fact.emergency_injection_event={preview.references.injections}")
            print(
                f"    plan.regimen_dose_slot outside target={preview.references.other_plan_slots}"
            )
            print(f"    ops.report_snapshot={preview.references.reports}")
            print(f"    ai.ai_analysis={preview.references.analyses}")
            print(f"    ai.extraction_draft={preview.references.extraction_drafts}")
            print(f"    source_document={preview.references.documents}")
            print(f"  required confirmation: {preview.confirmation_phrase}")
            if preview.references.total:
                message = (
                    "Cleanup is blocked by retained references; nothing changed. "
                    "Remove only demonstrably synthetic referenced records through their safe "
                    "record-specific workflows, then preview again."
                )
                if args.execute:
                    raise SyntheticBootstrapCleanupError(message)
                print(message)
                return 0
            if not args.execute:
                print("Preview only; nothing changed. Rerun with --execute to confirm locally.")
                return 0

            confirmation = input("Type the required confirmation phrase exactly: ")
            counts = execute_synthetic_bootstrap_cleanup(
                session,
                owner_id=owner.id,
                preview=preview,
                confirmation=confirmation,
            )
    except SyntheticBootstrapCleanupError as exc:
        sys.exit(str(exc))

    print(
        "Purged exact legacy synthetic medication bootstrap: "
        f"regimen_versions={counts.regimen_versions}; "
        f"slots={counts.regimen_dose_slots}; "
        f"instructions={counts.approved_instructions}; medications={counts.medications}."
    )
    return 0


# ---------------------------------------------------------------------------
# encrypted integration credentials
# ---------------------------------------------------------------------------


def credential_key_init(args: argparse.Namespace) -> int:
    from healthcurve.integrations.credentials import create_key_file

    create_key_file(Path(args.path), args.key_id)
    print(f"Created credential key ring at {args.path} with active key {args.key_id}.")
    print("Keep this file outside the repository and backup it separately from the database.")
    return 0


def credential_key_add(args: argparse.Namespace) -> int:
    from healthcurve.integrations.credentials import add_active_key

    add_active_key(Path(args.path), args.key_id)
    print(f"Added active credential key {args.key_id}. Run credential-rotate next.")
    return 0


def credential_key_retire(args: argparse.Namespace) -> int:
    from sqlalchemy import func

    from healthcurve.integrations.credentials import IntegrationCredential, retire_key

    factory = get_session_factory()
    with factory() as session:
        rows = session.scalar(
            select(func.count())
            .select_from(IntegrationCredential)
            .where(IntegrationCredential.key_id == args.key_id)
        )
    if rows:
        sys.exit(
            f"Cannot retire {args.key_id}: {rows} credential row(s) still use it. "
            "Run credential-rotate first."
        )
    retire_key(Path(args.path), args.key_id)
    print(f"Retired unused credential key {args.key_id}.")
    return 0


def _credential_key_file() -> Path:
    path = get_settings().credential_key_file
    if path is None:
        sys.exit("Set HC_CREDENTIAL_KEY_FILE to the mounted owner-only key ring.")
    return path


def credential_set(args: argparse.Namespace) -> int:
    from pydantic import SecretStr

    from healthcurve.integrations.credentials import (
        CredentialKeyRing,
        read_private_secret_file,
        set_credential,
    )

    path = _credential_key_file()
    value = (
        read_private_secret_file(Path(args.value_file))
        if args.value_file
        else SecretStr(getpass.getpass(f"{args.provider}/{args.name} value: "))
    )
    if not value.get_secret_value():
        sys.exit("Credential value must not be empty.")
    factory = get_session_factory()
    with factory() as session, session.begin():
        owner = _owner(session)
        set_credential(
            session,
            owner_id=owner.id,
            provider=args.provider,
            name=args.name,
            value=value,
            key_ring=CredentialKeyRing.from_file(path),
        )
    print(f"Stored encrypted credential {args.provider}/{args.name}; restart its worker.")
    return 0


def credential_delete(args: argparse.Namespace) -> int:
    from healthcurve.integrations.credentials import delete_credential

    factory = get_session_factory()
    with factory() as session, session.begin():
        owner = _owner(session)
        deleted = delete_credential(
            session, owner_id=owner.id, provider=args.provider, name=args.name
        )
    print("Credential destroyed." if deleted else "Credential was not present.")
    return 0


def credential_rotate(args: argparse.Namespace) -> int:
    from healthcurve.integrations.credentials import CredentialKeyRing, rotate_credentials

    factory = get_session_factory()
    with factory() as session, session.begin():
        owner = _owner(session)
        count = rotate_credentials(
            session,
            owner_id=owner.id,
            key_ring=CredentialKeyRing.from_file(_credential_key_file()),
        )
    print(f"Re-encrypted {count} credential(s) with the active key.")
    return 0


# ---------------------------------------------------------------------------
# telegram
# ---------------------------------------------------------------------------


def telegram_status(args: argparse.Namespace) -> int:
    from healthcurve.integrations.telegram.client import TelegramClient
    from healthcurve.integrations.telegram.secrets import load_telegram_secrets

    settings = get_settings()
    factory = get_session_factory()
    with factory() as session:
        telegram_secrets = load_telegram_secrets(session, settings)
    print(f"Bot token set:      {'yes' if telegram_secrets.bot_token else 'NO'}")
    print(f"Webhook secret set: {'yes' if telegram_secrets.webhook_secret else 'NO'}")
    print(f"Allowed chat id:    {settings.telegram_allowed_chat_id or 'NOT SET'}")
    print(f"Public base URL:    {settings.public_base_url or 'NOT SET'}")

    if not telegram_secrets.bot_token:
        print("\nStore telegram/bot_token with credential-set. See docs/telegram-setup.md")
        return 1

    client = TelegramClient(settings, token=telegram_secrets.bot_token)
    me = client.get_me()
    if me and me.get("ok"):
        bot = me["result"]
        print(f"\nConnected as @{bot.get('username')} ({bot.get('first_name')})")
    else:
        print("\nCould not reach the Telegram API with this token.")
        return 1

    info = client.get_webhook_info()
    if info and info.get("ok"):
        result = info["result"]
        print(f"Webhook URL:        {result.get('url') or '(not set)'}")
        print(f"Pending updates:    {result.get('pending_update_count', 0)}")
        if result.get("last_error_message"):
            print(f"Last error:         {result['last_error_message']}")
    return 0


def telegram_register(args: argparse.Namespace) -> int:
    from healthcurve.integrations.telegram.client import TelegramClient
    from healthcurve.integrations.telegram.secrets import load_telegram_secrets

    settings = get_settings()
    if settings.telegram_mode is not TelegramMode.WEBHOOK:
        sys.exit("Set HC_TELEGRAM_MODE=webhook before registering a webhook.")
    factory = get_session_factory()
    with factory() as session:
        telegram_secrets = load_telegram_secrets(session, settings)
    if not telegram_secrets.configured_for(settings):
        sys.exit(
            "Telegram is not fully configured. Store telegram/bot_token and "
            "telegram/webhook_secret, and set HC_TELEGRAM_ALLOWED_CHAT_ID. "
            "See docs/telegram-setup.md"
        )
    base = args.base_url or settings.public_base_url
    if not base:
        sys.exit("Set HC_PUBLIC_BASE_URL or pass --base-url https://your.domain")
    if not base.startswith("https://"):
        sys.exit("Telegram requires HTTPS for webhooks.")

    url = f"{base.rstrip('/')}/api/v1/integrations/telegram/webhook"
    secret = telegram_secrets.webhook_secret
    assert secret is not None

    result = TelegramClient(settings, token=telegram_secrets.bot_token).set_webhook(
        url, secret.get_secret_value()
    )
    if result and result.get("ok"):
        print(f"Webhook registered: {url}")
        return 0
    print(f"Failed to register webhook: {result}")
    return 1


def telegram_disconnect(args: argparse.Namespace) -> int:
    from healthcurve.integrations.telegram.client import TelegramClient
    from healthcurve.integrations.telegram.secrets import load_telegram_secrets

    settings = get_settings()
    factory = get_session_factory()
    with factory() as session:
        telegram_secrets = load_telegram_secrets(session, settings)
    result = TelegramClient(settings, token=telegram_secrets.bot_token).delete_webhook()
    print("Webhook deleted." if result and result.get("ok") else f"Failed: {result}")
    return 0


def draft_expiry_status(args: argparse.Namespace) -> int:
    """Show operational state only; never draft payloads or health text."""
    from healthcurve.integrations.telegram.draft_jobs import draft_expiry_health

    factory = get_session_factory()
    with factory() as session:
        health = draft_expiry_health(session)
    status = health.latest_job_status.value if health.latest_job_status else "never_scheduled"
    print(f"Latest status:      {status}")
    print(f"Scheduled for:      {health.scheduled_at.isoformat() if health.scheduled_at else '-'}")
    print(f"Started at:         {health.started_at.isoformat() if health.started_at else '-'}")
    print(f"Finished at:        {health.finished_at.isoformat() if health.finished_at else '-'}")
    print(f"Last error code:    {health.latest_job_error_code or '-'}")
    return 1 if health.latest_job_status is None else 0


def normalize_labs(args: argparse.Namespace) -> int:
    """Backfill reproducible derived lab fields without altering source strings."""
    from healthcurve.labs.service import backfill_normalizations

    factory = get_session_factory()
    with factory() as session, session.begin():
        owner = _owner(session)
        count = backfill_normalizations(session, owner_id=owner.id)
    print(f"Recomputed derived normalization for {count} lab result(s); source facts unchanged.")
    return 0


# ---------------------------------------------------------------------------


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    return datetime.fromisoformat(str(value))


def _as_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return _as_datetime(value).date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="healthcurve", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-owner", help="Create the single owner account")
    p.add_argument("--email")
    p.add_argument("--password", help="Prompted for if omitted (preferred)")
    p.add_argument("--name")
    p.add_argument("--timezone", default="Europe/London")
    p.set_defaults(func=create_owner)

    p = sub.add_parser(
        "recover-owner-access",
        help="Recover an unknown bootstrap login (local development only)",
    )
    p.add_argument("--email", help="Prompted for if omitted")
    p.set_defaults(func=recover_owner_access)

    p = sub.add_parser("init-medications-file", help="Write a template YAML to fill in")
    p.add_argument("path", nargs="?", default="medications.yaml")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=init_template)

    p = sub.add_parser("load-medications", help="Load medications and a draft regimen")
    p.add_argument("path")
    p.set_defaults(func=load_medications)

    p = sub.add_parser("approve-regimen", help="Record a physician's approval")
    p.add_argument("version_id")
    p.add_argument("--by", required=True, help="Clinician name or role")
    p.add_argument("--source", required=True, help="Letter, consultation, portal message")
    p.set_defaults(func=approve_regimen)

    p = sub.add_parser(
        "purge-synthetic-medication-bootstrap",
        help="Preview exact legacy sample-plan cleanup (development only)",
    )
    p.add_argument(
        "--execute",
        action="store_true",
        help="Prompt for the preview-bound phrase and perform the cleanup",
    )
    p.set_defaults(func=purge_synthetic_medication_bootstrap)

    p = sub.add_parser("credential-key-init", help="Create an external credential key ring")
    p.add_argument("path")
    p.add_argument("--key-id", required=True, help="Lowercase version label, e.g. key_2026_08")
    p.set_defaults(func=credential_key_init)

    p = sub.add_parser("credential-key-add", help="Add and activate a credential key")
    p.add_argument("path")
    p.add_argument("--key-id", required=True)
    p.set_defaults(func=credential_key_add)

    p = sub.add_parser("credential-key-retire", help="Remove an unused inactive key")
    p.add_argument("path")
    p.add_argument("--key-id", required=True)
    p.set_defaults(func=credential_key_retire)

    p = sub.add_parser("credential-set", help="Store one encrypted integration credential")
    p.add_argument("provider", help="Lowercase provider, e.g. telegram")
    p.add_argument("name", help="Lowercase credential name, e.g. bot_token")
    p.add_argument("--value-file", help="Read from a private file instead of a hidden prompt")
    p.set_defaults(func=credential_set)

    p = sub.add_parser("credential-delete", help="Destroy one integration credential")
    p.add_argument("provider")
    p.add_argument("name")
    p.set_defaults(func=credential_delete)

    p = sub.add_parser("credential-rotate", help="Re-encrypt credentials with active key")
    p.set_defaults(func=credential_rotate)

    p = sub.add_parser("telegram-status", help="Check the Telegram configuration")
    p.set_defaults(func=telegram_status)

    p = sub.add_parser("telegram-register", help="Register the webhook with Telegram")
    p.add_argument("--base-url", help="https://your.domain (defaults to HC_PUBLIC_BASE_URL)")
    p.set_defaults(func=telegram_register)

    p = sub.add_parser("telegram-disconnect", help="Remove the webhook")
    p.set_defaults(func=telegram_disconnect)

    p = sub.add_parser("draft-expiry-status", help="Check automatic draft expiry")
    p.set_defaults(func=draft_expiry_status)

    p = sub.add_parser(
        "normalize-labs", help="Recompute versioned derived fields for existing lab facts"
    )
    p.set_defaults(func=normalize_labs)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
