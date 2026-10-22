#!/bin/bash
# Run on the model server as root, from this directory:  sudo ./install.sh [config.json]
set -euo pipefail
cd "$(dirname "$0")"
install -d /opt/model-sleeper
install -m 755 sleeper.py /opt/model-sleeper/sleeper.py
[ -f /etc/model-sleeper.json ] || cp "${1:-config.example.json}" /etc/model-sleeper.json
python3 recreate_containers.py /etc/model-sleeper.json
install -m 644 model-sleeper.service /etc/systemd/system/model-sleeper.service
systemctl daemon-reload
systemctl enable --now model-sleeper
sleep 2
curl -s http://127.0.0.1:8099/status
