"""Administrative CLI for one-time registration invitations."""

from argparse import ArgumentParser, Namespace
from datetime import timedelta
from secrets import token_urlsafe

from sqlalchemy import select

from .auth import hash_invite_code, utcnow
from .database import Base, SessionLocal, engine
from .models import InvitationCode


def create_invitations(*, count: int, expires_days: int, label: str | None) -> list[str]:
    Base.metadata.create_all(bind=engine)
    now = utcnow()
    expires_at = now + timedelta(days=expires_days)
    raw_codes: list[str] = []
    with SessionLocal() as db:
        for index in range(count):
            raw_code = token_urlsafe(24)
            item_label = label
            if label and count > 1:
                item_label = f"{label} #{index + 1}"
            db.add(
                InvitationCode(
                    code_hash=hash_invite_code(raw_code),
                    label=item_label,
                    expires_at=expires_at,
                )
            )
            raw_codes.append(raw_code)
        db.commit()
    return raw_codes


def disable_invitation(raw_code: str) -> bool:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        invitation = db.scalar(
            select(InvitationCode).where(
                InvitationCode.code_hash == hash_invite_code(raw_code.strip())
            )
        )
        if invitation is None:
            return False
        if invitation.disabled_at is None:
            invitation.disabled_at = utcnow()
            db.commit()
        return True


def parser() -> ArgumentParser:
    command_parser = ArgumentParser(description="Manage MoniPan registration invitations")
    subparsers = command_parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="create one-time invitations")
    create_parser.add_argument("--count", type=int, default=1, choices=range(1, 101))
    create_parser.add_argument("--expires-days", type=int, default=30, choices=range(1, 3651))
    create_parser.add_argument("--label", type=str, default=None)

    disable_parser = subparsers.add_parser("disable", help="disable an invitation")
    disable_parser.add_argument("code", type=str)
    return command_parser


def main(args: Namespace | None = None) -> int:
    parsed = args or parser().parse_args()
    if parsed.command == "create":
        codes = create_invitations(
            count=parsed.count,
            expires_days=parsed.expires_days,
            label=parsed.label,
        )
        print("Invitation codes (shown once):")
        for code in codes:
            print(code)
        return 0

    if disable_invitation(parsed.code):
        print("Invitation disabled")
        return 0
    print("Invitation not found")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
