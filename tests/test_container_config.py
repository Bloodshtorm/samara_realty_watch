import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_lan_compose_preserves_data_and_isolates_browser():
    config = yaml.safe_load((ROOT / "compose.lan.yml").read_text(encoding="utf-8"))
    services = config["services"]
    assert set(services) == {"web", "browser-auth", "scheduler", "collector"}
    for service in services.values():
        assert service["user"] == "1000:1000"
        assert not service.get("privileged")
        assert service["logging"]["options"] == {"max-size": "10m", "max-file": "3"}
        assert not any(":9222" in port or ":5900" in port for port in service.get("ports", []))
    for name in ("web", "scheduler", "collector"):
        service = services[name]
        assert (
            service["environment"]["DATABASE_URL"] == "sqlite+aiosqlite:////app/data/realty.sqlite3"
        )
        assert "./data:/app/data" in service["volumes"]
        assert "./config:/app/config:ro" in service["volumes"]
    for name in ("scheduler", "collector"):
        assert services[name]["network_mode"] == "service:browser-auth"
        assert services[name]["profiles"]
    assert services["browser-auth"]["security_opt"] == ["seccomp=./scripts/docker-seccomp.json"]
    assert "./data/browser-profile-docker:/browser-profile" in services["browser-auth"]["volumes"]


def test_runtime_files_are_excluded_from_image_context():
    excluded = (ROOT / ".dockerignore").read_text().splitlines()
    for pattern in ("data/", ".env*", "config/searches.yaml", "config/scoring.yaml"):
        assert pattern in excluded
    dockerfile = (ROOT / "Dockerfile.lan").read_text()
    assert "COPY . " not in dockerfile
    assert "--no-sandbox" not in (ROOT / "scripts/container-browser.sh").read_text()
    profile = json.loads((ROOT / "scripts/docker-seccomp.json").read_text())
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
