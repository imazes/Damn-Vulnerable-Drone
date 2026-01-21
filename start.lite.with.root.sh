#!/bin/bash
set -x
export DISPLAY=:0
sudo -u damn env DISPLAY=:0 xhost +SI:localuser:root

CC_SVC="companion-computer-lite"
GCS_SVC="ground-control-station-lite"
SIM_SVC="simulator-lite"
docker compose -f docker-compose-lite.yaml up -d
docker compose -f docker-compose-lite.yaml logs -f "$SIM_SVC" "$CC_SVC" "$GCS_SVC"
