#!/usr/bin/env bash
# deploy.sh — 打包后一键部署：停止服务 → 复制可执行文件到 Project/vpn → 赋权 777 → 重启服务
# 用法：先 ./build.sh 打包，再 ./deploy.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)/dist/clashaux"
DST="/home/junjie/mydata/Project/vpn/clashaux"

[[ -f "$SRC" ]] || { echo "❌ 未找到 $SRC，请先运行 ./build.sh 打包"; exit 1; }

echo "▶ 停止 clashaux 服务…"
sudo systemctl stop clashaux

echo "▶ 复制 $SRC → $DST"
sudo cp "$SRC" "$DST"
sudo chmod 777 "$DST"

echo "▶ 启动 clashaux 服务…"
sudo systemctl start clashaux

sleep 1
systemctl --no-pager status clashaux | head -n 4
echo "✅ 部署完成"
