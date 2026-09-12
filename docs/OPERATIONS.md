# Samara Realty Watch Operations

Этот файл является единственным источником правды для операций с сервером, deploy-путей и команд. Если в README, AGENTS.md или skill есть расхождения, сначала обновляйте этот файл.

## Inventory

- Local workspace: `D:\dev\samara_realty_watch`
- Git remote: `git@github.com:Bloodshtorm/samara_realty_watch.git`
- Main branch: `main`
- Active feature branch: task-specific; read the current local branch before work.
- Deploy branch: task-specific; deploy the current feature branch to LAN for validation, then merge/push to `main` only after the user accepts the result.
- LAN server SSH host: `bs@192.168.0.246`
- LAN server alias: `lan-dev`
- Deploy path on LAN server: `/home/bs/soft/github/samara_realty_watch`
- Web UI: `http://192.168.0.246:8000/`
- noVNC URL: `http://192.168.0.246:6080/vnc.html`
- Browser CDP endpoint: `http://127.0.0.1:9222` inside the browser container network namespace only.

## Runtime

The LAN runtime uses `sudo docker compose -f compose.lan.yml` (project `samara-realty-lan`).
Docker Engine starts at boot; run Docker through sudo, not a newly granted docker group.
The old Python/systemd setup is retained only for rollback and must not run concurrently.

The LAN database is SQLite (`sqlite+aiosqlite`), stored at
`/home/bs/soft/github/samara_realty_watch/data/realty.sqlite3`.

- Python venv: `/home/bs/soft/github/samara_realty_watch/.venv`
- Web service: `samara-realty-web.service`
- Collector timer: `samara-realty-collector.timer`
- Collector service: `samara-realty-collector.service`
- Original rollback browser profile: `/home/bs/soft/github/samara_realty_watch/data/browser-profile`
- Container browser profile: `/home/bs/soft/github/samara_realty_watch/data/browser-profile-docker`
- Runtime config: `/home/bs/soft/github/samara_realty_watch/config/searches.yaml`
- Debug HTML: `/home/bs/soft/github/samara_realty_watch/data/debug/html`
- Debug screenshots: `/home/bs/soft/github/samara_realty_watch/data/debug/screenshots`

The original `docker-compose.yml` is the separate PostgreSQL/local workflow, not the LAN stack.
LAN services are `web`, `browser-auth`, `scheduler`; `collector` is one-shot only.
SQLite uses a directory bind mount `./data:/app/data`, including SQLite sidecar files.
The original `.env` is retained; Compose explicitly overrides host-only paths.
Config is read-only. Browser password and auth URLs are read-only mounts from
`/home/bs/.config/samara-realty-watch/`; never print or commit them.
The web UI requires app users. Bootstrap the first admin through `.env` with
`APP_ADMIN_USERNAME`, `APP_ADMIN_PASSWORD`, and optional `APP_ADMIN_DISPLAY_NAME`;
after the first user exists, manage users at `/admin/users`.

## SLA

This is a personal LAN service, not a public production system.

- Target availability: always reachable over LAN SSH and web UI.
- Collector cadence: every 3 hours via the container scheduler; a shared file lock prevents overlapping manual/scheduled runs.
- Web UI recovery target: restart service immediately after deploy or failure.
- Data safety: do not delete SQLite/PostgreSQL/runtime data unless the user explicitly asks.
- Browser sessions: preserve `data/browser-profile`; it contains the source-site login state.
- noVNC exposure: LAN-only, password-protected, and not published to the internet.

### Collector Network Recovery

The host timer `samara-realty-network-recovery.timer` checks every minute whether
scheduler still shares the running browser's network namespace. If Chrome's
container was restarted and namespaces differ, it recreates only scheduler after
the shared collector lock is released. It respects an intentionally stopped scheduler.
CDP remains loopback-only; no Docker socket is mounted into application containers.

Install/update from the deploy directory:

```bash
sudo install -m 644 scripts/samara-realty-network-recovery.service scripts/samara-realty-network-recovery.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now samara-realty-network-recovery.timer
```

