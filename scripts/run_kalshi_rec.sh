#!/bin/bash
cd "$(dirname "$0")/.."
while true; do
  python3 -m pm.kalshi_rec >> logs/kalshi_rec.log 2>&1
  sleep 2
done
