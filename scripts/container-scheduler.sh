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
next_scheduled=0
while true; do
  mode=--requested-only
  if (( SECONDS >= next_scheduled )); then
    mode=--due-only
    next_scheduled=$(( SECONDS + ${COLLECT_INTERVAL_SECONDS:-10800} ))
  fi
  bash /app/scripts/container-collect.sh "$mode" &
  child=$!
  result=0
  wait "$child" || result=$?
  child=""
  if [[ "$mode" == --due-only ]] || (( result != 0 )); then
    echo "Collection $mode finished with exit code $result"
  fi
  sleep 10 &
  child=$!
  wait "$child" || true
  child=""
done
