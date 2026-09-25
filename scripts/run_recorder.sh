#!/bin/bash
# keep the recorder alive
cd "$(dirname "$0")/.."
while true; do
  python3 -m pm.recorder >> logs/recorder.log 2>&1
  echo "recorder exited $(date), restarting" >> logs/recorder.log
  sleep 2
done
