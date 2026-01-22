#!/bin/bash
set -e
cd "$(dirname "$0")"

export DISPLAY=:0
sudo -u damn env DISPLAY=:0 xhost +SI:localuser:root
sudo -u damn env DISPLAY=:0 xhost +local:docker 

# 镜像名称
CC_SVC="companion-computer-lite"
GCS_SVC="ground-control-station-lite"
SIM_SVC="simulator-lite"

./stop.sh
# ./start.sh --no-wifi
# 刷新构建,删除旧的构建
docker compose -f docker-compose-lite.yaml build && docker image prune -f
# 后台启动项目
docker compose -f docker-compose-lite.yaml up -d
# 记录日志
docker compose -f docker-compose-lite.yaml logs -f "$SIM_SVC" "$CC_SVC" "$GCS_SVC" 2>&1 > dvd.log &
tail -f dvd.log
