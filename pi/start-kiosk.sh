#!/bin/bash
# Raspberry Pi のデスクトップ起動時に呼ばれ、サイネージを全画面で表示する
PORT="${SIGNAGE_PORT:-8080}"
URL="${SIGNAGE_URL:-http://localhost:${PORT}/}"   # NAS等で動かす場合は SIGNAGE_URL=http://NASのIP:8080/

# サーバーの起動を待つ（最大90秒）
for i in $(seq 1 90); do
  python3 -c "import urllib.request,sys;urllib.request.urlopen('${URL}api/info',timeout=2)" 2>/dev/null && break
  sleep 1
done

# 画面の自動消灯を止める（X11 の場合）
xset s off -dpms s noblank 2>/dev/null || true

BROWSER="$(command -v chromium-browser || command -v chromium)"
PREF="$HOME/.config/chromium/Default/Preferences"

# Chromium が落ちても自動で開き直す
while true; do
  # 「前回正しく終了しませんでした」の表示を防ぐ
  [ -f "$PREF" ] && sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' "$PREF"
  "$BROWSER" --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
    --autoplay-policy=no-user-gesture-required --check-for-update-interval=31536000 \
    --disable-features=Translate --overscroll-history-navigation=0 --password-store=basic \
    --no-first-run "$URL"
  sleep 3
done
