#!/bin/bash
set -e

cd "$(dirname "$0")"

UV="${UV:-/home/junjie/.local/bin/uv}"

echo "==> 安装 PyInstaller..."
"$UV" pip install pyinstaller

echo "==> 清理旧构建..."
rm -rf build dist clashaux.spec

echo "==> 打包..."
"$UV" run pyinstaller --onefile \
  --add-data "static:static" \
  --add-data "base.yaml:." \
  --name clashaux \
  main.py

echo "==> 完成！可执行文件: dist/clashaux"