Scheduler health checks heartbeat, CDP connectivity and collection freshness.
Whole-cycle/browser errors are written to `/runs` as system runs. Scheduled
collections use `--due-only` and respect each search's `interval_hours`; manual
collections omit that flag to force a controlled check. `empty_filtered` means
that parsed objects were rejected by domain rules, not a successful data refresh.
Page counters now record requests processed; rule exclusion counts are in logs.

### Avito Load Limits And Manual Recovery

Avito shares one persistent load policy across all searches, manual runs and
container restarts: `data/avito-policy.sqlite3` (not the listing database).
Use the shared collector flock for every invocation; do not launch concurrent
`python -m app collect` processes directly. No schema migration is required.

Initial conservative settings (not site-approved safe limits):

- `AVITO_DAILY_PAGES=60`: navigation attempts per rolling 24-hour window.
- `AVITO_PAGE_DELAY_SECONDS=60`: minimum gap, plus 0-30 seconds of scheduling jitter.
- `AVITO_BATCH_PAGES=10`: maximum pages per search per run.
- `AVITO_POLICY_PATH=data/avito-policy.sqlite3`: use the same persistent path in web/collector.

Budgets count navigations, including failures, not Chrome's subresource requests.
Single-page watch searches run before discovery searches. Discovery resumes from
its saved next page and wraps at the configured max_pages/end of results. Batches
are marked `partial`; they never deactivate unvisited listings. This trades discovery
and price freshness for lower load; reordered source pages can still cause gaps.
Existing saved-watch URLs get priority; automatic per-favorite detail checks are
not added. Search intervals gain 0-30 minutes of persisted jitter; the scheduler
checks them on its normal 3-hour tick, not at the exact timestamp shown.

CAPTCHA/login/401/403 pauses the whole source until manual verification. HTTP 429
honors Retry-After (seconds or HTTP date); network/5xx failures use persisted
1/2/4-hour exponential cooldown, then require manual review after three failures.
There are no immediate request retries. Cooldown never shortens Retry-After.
Markup/empty-first-page errors are not automatically labelled an IP ban.

At `/runs`, an admin can inspect the reason, budget and schedule. After manually
checking the existing Chrome through LAN noVNC, request a probe with the button.
The next scheduler cycle permits one page only, respects budget/cooldown, and
unpauses only after useful listings are ingested. A failed or interrupted probe
leaves the source paused. Requesting a probe does not restart Chrome or clear cookies.
For an earlier controlled probe after pressing the button, use the documented
one-shot collector command for a single existing Avito search (same flock).

Never remove the policy file to reset quotas or bypass a block. Other sources keep
running while Avito is paused. On deployment during a known active block, initialize
the policy with `AvitoPolicy(...).block(reason)` before starting scheduler.

### Derived Data Repair Procedure

For the September 12 geography/mortgage/coordinate fixes, stop scheduler and web
after any active collection finishes, then preview and apply:

```bash
.venv/bin/python -m scripts.repair_listing_quality data/realty.sqlite3
.venv/bin/python -m scripts.repair_listing_quality data/realty.sqlite3 --apply
```

Apply creates and verifies a SQLite backup under `data/backups/before-quality-*`.
Only derived district, feature and coordinate fields change; observations, prices,
user flags and records are preserved. Coordinates inferred from matching addresses
are tagged with `features.coordinates_inferred`. Ambiguous matches over 100 m apart
are excluded. Do not treat inferred or source coordinates as surveyed boundaries.
Land searches default to the server-checked radius; objects without usable
coordinates remain accessible under `Местоположение: Не подтверждено`.

## Normal Deploy

Use this path after code is committed and pushed. Deploy the current task branch, not a hard-coded branch. On the server, fetch and checkout the same branch that was pushed from the laptop.

