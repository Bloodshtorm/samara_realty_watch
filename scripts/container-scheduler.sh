#!/usr/bin/env bash
set -euo pipefail
child=""
heartbeat=""
cleanup() {
  trap - TERM INT EXIT
  [[ -z "$child" ]] || kill -TERM "$child" 2>/dev/null || true
  [[ -z "$heartbeat" ]] || kill -TERM "$heartbeat" 2>/dev/null || true
  wait || true
}
trap 'cleanup; exit 0' TERM INT
trap cleanup EXIT
while true; do touch /tmp/scheduler-heartbeat; sleep 30; done &
heartbeat=$!
while true; do
  started=$SECONDS
  bash /app/scripts/container-collect.sh --due-only &
  child=$!
  result=0
  wait "$child" || result=$?
  child=""
  echo "Collection finished with exit code $result"
  delay=$(( ${COLLECT_INTERVAL_SECONDS:-10800} - (SECONDS - started) ))
  (( delay > 0 )) || delay=1
  sleep "$delay" &
  child=$!
  wait "$child" || true
  child=""
done
