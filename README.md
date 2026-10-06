# Backend development

Apply the database schema before running the seed command:

```powershell
alembic upgrade head
python -m database.seed
```

The default command only creates authorization master data. To also create the
fixed local-development login accounts, set `SEED_DEV_PASSWORD` and run:

```powershell
$env:SEED_DEV_PASSWORD = "a-local-development-password"
python -m database.seed --dev-account
```

This creates student account `320` (position `一般學生`) and teacher account
`teacher`. The password is only for local development; do not commit it. Seed
commands only create missing records and never update or delete existing data.

## Broadcast domain services

All public operations in `database.service` require the authenticated `account_id`.
Callers must obtain it from authentication, rather than accepting an arbitrary
account ID from a client. Services load the account and apply group scope.
Like existing services, successful writes commit the session; use a dedicated
session per operation. Constraint failures roll back and raise `ConflictError`.

| Service | Public methods | Domain rules |
| --- | --- | --- |
| `BroadcastService` | `create`, `get`, `list` | Teacher-only publishing; at least one existing `class` group; deduplicated targets in one transaction; teacher reads all, student reads own targets; optional group filter |
| `BroadcastConfirmationService` | `confirm`, `get`, `status` | Target-group students with confirmation permission; composite primary key preserves the first actor/time, including concurrent attempts; derived status |
| `ReplyService` | `create`, `get`, `list_thread` | Author or permitted target students write; teachers read all threads, students read own group; parent must share broadcast and group |

`list_thread` returns all nodes of one target tree ordered by creation time and
ID, with `ref_id` linking children to parents. There are no edit/delete methods.
Replies and confirmations have independent effects and remain available for
historical broadcasts. `list` is a historical broadcast query, not a weekly
workload calculation; no weekly settlement fields or automatic cleanup exist.

`database.broadcast_cycle` provides `confirmation_day`, `confirmation_deadline`,
and `confirmation_state`. Before 15:00 Taipei, responsibility falls on the same
calendar day; at/after 15:00 it falls on the next day, including weekends.
The deadline is 16:30 Taipei. Naive timestamps represent UTC throughout.
An optional constructor `clock` supports deterministic service tests.
Confirmation may occur immediately or after the deadline. Status is computed
as `pending`, `overdue`, `confirmed_on_time`, or `confirmed_late`; equality with
the deadline counts as on time.

`database.permissions.Permission` defines `CONFIRM_BROADCAST = 1` and
`REPLY_BROADCAST = 2`. Authorization uses the existing Position / Role / active
Override calculation, with deny taking precedence. New seed records give both
bits to 資訊股長 and 班代表, and zero to 一般學生 and 教師. Existing positions are
preserved by seed and require explicit permission provisioning if they still
have zero permissions. No publish bit or other-position password feature is added.

Migration `20261006_0006` adds required `replies.group_id` and replaces the direct
broadcast FK with a composite FK to `broadcast_targets`. Upgrade locks the
affected tables and backfills legacy replies only when there is exactly one
target. Zero/multiple-target legacy replies abort upgrade transactionally and
require a separately reviewed historical ownership mapping; the migration
never guesses ownership from the author. Downgrade preserves replies but removes
their group scope and restores the original FK. Re-upgrading after a downgrade
can therefore require manual mapping if replies belong to multiple-target broadcasts.

Run `python -m pytest -q` with the `TEST_DB_*` variables configured for a disposable
PostgreSQL test database. Integration tests migrate and truncate that database.
