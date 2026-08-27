COMPOSE=docker compose

.PHONY: init up down logs browser-init collect migrate test lint format shell stats

init:
	cp -n .env.example .env || true
	cp -n config/searches.example.yaml config/searches.yaml || true
	cp -n config/scoring.example.yaml config/scoring.yaml || true
	mkdir -p data/browser-profile data/debug/screenshots data/debug/html

up:
	$(COMPOSE) up -d postgres

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

browser-init:
	$(COMPOSE) run --rm collector python -m app browser-init

collect:
	$(COMPOSE) run --rm -e HEADLESS=$${HEADLESS:-true} collector python -m app collect

migrate:
	$(COMPOSE) run --rm collector alembic upgrade head

test:
	pytest

lint:
	ruff check .
	mypy app collectors services

format:
	ruff format .
	ruff check --fix .

shell:
	$(COMPOSE) run --rm collector bash

stats:
	$(COMPOSE) run --rm collector python -m app stats