```bash
BRANCH="$(git branch --show-current)"
git push origin "$BRANCH"
ssh bs@192.168.0.246 "
  cd /home/bs/soft/github/samara_realty_watch &&
  git fetch origin &&
  git checkout '$BRANCH' &&
  git pull --ff-only origin '$BRANCH' &&
  .venv/bin/python -m alembic upgrade head &&
  .venv/bin/python -m pytest &&
  .venv/bin/python -m ruff check . &&
  .venv/bin/python -m mypy app collectors services &&
  sudo docker compose -f compose.lan.yml build web browser-auth &&
  sudo docker compose -f compose.lan.yml stop scheduler &&
  sudo docker compose -f compose.lan.yml up -d --wait --force-recreate browser-auth web &&
  sudo docker compose -f compose.lan.yml --profile collect up -d --force-recreate scheduler &&
  sudo docker compose -f compose.lan.yml ps
"
```

Check the web UI:

```bash
ssh bs@192.168.0.246 "cd /home/bs/soft/github/samara_realty_watch && .venv/bin/python - <<'PY'
from urllib.request import urlopen
print(urlopen('http://192.168.0.246:8000/healthz', timeout=10).status)
PY"
```

## LAN Cutover And Recovery

Normal deploy above assumes authentication was accepted. During the first cutover or an
authentication incident, leave the `collect` profile stopped until live validation succeeds.
Scheduler and manual collector share the browser network namespace, so recreate them after
recreating the browser container. Never publish CDP (9222) or raw VNC (5900).

Install Docker using `bash scripts/install-docker-debian.sh` on Debian 13. Build and run the
test suite before stopping the old runtime. Then disable the old collector timer, wait for
the running collection to finish, stop the old web and browser services, and make verified
SQLite/profile/config backups under `data/backups/`. Copy the stopped browser profile to
`data/browser-profile-docker`, owned by UID/GID 1000. Do not downgrade Chrome or share one
profile between running browsers. The container supervisor holds an exclusive profile flock
and clears only stale Chrome SingletonLock/SingletonSocket/SingletonCookie files in that
dedicated copy before startup; never run it against the original host profile.
Chrome's sandbox remains enabled; `scripts/docker-seccomp.json`
is the unmodified Playwright v1.61.0 profile from
https://github.com/microsoft/playwright/blob/v1.61.0/utils/docker/seccomp_profile.json.

Start `browser-auth web` only and check `/healthz`, noVNC, data counts, old listing links,
and a controlled existing search for each enabled source. Reauthenticate through noVNC if
needed. Only after non-zero useful results enable the scheduler with the collect profile.
Cian uses the shared authenticated Chrome context, not a separate unauthenticated HTTP
client. CAPTCHA/HTTP access blocks and an empty first page are failed runs with debug
artifacts, not successful zero-result collections. Solve challenges manually in noVNC;
do not add automated challenge solving or evasion.
Disable old `samara-realty-web.service`, `samara-realty-browser-auth.service`, and
`samara-realty-collector.timer` autostart, retaining their unit files and venv for rollback.

Rollback: stop all LAN containers with `sudo docker compose -f compose.lan.yml --profile collect
--profile manual down` (never `--volumes`); re-enable/start the old browser, web and collector
timer. They use the original profile and the same SQLite. Do not overwrite newer database
changes with a backup unless corruption actually requires recovery.

Container logs rotate at 10 MiB with three files each. No secrets, databases, backups or
profiles are included in build context or image layers. `/healthz` returns 200/503 without
database error details. Check statuses and logs after every deploy:

```bash
sudo docker compose -f compose.lan.yml ps
sudo docker compose -f compose.lan.yml logs --tail=100 web browser-auth scheduler
sudo docker compose -f compose.lan.yml run --rm --no-deps collector --search <existing-search-name>
```

Manual collection uses the same lock as the scheduler and exits 75 if busy. The scheduler
runs immediately at startup, then every 10800 seconds from the prior start (no overlap).
Stop the scheduler and wait for active manual collectors before migrations/DB maintenance.
Use the existing verified database backup tooling; browser backups require a stopped browser.

## Legacy PostgreSQL Compose Deploy

Use Docker Compose only if the server is intentionally running compose services.

