#!/bin/bash
set -e

cd "$(dirname "$0")"

# 查找 uv：优先环境变量 → PATH → 实际用户家目录（sudo 下 $HOME 是 /root）
if [ -z "$UV" ]; then
  REAL_HOME="${HOME}"
  if [ -n "$SUDO_USER" ]; then
    REAL_HOME=$(eval echo "~$SUDO_USER")
  fi
  UV="$(command -v uv 2>/dev/null || true)"
  if [ -z "$UV" ] && [ -x "$REAL_HOME/.local/bin/uv" ]; then
    UV="$REAL_HOME/.local/bin/uv"
  fi
fi

if [ -z "$UV" ] || [ ! -x "$UV" ]; then
  echo "错误: 找不到 uv，请安装或设置 UV 环境变量" >&2
  exit 1
fi

echo "==> 安装 PyInstaller..."
"$UV" pip install pyinstaller

echo "==> 清理旧构建..."
rm -rf build dist clashaux.spec

echo "==> 检查内置面板包..."
if [ ! -f metacubexd.tgz ]; then
  echo "错误: 找不到 metacubexd.tgz（内置 metacubexd 面板包），请将其放到项目根目录" >&2
  exit 1
fi

echo "==> 打包..."
"$UV" run pyinstaller --onefile \
  --add-data "static:static" \
  --add-data "base.yaml:." \
  --add-data "metacubexd.tgz:." \
  --add-data "install-service.sh:." \
  --name clashaux \
  main.py

echo "==> 完成！可执行文件: dist/clashaux"
