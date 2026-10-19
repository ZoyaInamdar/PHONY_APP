#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

echo "=== [1/4] Cleaning stale VM locks ==="
rm -f /Users/zoya/vega/sdk/vega-sdk/main/0.24.12112/vvd/instances/*/*.lock 2>/dev/null || true

echo "=== [2/4] Ensuring Vega Virtual Device is running ==="
if ! ps -ef | grep -E "vega-virtual-device" | grep -v grep >/dev/null 2>&1; then
  echo "Virtual device not running. Starting detached instance..."
  nohup /Users/zoya/vega/bin/vega virtual-device start --timeoutSeconds 180 >/tmp/vvd_start.log 2>&1 &
  sleep 5
  echo "Waiting for device to register..."
  for i in {1..30}; do
    if /Users/zoya/vega/bin/vega device list 2>/dev/null | grep -q "VirtualDevice"; then
      echo "VirtualDevice is online!"
      break
    fi
    sleep 2
  done
else
  echo "VirtualDevice is already running."
fi

echo "=== [3/4] Building Debug Package ==="
npm run build:debug

echo "=== [4/4] Deploying & Launching on VirtualDevice ==="
/Users/zoya/vega/bin/vega run-app build/private/kepler/@amazon-devices/triggerawareplayer/undefined/vega/aarch64/Debug/@amazon-devices/triggerawareplayer_aarch64.vpkg com.hackathon2026.triggerawareplayer.main --deviceId VirtualDevice

echo "=== Done! App is running on VirtualDevice ==="
