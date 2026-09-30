"""Idempotent authorization seed data for local development."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.database import AsyncSessionLocal
from database.model import Account, AccountType, Group, Position
from routes.auth import hash_password


CLASS_GROUP = ("class", "320")
DEV_TEACHER_GROUP = ("department", "開發教師")
POSITION_NAMES = ("一般學生", "班代表", "資訊股長", "教師")


async def _ensure_group(
    session: AsyncSession,
    *,
    group_type: str,
    name: str,
    results: Counter[str],
) -> Group:
    group = await session.scalar(
        select(Group).where(
            Group.type == group_type,
            Group.name == name,
            Group.parent_id.is_(None),
        )
    )
    if group is not None:
        results["groups_skipped"] += 1
        return group

    group = Group(type=group_type, name=name, parent_id=None, propagate_confirmation=True)
    session.add(group)
    await session.flush()
    results["groups_created"] += 1
    return group


async def _ensure_position(
    session: AsyncSession, *, name: str, results: Counter[str]
) -> Position:
    position = await session.scalar(select(Position).where(Position.name == name))
    if position is not None:
        results["positions_skipped"] += 1
        return position

    position = Position(name=name, permissions=0, description=None)
    session.add(position)
    await session.flush()
    results["positions_created"] += 1
    return position


async def _ensure_student_account(
    session: AsyncSession,
    *,
    position: Position,
    group: Group,
    password: str,
    results: Counter[str],
) -> None:
    account = await session.scalar(
        select(Account).where(
            Account.account == "320",
            Account.account_type == AccountType.STUDENT,
            Account.position_id == position.id,
        )
    )
    if account is not None:
        results["accounts_skipped"] += 1
        return

    session.add(
        Account(
            account="320",
            account_type=AccountType.STUDENT,
            position_id=position.id,
            group_id=group.id,
            password_hash=await hash_password(password),
            display_name=None,
            is_active=True,
        )
    )
    await session.flush()
    results["accounts_created"] += 1


async def _ensure_teacher_account(
    session: AsyncSession,
    *,
    position: Position,
    group: Group,
    password: str,
    results: Counter[str],
) -> None:
    account = await session.scalar(
        select(Account).where(
            Account.account == "teacher", Account.account_type == AccountType.TEACHER
        )
    )
    if account is not None:
        results["accounts_skipped"] += 1
        return

    session.add(
        Account(
            account="teacher",
            account_type=AccountType.TEACHER,
            position_id=position.id,
            group_id=group.id,
            password_hash=await hash_password(password),
            display_name=None,
            is_active=True,
        )
    )
    await session.flush()
    results["accounts_created"] += 1


def _dev_password() -> str:
    password = os.getenv("SEED_DEV_PASSWORD", "")
    if not password.strip():
        raise ValueError("SEED_DEV_PASSWORD must be set when using --dev-account")
    return password


async def seed_database(*, dev_account: bool = False) -> Counter[str]:
    """Create missing seed data without changing any existing records."""
    password = _dev_password() if dev_account else None
    results: Counter[str] = Counter()

    async with AsyncSessionLocal() as session:
        async with session.begin():
            class_group = await _ensure_group(
                session,
                group_type=CLASS_GROUP[0],
                name=CLASS_GROUP[1],
                results=results,
            )
            positions = {
                name: await _ensure_position(session, name=name, results=results)
                for name in POSITION_NAMES
            }

            if dev_account:
                dev_group = await _ensure_group(
                    session,
                    group_type=DEV_TEACHER_GROUP[0],
                    name=DEV_TEACHER_GROUP[1],
                    results=results,
                )
                await _ensure_student_account(
                    session,
                    position=positions["一般學生"],
                    group=class_group,
                    password=password,
                    results=results,
                )
                await _ensure_teacher_account(
                    session,
                    position=positions["教師"],
                    group=dev_group,
                    password=password,
                    results=results,
                )
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dev-account",
        action="store_true",
        help="also create the fixed local-development student and teacher accounts",
    )
    return parser.parse_args()


def _print_summary(results: Counter[str]) -> None:
    print(
        "Seed complete: "
        f"groups created={results['groups_created']} skipped={results['groups_skipped']}; "
        f"positions created={results['positions_created']} skipped={results['positions_skipped']}; "
        f"accounts created={results['accounts_created']} skipped={results['accounts_skipped']}"
    )


def main() -> int:
    args = _parse_args()
    try:
        _print_summary(asyncio.run(seed_database(dev_account=args.dev_account)))
    except Exception as error:
        print(f"Seed failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
