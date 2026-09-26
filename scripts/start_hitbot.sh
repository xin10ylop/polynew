#!/bin/bash
# Run on the server: replaces the old fast-maker paper bot with the weekly "hit" paper bot (systemd service polybot-hit).
# Paper only: no keys, no money. Logs: ~/polynew/logs/hitbot.log; P&L: .venv/bin/python -m bot.hitbot --summary
set -e
cd /home/ubuntu/polynew
sudo systemctl disable --now polybot-paper 2>/dev/null || true
mkdir -p logs
.venv/bin/pip install -q requests
sudo tee /etc/systemd/system/polybot-hit.service >/dev/null <<'UNIT'
[Unit]
Description=Weekly Bitcoin 'hit' strategy - PAPER mode (no keys, no money)
After=network-online.target
Wants=network-online.target
[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/polynew
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/polynew/.venv/bin/python -m bot.hitbot
Restart=always
RestartSec=30
StandardOutput=append:/home/ubuntu/polynew/logs/hitbot.log
StandardError=append:/home/ubuntu/polynew/logs/hitbot.log
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now polybot-hit
sleep 20
echo "--- service status:"; systemctl is-active polybot-hit
echo "--- last log lines:"; tail -5 logs/hitbot.log
