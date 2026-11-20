#!/usr/bin/env bash
set -euo pipefail

APK_PATH="${1:-}"
PACKAGE="com.zulong.drc.mi"
ACTIVITY="com.epicgames.ue4.SplashActivity"
LOG_DIR="${LOG_DIR:-logs}"

if [[ -z "$APK_PATH" || ! -f "$APK_PATH" ]]; then
  echo "Usage: $0 /path/to/dragon.apk" >&2
  exit 2
fi

if ! command -v adb >/dev/null 2>&1; then
  echo "adb not found. Install Android Platform Tools first." >&2
  exit 2
fi

mkdir -p "$LOG_DIR"

echo "[1/5] Connected devices"
adb devices -l

echo "[2/5] APK package and ABI pre-check"
python3 "$(dirname "$0")/inspect_apk.py" "$APK_PATH"

echo "[3/5] Install APK"
adb install -r "$APK_PATH"

echo "[4/5] Launch game and capture logs"
adb logcat -c
adb shell am force-stop "$PACKAGE" || true
adb shell am start -n "$PACKAGE/$ACTIVITY"
sleep "${STARTUP_WAIT_SECONDS:-45}"
adb logcat -d -v threadtime > "$LOG_DIR/dragon-logcat.txt"

echo "[5/5] Process state"
adb shell pidof "$PACKAGE" || true
adb shell dumpsys activity activities | grep -E "$PACKAGE|mResumedActivity" | tail -n 20 || true

echo "Logs saved to: $LOG_DIR/dragon-logcat.txt"
echo "If the game exits, inspect UE4, linker, graphics, texture and network errors in the log."
