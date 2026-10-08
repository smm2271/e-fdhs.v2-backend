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

## Broadcast HTTP API

All endpoints use the existing `__Host-session` HttpOnly cookie dependency.
Angular calls these endpoints through `/api/broadcasts...`; its proxy strips
`/api` so the `/broadcasts` page route remains an Angular route. Production
hosting must apply the same mapping and serve the Angular index for page routes.
Requests never accept an account or position as authentication evidence.
Students may omit `group_id`; it is derived from their session. A different
student group is rejected. Teachers must specify `group_id` for reply reads/writes.

| Method | Endpoint | Request |
| --- | --- | --- |
| GET | `/broadcasts` | Optional `group_id`, `week=YYYY-MM-DD`, `history=true`, `limit` (1–500), `offset` |
| POST | `/broadcasts` | `{content, target_group_ids: UUID[]}` |
| GET | `/broadcasts/pending-count` | Current Taipei display week only |
| GET | `/broadcasts/{id}` | Optional `group_id` |
| POST | `/broadcasts/{id}/confirmations` | `{group_id?: UUID}` (students can send `{}`) |
| GET | `/broadcasts/{id}/replies` | Optional `group_id` (required for teachers) |
| POST | `/broadcasts/{id}/replies` | `{content, group_id?: UUID, ref_id?: UUID}` |

The feed returns `{items, current_user, pending_count}`. Each broadcast includes
its sender, UTC timestamps with a timezone suffix, content, deadline, and visible
`targets`. Each target includes its group, dynamic confirmation state, confirmer
name/position/time, reply count, recursively nested replies, and `can_confirm` /
`can_reply`. Mutations return the updated broadcast scoped to the affected target.
`GET .../replies` returns that target's recursive reply tree. Content must be
nonblank and at most 20,000 characters. OpenAPI schemas are available at `/docs`.

The feed defaults to broadcasts **published** during the current Taipei
Monday–Sunday week; `week` selects another display week, and `history=true`
removes the publication-date filter. The badge always counts this week's
unconfirmed targets for a student with confirmation permission, independent of
pagination and confirmation deadlines. Historical broadcasts remain readable,
confirmable, and replyable through their ID.

Expected service errors map to 403 (permission), 404 (missing/invisible target),
409 (duplicate confirmation / constraint conflict), or 422 (invalid input).
Repeating a confirmation returns 409 and preserves the original actor/time.

Migration `20261008_0007` adds a composite parent-reply foreign key and a
confirmation-to-target foreign key. It does not rewrite existing records;
invalid legacy references cause the transactional upgrade to fail. Apply with
`alembic upgrade head`. Downgrade removes these added constraints only.

`tests/test_broadcast_routes.py` tests actual login cookies and PostgreSQL through
ASGI HTTP requests. These tests do not replace a browser acceptance check.
