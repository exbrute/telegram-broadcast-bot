#!/bin/bash
set -euo pipefail

APP_DIR="/opt/telegram-broadcast-bot"

echo "==> Installing system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y python3 python3-pip python3.12-venv

echo "==> Setting up Python environment..."
cd "$APP_DIR"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
mkdir -p data/sessions data/media

echo "==> Installing systemd service..."
cat > /etc/systemd/system/telegram-broadcast-bot.service << EOF
[Unit]
Description=Telegram Broadcast Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python run.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable telegram-broadcast-bot
systemctl restart telegram-broadcast-bot

sleep 3
systemctl status telegram-broadcast-bot --no-pager -l
echo "==> Done. Check logs: journalctl -u telegram-broadcast-bot -f"
