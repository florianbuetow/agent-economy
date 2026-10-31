# Supervision

Supervision restarts a crashed service within seconds. On macOS it uses launchd
LaunchAgents, on Linux systemd user units. One unit exists per service (db-gateway,
identity, reputation, central-bank, task-board, court, ui), plus the task feeder and
the `mathbot` math worker.

## Commands

```bash
uv run --project agents scripts/supervise.py install     # write and load all units
uv run --project agents scripts/supervise.py status      # installed and running state
uv run --project agents scripts/supervise.py stop        # unload, keep unit files
uv run --project agents scripts/supervise.py start       # load installed unit files
uv run --project agents scripts/supervise.py uninstall   # unload and remove all units
```

`--no-load` writes or removes the unit files without calling `launchctl` or `systemctl`.
`--unit-dir` overrides the target directory (default `~/Library/LaunchAgents` or
`~/.config/systemd/user`). The command exits 1 if any unit fails.

## What is generated

- Each unit runs the same argv as the matching `_launch-*` recipe in the root
  `justfile` (`uv run uvicorn <app> --factory --host <host> --port <port>`), from
  `services/<name>`. `scripts/stop_agent.py` therefore keeps recognising the process.
- The bind host comes from `server.host` in each service `config.yaml`. The port comes
  from the `port_*` variables in the root `justfile`.
- launchd: `KeepAlive` with `SuccessfulExit=false`, `ThrottleInterval` 5 seconds.
  systemd: `Restart=on-failure`, `RestartSec=3`, and `After=`/`Wants=` ordering that
  starts db-gateway before identity and the rest.
- Logs go to `data/logs/supervised-<name>.out.log` and `.err.log`.
- Court secrets from `services/court/.env` are embedded in the Court plist
  (`EnvironmentVariables`) on macOS and referenced via `EnvironmentFile=` on Linux.
  Unit files are written with mode 0600. Re-run `install` after changing the `.env`.
- Unit files embed the absolute path of `uv` found on `PATH` at install time.

## Supervised mode changes stop and start

While units are installed, a supervisor restarts anything you stop by hand, so
`just stop-all` and `just start-all` fight the supervisor. In supervised mode:

- Stop everything with `scripts/supervise.py stop` (or `uninstall` to leave supervision).
- Start everything with `scripts/supervise.py start`.
- `just backup` and `just restore` detect installed units and use the supervisor.

## Recovery check

```bash
kill -9 "$(lsof -ti tcp:8003 -sTCP:LISTEN)"
sleep 10 && just status
```

Task Board should report healthy again within about 10 seconds.
