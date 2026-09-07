# Samara Realty Watch Operations

Этот файл является единственным источником правды для операций с сервером, deploy-путей и команд. Если в README, AGENTS.md или skill есть расхождения, сначала обновляйте этот файл.

## Inventory

- Local workspace: `D:\dev\samara_realty_watch`
- Git remote: `git@github.com:Bloodshtorm/samara_realty_watch.git`
- Main branch: `main`
- Active feature branch: `feature/search-contexts-map-timeline`
- LAN server SSH host: `bs@192.168.0.246`
- LAN server alias: `lan-dev`
- Deploy path on LAN server: `/home/bs/soft/github/samara_realty_watch`
- Web UI: `http://192.168.0.246:8000/`
- noVNC URL: `http://192.168.0.246:6080/vnc.html`
- Browser CDP endpoint on server: `http://127.0.0.1:9222`

## Runtime

The project currently runs directly from a Python virtual environment on `lan-dev`.

- Python venv: `/home/bs/soft/github/samara_realty_watch/.venv`
- Web service: `samara-realty-web.service`
- Collector timer: `samara-realty-collector.timer`
- Collector service: `samara-realty-collector.service`
- Browser profile: `/home/bs/soft/github/samara_realty_watch/data/browser-profile`
- Runtime config: `/home/bs/soft/github/samara_realty_watch/config/searches.yaml`
- Debug HTML: `/home/bs/soft/github/samara_realty_watch/data/debug/html`
- Debug screenshots: `/home/bs/soft/github/samara_realty_watch/data/debug/screenshots`

Docker Compose remains supported for local/container workflows, but the LAN server deploy process below uses git plus user systemd unless this file is updated.

## SLA

This is a personal LAN service, not a public production system.

- Target availability: always reachable over LAN SSH and web UI.
- Collector cadence: every 2 hours via user systemd timer.
- Web UI recovery target: restart service immediately after deploy or failure.
- Data safety: do not delete SQLite/PostgreSQL/runtime data unless the user explicitly asks.
- Browser sessions: preserve `data/browser-profile`; it contains the source-site login state.
- noVNC exposure: LAN-only, password-protected, and not published to the internet.

## Normal Deploy

Use this path after code is committed and pushed.

```bash
ssh bs@192.168.0.246
cd /home/bs/soft/github/samara_realty_watch
git pull --ff-only
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy app collectors services
systemctl --user restart samara-realty-web.service
systemctl --user status samara-realty-web.service --no-pager
```

Check the web UI:

```bash
ssh bs@192.168.0.246 "cd /home/bs/soft/github/samara_realty_watch && .venv/bin/python - <<'PY'
from urllib.request import urlopen
print(urlopen('http://127.0.0.1:8000/', timeout=10).status)
PY"
```

## Docker Compose Deploy

Use Docker Compose only if the server is intentionally running compose services.

```bash
ssh bs@192.168.0.246
cd /home/bs/soft/github/samara_realty_watch
git pull --ff-only
docker compose build web scheduler collector
docker compose up -d postgres web scheduler
docker compose ps
docker compose logs --tail=100 web scheduler
```

## Development Workflow

Preferred flow from the laptop:

```bash
cd D:\dev\samara_realty_watch
git status --short
pytest
ruff check .
mypy app collectors services
git add -A
git commit -m "Short imperative message"
git push origin <branch>
```

Then deploy from git on `lan-dev` using the normal deploy section. Avoid direct `scp` deploys except for emergency debugging; if `scp` is used, commit and push the same change promptly.

## Collector Commands

Run all enabled searches:

```bash
ssh bs@192.168.0.246 "cd /home/bs/soft/github/samara_realty_watch && .venv/bin/python -m app collect"
```

Run one source:

```bash
ssh bs@192.168.0.246 "cd /home/bs/soft/github/samara_realty_watch && .venv/bin/python -m app collect --source avito"
```

Run one named search:

```bash
ssh bs@192.168.0.246 "cd /home/bs/soft/github/samara_realty_watch && .venv/bin/python -m app collect --search avito_samara_dacha_watch_8250141503"
```

Check collector history in the web UI:

- `http://192.168.0.246:8000/runs`

Or from the server:

```bash
ssh bs@192.168.0.246 "journalctl --user -u samara-realty-collector.service -n 200 --no-pager"
```

## Browser Auth And CDP

For Avito, Cian, Domclick and other sources with bot checks or login state:

1. Use the existing persistent browser profile.
2. Start noVNC/browser auth only inside LAN.
3. Log in manually through noVNC when needed.
4. Run a live collect for the source/search.
5. Enable a source/search in scheduler only after a live collect returns a non-zero useful result.

Start noVNC/browser auth:

```bash
ssh bs@192.168.0.246
cd /home/bs/soft/github/samara_realty_watch
NOVNC_LISTEN_HOST=0.0.0.0 NOVNC_PASSWORD='change-me' bash scripts/browser-auth-novnc.sh
```

If a browser is already open with CDP, collectors should use the configured CDP endpoint instead of starting a separate isolated browser session:

```env
BROWSER_CDP_URL=http://127.0.0.1:9222
```

Do not delete `data/browser-profile` while diagnosing auth unless the user explicitly accepts losing sessions.

## Keep Server Awake

The LAN server should stay reachable over SSH.

```bash
ssh bs@192.168.0.246
cd /home/bs/soft/github/samara_realty_watch
bash scripts/prevent-sleep-system.sh
sudo loginctl enable-linger bs
loginctl show-user bs -p Linger
```

## Safety Rules

- Do not run destructive commands against runtime data without explicit user approval.
- Before DB cleanup, create a backup and tell the user the backup path.
- Do not guess deploy host, deploy path, service names, or noVNC exposure settings; read them from this file.
- Keep secrets and browser sessions out of examples. The repository is private, but operational docs should still avoid unnecessary secret sprawl.
- Do not implement CAPTCHA bypass, proxy rotation, fingerprint spoofing, or rate-limit evasion.
