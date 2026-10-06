from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import DateTime, Text, Uuid
from sqlalchemy.orm import configure_mappers

from database.model import Base


TABLE_COLUMNS = {
    "broadcasts": ("id", "author_id", "content", "created_at", "ack_deadline_at"),
    "broadcast_targets": ("broadcast_id", "group_id"),
    "broadcast_confirmations": (
        "broadcast_id", "group_id", "confirmed_by_account_id", "confirmed_at"
    ),
    "replies": ("id", "broadcast_id", "group_id", "author_id", "content", "ref_id", "created_at"),
}


def test_broadcast_metadata_and_relationships() -> None:
    configure_mappers()
    for name, columns in TABLE_COLUMNS.items():
        table = Base.metadata.tables[name]
        assert tuple(table.columns.keys()) == columns
        assert [column.name for column in table.primary_key] == (
            ["broadcast_id", "group_id"]
            if name in ("broadcast_targets", "broadcast_confirmations") else ["id"]
        )
        for column in table.columns:
            assert column.nullable == (column.name == "ref_id")
            if column.name == "id" or column.name.endswith("_id"):
                assert isinstance(column.type, Uuid)
            elif column.name == "content":
                assert isinstance(column.type, Text)
            else:
                assert isinstance(column.type, DateTime)
                assert column.type.timezone is False
        for column in table.columns:
            if column.name in ("created_at", "confirmed_at"):
                assert str(column.server_default.arg) == "timezone('utc', now())"
    deadline = Base.metadata.tables["broadcasts"].c.ack_deadline_at
    assert deadline.default is None
    assert deadline.server_default is None
    assert {
        foreign_key.target_fullname
        for foreign_key in Base.metadata.tables["replies"].c.ref_id.foreign_keys
    } == {"replies.id"}


def test_broadcast_migration_is_the_only_head_and_follows_previous_revision() -> None:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["20261006_0006"]
    assert scripts.get_revision("head").down_revision == "20261005_0005"
