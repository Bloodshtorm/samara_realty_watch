"""Host-side recovery for stale Docker service network namespaces after browser restart."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "compose.lan.yml")]


def main() -> None:
    containers = {}
    for service in ("browser-auth", "scheduler"):
        container_id = subprocess.check_output(
            [*COMPOSE, "ps", "-q", service], cwd=ROOT, text=True, timeout=15
        ).strip()
        if not container_id:
            return  # Respect an intentional stop, including deployments.
        containers[service] = json.loads(
            subprocess.check_output(["docker", "inspect", container_id], text=True, timeout=15)
        )[0]
    browser, scheduler = containers["browser-auth"], containers["scheduler"]
    if browser["State"].get("Health", {}).get("Status") != "healthy":
        return
    if not scheduler["State"]["Running"]:
        return
    namespaces = [os.readlink(f"/proc/{c['State']['Pid']}/ns/net") for c in (browser, scheduler)]
    if namespaces[0] == namespaces[1]:
        return
    # Do not interrupt a collector still persisting its previous search.
    lock = subprocess.run(["flock", "-n", str(ROOT / "data/collector.lock"), "true"], check=False)
    if lock.returncode:
        return
    print("Recovering scheduler: browser network namespace changed", flush=True)
    subprocess.run(
        [
            *COMPOSE,
            "--profile",
            "collect",
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "scheduler",
        ],
        cwd=ROOT,
        check=True,
        timeout=180,
    )


if __name__ == "__main__":
    main()
