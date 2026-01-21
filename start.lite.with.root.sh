#!/bin/bash
# set -x
cd "$(dirname "$0")"

export DISPLAY=:0
sudo -u damn env DISPLAY=:0 xhost +SI:localuser:root
sudo -u damn env DISPLAY=:0 xhost +local:docker 

CC_SVC="companion-computer-lite"
GCS_SVC="ground-control-station-lite"
SIM_SVC="simulator-lite"
./stop.sh
# ./start.sh --no-wifi
docker compose -f docker-compose-lite.yaml up -d
docker compose -f docker-compose-lite.yaml logs -f "$SIM_SVC" "$CC_SVC" "$GCS_SVC" 2>&1 |tee dvd.log
