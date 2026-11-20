#!/usr/bin/env bash
set -euo pipefail
mkdir -p output
printf 'Device information\n' > output/device-info.txt
adb devices -l | tee -a output/device-info.txt
for prop in ro.product.cpu.abi ro.product.cpu.abilist ro.dalvik.vm.native.bridge ro.build.version.sdk ro.product.model; do
  printf '%s: ' "$prop" | tee -a output/device-info.txt
  adb shell getprop "$prop" | tr -d '\r' | tee -a output/device-info.txt
done
echo "Install APK" | tee output/test-progress.txt
if ! adb install -r work/dragon.apk > output/install.txt 2>&1; then
  cat output/install.txt
  echo "APK INSTALL FAILED" | tee -a output/test-progress.txt
  exit 1
fi
cat output/install.txt
adb logcat -c || true
echo "Start game" | tee -a output/test-progress.txt
if ! adb shell monkey -p com.zulong.drc.mi -c android.intent.category.LAUNCHER 1 > output/launch.txt 2>&1; then
  cat output/launch.txt
  echo "APK LAUNCH COMMAND FAILED" | tee -a output/test-progress.txt
  exit 1
fi
cat output/launch.txt
WAIT_MINUTES="${WAIT_MINUTES_INPUT:-8}"
if ! [[ "$WAIT_MINUTES" =~ ^[0-9]+$ ]] || [ "$WAIT_MINUTES" -lt 1 ] || [ "$WAIT_MINUTES" -gt 20 ]; then
  WAIT_MINUTES=8
fi
for i in $(seq 1 "$WAIT_MINUTES"); do
  sleep 60
  echo "minute=$i" | tee -a output/test-progress.txt
  adb shell pidof com.zulong.drc.mi > "output/process-minute-$i.txt" || true
  adb exec-out screencap -p > "output/screen-minute-$i.png" || true
done
echo "RUNTIME WATCH COMPLETE" | tee -a output/test-progress.txt
