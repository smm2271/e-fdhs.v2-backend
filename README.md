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
