# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python 3.12 personal realty monitoring service for Samara apartment listings.

- `app/` contains CLI entrypoints, FastAPI web UI, Jinja templates, settings, logging, DB setup, SQLAlchemy models, and the collection runner.
- `collectors/` contains source adapters. `yandex_realty.py` has the first implemented path; `domclick.py`, `cian.py`, and `avito.py` are safe adapter skeletons.
- `services/` contains domain logic: ingestion, normalization, scoring, mortgage math, deduplication, stats, reporting, and debug artifacts.
- `migrations/` contains Alembic migrations.
- `tests/` contains unit tests and `tests/fixtures/` contains saved HTML fixtures.
- `config/` contains example YAML files. Runtime copies are `config/searches.yaml` and `config/scoring.yaml`.
- `data/` stores local browser profile and debug dumps; do not commit runtime data.

## Build, Test, and Development Commands

- `make init` creates local `.env`, YAML configs, and data directories.
- `make up` starts PostgreSQL.
- `make migrate` applies Alembic migrations.
- `make browser-init` opens persistent Chromium for manual login.
- `make collect` runs one collection cycle.
- `make web` starts the local FastAPI web UI on `http://localhost:8000`.
- `make scheduler` starts the Docker Compose scheduler that collects every 2 hours.
- `make stats` prints current market statistics.
- `make report` writes a static HTML report to `data/reports/index.html`.
- `make test` runs pytest.
- `make lint` runs Ruff and mypy.
- `make format` formats and fixes Ruff issues.

When Docker image download is slow, use a temporary Python container for non-browser tasks.

## Coding Style & Naming Conventions

Use 4-space indentation, type hints, and small focused modules. Prefer async SQLAlchemy APIs for DB work. Keep source names stable: `yandex_realty`, `domclick`, `cian`, `avito`. Use `snake_case` for functions, variables, config keys, and CLI options. Run `ruff format` before committing.

## Testing Guidelines

Tests use `pytest` and `pytest-asyncio`. Name tests as `tests/test_*.py`. Do not hit live realty sites in tests; use saved HTML under `tests/fixtures/`. Cover normalization, mortgage formulas, deduplication, scoring, idempotent ingestion, and price history behavior.

## Commit & Pull Request Guidelines

No established git history is available yet, so use concise imperative commit messages, for example `Add yandex fixture parser` or `Fix price history upsert`.

Pull requests should include a short summary, test results, affected commands, and any config or migration notes. Include screenshots or saved debug HTML only when needed, and never include `.env`, cookies, browser profiles, or seller phone data.

## Security & Source Handling

Do not implement CAPTCHA bypass, fingerprint spoofing, proxy rotation, or rate-limit evasion. If a source blocks access or requires login, save debug HTML/screenshot, log a structured error, and continue other sources.
