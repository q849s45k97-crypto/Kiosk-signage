#!/bin/bash
# Kiosk Signage – Raspberry Pi セットアップ
# 使い方:  bash pi/setup.sh        （sudo を付けずに実行してください）
set -e
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
PORT="${SIGNAGE_PORT:-8080}"

if [ "$USER_NAME" = "root" ]; then
  echo "sudo を付けずに実行してください:  bash pi/setup.sh"; exit 1
fi

echo "== 機種 =="; cat /proc/device-tree/model 2>/dev/null; echo
echo "== OS ==";  . /etc/os-release && echo "$PRETTY_NAME"

command -v python3 >/dev/null || { echo "python3 が見つかりません"; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,7)' || { echo "Python 3.7 以上が必要です"; exit 1; }

if ! command -v chromium-browser >/dev/null && ! command -v chromium >/dev/null; then
  echo "== Chromium をインストールします =="
  sudo apt-get update
  sudo apt-get install -y chromium-browser || sudo apt-get install -y chromium
fi

echo "== サーバーを自動起動に登録 =="
sudo tee /etc/systemd/system/kiosk-signage.service >/dev/null <<UNIT
[Unit]
Description=Kiosk Signage server
After=network.target

[Service]
User=${USER_NAME}
Environment=SIGNAGE_PORT=${PORT}
ExecStart=/usr/bin/python3 ${APP_DIR}/pi/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now kiosk-signage

echo "== 全画面表示を自動起動に登録 =="
chmod +x "${APP_DIR}/pi/start-kiosk.sh"
mkdir -p "$HOME/.config/autostart"
cat > "$HOME/.config/autostart/kiosk-signage.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Kiosk Signage
Exec=env SIGNAGE_PORT=${PORT} ${APP_DIR}/pi/start-kiosk.sh
X-GNOME-Autostart-enabled=true
DESK
# labwc（新しい Raspberry Pi OS）向け
if [ -d /etc/xdg/labwc ] || command -v labwc >/dev/null; then
  mkdir -p "$HOME/.config/labwc"
  grep -q start-kiosk.sh "$HOME/.config/labwc/autostart" 2>/dev/null || \
    echo "env SIGNAGE_PORT=${PORT} ${APP_DIR}/pi/start-kiosk.sh &" >> "$HOME/.config/labwc/autostart"
  rm -f "$HOME/.config/autostart/kiosk-signage.desktop"   # 二重起動を防ぐ
fi

echo "== 画面の自動消灯オフ・デスクトップ自動ログイン =="
sudo raspi-config nonint do_blanking 1 2>/dev/null || true
sudo raspi-config nonint do_boot_behaviour B4 2>/dev/null || true

IP="$(hostname -I | awk '{print $1}')"
echo
echo "完了しました。再起動するとサイネージが全画面で表示されます:  sudo reboot"
echo "スマホの操作画面:  http://${IP}:${PORT}/remote"
echo "                 http://$(hostname).local:${PORT}/remote"
