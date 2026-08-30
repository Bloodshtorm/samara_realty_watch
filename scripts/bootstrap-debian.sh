#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f pyproject.toml ]]; then
  echo "Run this script from the repository root." >&2
  exit 1
fi

python_bin="${PYTHON_BIN:-python3}"
python_version="$("$python_bin" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
venv_pkg="python${python_version}-venv"

if ! "$python_bin" -m venv --help >/dev/null 2>&1 || ! "$python_bin" -m venv /tmp/samara-realty-venv-check >/dev/null 2>&1; then
  rm -rf /tmp/samara-realty-venv-check
  echo "Installing system prerequisites: ${venv_pkg}, make"
  sudo apt-get update
  sudo apt-get install -y "$venv_pkg" make
else
  rm -rf /tmp/samara-realty-venv-check
fi

cp -n .env.example .env
cp -n config/searches.example.yaml config/searches.yaml
cp -n config/scoring.example.yaml config/scoring.yaml
mkdir -p data/browser-profile data/debug/screenshots data/debug/html data/reports

if [[ ! -x .venv/bin/python || ! -f .venv/bin/activate || ! -x .venv/bin/pip ]]; then
  rm -rf .venv
  "$python_bin" -m venv .venv
fi
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"
python -m playwright install chromium

pytest
ruff check .
mypy app collectors services
