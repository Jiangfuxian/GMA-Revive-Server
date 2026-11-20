#!/usr/bin/env bash
set -euo pipefail
mkdir -p output
collect_live_diagnostics() {
  local last_code="$?"
  set +e
  echo "Script exit code: $last_code" >> output/test-progress.txt
  timeout 20s adb devices -l > output/adb-final-live.txt 2>&1 || true
  timeout 25s adb logcat -d -v threadtime 2>&1 | tail -n 25000 > output/logcat-while-live.txt || true
  timeout 20s adb shell dumpsys activity activities > output/activity-while-live.txt 2>&1 || true
  timeout 20s adb shell dumpsys activity processes > output/processes-while-live.txt 2>&1 || true
  timeout 20s adb shell dumpsys window windows > output/windows-while-live.txt 2>&1 || true
  timeout 20s adb shell dumpsys connectivity > output/network-while-live.txt 2>&1 || true
  timeout 20s adb shell 'for p in /sdcard/Android/data/com.zulong.drc.mi /sdcard/Android/obb/com.zulong.drc.mi /sdcard/Android/data/com.zulong.drc.mi/files; do echo "=== $p"; ls -la "$p" 2>&1 | head -40; du -ak "$p" 2>&1 | sort -n | tail -100; done' > output/resource-inventory-while-live.txt 2>&1 || true
  timeout 20s adb exec-out screencap -p > output/final-before-shutdown.png 2>/dev/null || true
}
trap collect_live_diagnostics EXIT
printf 'Device information\n' > output/device-info.txt
adb devices -l | tee -a output/device-info.txt
for prop in ro.product.cpu.abi ro.product.cpu.abilist ro.dalvik.vm.native.bridge ro.build.version.sdk ro.product.model; do
  printf '%s: ' "$prop" | tee -a output/device-info.txt
  adb shell getprop "$prop" | tr -d '\r' | tee -a output/device-info.txt
done
echo "Graphics capability probe" | tee output/graphics-capability.txt
adb shell getprop ro.opengles.version | tee -a output/graphics-capability.txt
adb shell dumpsys SurfaceFlinger 2>&1 | grep -Ei 'GLES|OpenGL|renderer|version' | head -25 | tee -a output/graphics-capability.txt || true
echo "Install APK" | tee output/test-progress.txt
if ! adb install -r work/dragon.apk > output/install.txt 2>&1; then
  cat output/install.txt
  echo "APK INSTALL FAILED" | tee -a output/test-progress.txt
  exit 1
fi
cat output/install.txt
adb logcat -c || true
adb shell settings put secure immersive_mode_confirmations confirmed || true
echo "Start game" | tee -a output/test-progress.txt
if ! adb shell monkey -p com.zulong.drc.mi -c android.intent.category.LAUNCHER 1 > output/launch.txt 2>&1; then
  cat output/launch.txt
  echo "APK LAUNCH COMMAND FAILED" | tee -a output/test-progress.txt
  exit 1
fi
cat output/launch.txt
sleep 8
adb exec-out screencap -p > output/screen-before-agreement.png || true
# In run 44741406228, the previous tap (1255,430) opened the "User Agreement"
# hyperlink and trapped the test in an in-app WebView. Do not click it.
# The consent popup's actual Accept-button center is (1140,692) on 1920x1080.
if [ "${ACCEPT_GAME_TERMS:-false}" != "true" ]; then
  echo "BLOCKED: explicit approval for the first-launch agreement was not provided" | tee -a output/test-progress.txt
  exit 0
fi
echo "User-approved acceptance: tap (1140,692) without opening policy links" | tee -a output/test-progress.txt
adb shell input tap 1140 692
sleep 30
adb exec-out screencap -p > output/screen-after-agreement.png || true
timeout 15s adb shell uiautomator dump /sdcard/window.xml > output/ui-dump-status.txt 2>&1 || true
timeout 15s adb exec-out cat /sdcard/window.xml > output/ui-after-agreement.xml 2>/dev/null || true
timeout 20s adb shell dumpsys activity activities > output/activity-after-agreement.txt 2>&1 || true
WAIT_MINUTES="${WAIT_MINUTES_INPUT:-5}"
if ! [[ "$WAIT_MINUTES" =~ ^[0-9]+$ ]] || [ "$WAIT_MINUTES" -lt 1 ] || [ "$WAIT_MINUTES" -gt 20 ]; then
  WAIT_MINUTES=5
fi
for i in $(seq 1 "$WAIT_MINUTES"); do
  sleep 60
  echo "minute=$i" | tee -a output/test-progress.txt
  adb shell pidof com.zulong.drc.mi > "output/process-minute-$i.txt" || true
  timeout 10s adb shell dumpsys activity processes | grep -A12 -B5 "com.zulong.drc.mi" > "output/process-state-minute-$i.txt" || true
  adb exec-out screencap -p > "output/screen-minute-$i.png" || true
done
echo "RUNTIME WATCH COMPLETE" | tee -a output/test-progress.txt
