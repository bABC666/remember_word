# Linux private staging on Alibaba Cloud Linux 3

This is the first deployment stage for an empty database. The app listens on
`127.0.0.1:8000` and is reached through an SSH tunnel. Do not open port 8000 in
the ECS security group. Public access requires a domain, filing, HTTPS, verified
daily/off-site backups, and the remaining Phase 2.9/4 acceptance work.

## Layout

- Code and built frontend: `/opt/shici/current/`
- Python virtual environment: `/opt/shici/current/.venv/`
- Mutable data: `/var/lib/shici/`
- Service environment: `/etc/shici/shici.env` (root-owned, mode 0600)

Package only `backend/app`, `backend/alembic`, `backend/alembic.ini`,
`backend/pyproject.toml`, `frontend/dist`, and `deploy`. Never package `.env`,
`data`, Python virtual environments, `node_modules`, or local test artifacts.
The frontend must be built before packaging with `npm ci && npm run build`.

## First installation (after uploading the package)

Install `python3.11` from the Alibaba Cloud Linux 3 repository. Do not replace
the system `python3` executable. Use a non-root `shici` service account and give
it ownership of `/opt/shici/current` and `/var/lib/shici`; keep `/etc/shici`
root-owned. Create the virtual environment and install only runtime backend
dependencies:

```sh
python3.11 -m venv /opt/shici/current/.venv
/opt/shici/current/.venv/bin/python -m pip install -e /opt/shici/current/backend
```

This minimal install leaves OCR disabled. To run image recognition on this
server's CPU, follow [the OCR installation and verification guide](OCR.md).

Copy `deploy/shici.env.example` to `/etc/shici/shici.env`. Set a DeepSeek key
there if AI features are needed. The empty database is initialized **once**, to
the exact revision in this package. For the current package that revision is
`0016_dictionary_extraction`; check the Alembic heads again before running:

```sh
cd /opt/shici/current/backend
sudo -u shici env VOCAB_DATA_DIR=/var/lib/shici VOCAB_DATABASE_PATH=/var/lib/shici/vocab.db \
  /opt/shici/current/.venv/bin/python -m alembic heads
sudo -u shici env VOCAB_DATA_DIR=/var/lib/shici VOCAB_DATABASE_PATH=/var/lib/shici/vocab.db \
  /opt/shici/current/.venv/bin/python -m alembic upgrade 0016_dictionary_extraction
```

This command is only for the new empty staging database described above; existing databases require their own backup and rehearsed migration plan.

The migration creates an `admin` account with an unusable password. Set its
password interactively; never put a password on the command line:

```sh
cd /opt/shici/current/backend
sudo -u shici env VOCAB_DATA_DIR=/var/lib/shici VOCAB_DATABASE_PATH=/var/lib/shici/vocab.db \
  /opt/shici/current/.venv/bin/python -m app.cli set-password admin
```

Install `deploy/shici.service` as `/etc/systemd/system/shici.service`, then
`systemctl daemon-reload`, `systemctl enable --now shici`, and check
`curl -fsS http://127.0.0.1:8000/api/health` **on the server**. Inspect
`journalctl -u shici` if startup fails. The application refuses to start when
the database revision does not match its code.

For private browser testing, forward a local port to server loopback with SSH
or Workbench, then visit `http://127.0.0.1:8000` on the local machine. Do not
switch `VOCAB_COOKIE_SECURE` to `true` until access is via HTTPS.

## Before public launch

Complete the Phase 4 tasks in `docs/PROJECT_ROADMAP.md`: verified daily
SQLite online backups, an off-site copy, a restore rehearsal, a domain and
filing for this mainland ECS, HTTPS reverse proxy, secure cookies, approved
public word source/import, and two-user acceptance. Changing code or database
revision requires a rehearsed migration; never run an unplanned `upgrade head`
against an existing database.
