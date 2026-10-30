# Backup and restore

`scripts/backup.py` writes one `tar.gz` archive holding all state needed to resume the
economy:

- `services/db-gateway/data/economy.db` with `-wal` and `-shm` when present
- `data/keys`
- `services/task-board/data/assets`
- `data/agent-runtime`
- `services/court/.env`

The archive contains private keys and API keys. It is written with mode 0600; keep it
private.

## Backup

```bash
just backup /path/to/economy-2026-10-30.tar.gz
```

The stack is stopped first (`just stop-all`, or the supervisor when units are
installed) so the database and WAL are consistent. The command refuses to overwrite an
existing file and fails if the database is missing. It does not restart services:

```bash
just start-all
```

## Restore

```bash
just restore /path/to/economy-2026-10-30.tar.gz
just check-keys
just start-all
```

`restore` requires the explicit confirmation flag (the recipe passes `--yes`). It stops
the stack, deletes the current database, WAL, keys, assets, agent runtime state and
court `.env`, then extracts the archive. Stale `-wal`/`-shm` files are removed so they
never pair with the restored database. It does not start services; run `just check-keys`
and `just start-all` afterwards (`scripts/supervise.py start` when supervised).

Restoring into a fresh checkout works the same way, after `just init-all`.

Pass `--no-stop` to the script when the stack is already stopped.