```bash
BRANCH="$(git branch --show-current)"
git push origin "$BRANCH"
ssh bs@192.168.0.246 "
  cd /home/bs/soft/github/samara_realty_watch &&
  git fetch origin &&
  git checkout '$BRANCH' &&
  git pull --ff-only origin '$BRANCH' &&
  docker compose build web scheduler collector &&
  docker compose up -d postgres web scheduler &&
  docker compose ps &&
  docker compose logs --tail=100 web scheduler
"
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

### Apartment Groups

Migration `0004_apartment_groups` adds groups without replacing source listings or history.
Back up SQLite and stop the web service and collector timer/service before upgrading.
The existing LAN SQLite schema predates Alembic tracking: if `alembic_version` is absent,
verify all existing tables/columns against the pre-group models, then stamp
`0003_search_contexts`. Never stamp an unknown or empty database. Then run:

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m app listing duplicates
.venv/bin/python -m app listing duplicates --apply
```

The command without `--apply` previews suggested groups without writing. It also works
before migration, using reflected listing columns. The preview is a fresh algorithmic
estimate, not a replay of manual decisions; `--apply` always respects saved rejections.
After successful collections, reconciliation runs automatically. It uses building/nearby
coordinate buckets and requires a complete match across every member before auto-merging.
Long generic descriptions are insufficient: text confirmation additionally requires
diverse text and apartment-specific measurements or an agency object reference.

`/duplicates` contains candidates and rejected pairs. `/apartments/<id>` compares source
values and price histories. Manual separation rejects every crossing pair; recollection
does not undo it. Price changes preserve membership; address/floor conflicts flag a group
for review and prevent adding more members automatically. Apartment flags apply across
sources; splitting inherits them. Existing `/listings/<id>` URLs remain source-specific.
No new image downloads, image blobs, or duplicated payload snapshots are introduced.

### MVP Storage Policy

- Keep full source JSON only on the current listing, not in observation history.
- Observations store time, price, active status and title. Save description snapshots only
  when the description changes; remove these snapshots after 7 days.
- At the start of each collection cycle, keep observations for 30 days plus the first and
  last observation for every listing/search pair. These boundary rows preserve the initial
  price and search-context membership even for sources that are no longer collected.
- Keep price changes, current listings, search contexts and user flags. Observation counts
  describe retained observations, not the lifetime number of collection events.
- Remove finished collector run records after 30 days; never prune runs marked `started`.
- SQLite reuses freed pages. Do not run a blocking `VACUUM` on each collection cycle.

For a one-time MVP reset of redundant history, stop the web service, timer and collector.
Then run from the deploy directory:

```bash
.venv/bin/python -m scripts.compact_database data/realty.sqlite3
.venv/bin/python -m scripts.compact_database data/realty.sqlite3 --apply
```

The first command is a dry run. `--apply` creates and verifies a compressed SQLite backup
next to the database (`realty.sqlite3.before-mvp-compact-<UTC timestamp>.gz`), keeps only
first/last observations per listing/search, removes historical JSON/description snapshots,
then runs `VACUUM`, integrity and foreign-key checks. Current listings, prices, searches,
user flags, browser profile and runtime config are preserved. Allow at least 3x the database
size in free disk space. Restart the web service and timer afterward, including on failure.

Run all enabled searches:

```bash
ssh bs@192.168.0.246 "cd /home/bs/soft/github/samara_realty_watch && sudo docker compose -f compose.lan.yml run --rm --no-deps collector"
```

Run one source:

```bash
ssh bs@192.168.0.246 "cd /home/bs/soft/github/samara_realty_watch && sudo docker compose -f compose.lan.yml run --rm --no-deps collector --source avito"
```

Run one named search:

```bash
ssh bs@192.168.0.246 "cd /home/bs/soft/github/samara_realty_watch && sudo docker compose -f compose.lan.yml run --rm --no-deps collector --search avito_samara_dacha_watch_8250141503"
```

Check collector history in the web UI:

- `http://192.168.0.246:8000/runs`

Or from the server:

```bash
ssh bs@192.168.0.246 "cd /home/bs/soft/github/samara_realty_watch && sudo docker compose -f compose.lan.yml logs --tail=200 scheduler"
```

## Legacy Host Browser Auth (Rollback Only)

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
