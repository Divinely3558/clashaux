#!/usr/bin/env bash
# clashaux 开机自启安装脚本
# 用法: ./install-service.sh [install|status|logs|uninstall] [可执行文件路径]
set -euo pipefail

SERVICE_NAME="clashaux"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'

# ---------- 1. 定位可执行文件 ----------
EXE="${2:-}"
if [[ -z "$EXE" ]]; then
    HERE="$(cd "$(dirname "$0")" && pwd)"
    for cand in "$HERE/dist/clashaux" "$HERE/clashaux" "$PWD/clashaux"; do
        if [[ -f "$cand" ]]; then EXE="$cand"; break; fi
    done
fi
if [[ -z "$EXE" ]]; then
    echo -e "${RED}未找到 clashaux 可执行文件，请手动指定：$0 install /path/to/clashaux${NC}" >&2
    exit 1
fi
EXE="$(cd "$(dirname "$EXE")" && pwd)/$(basename "$EXE")"
if [[ ! -f "$EXE" ]]; then
    echo -e "${RED}文件不存在：$EXE${NC}" >&2
    exit 1
fi
chmod +x "$EXE" 2>/dev/null || true

# ---------- 2. 判断 sudo 可用性 ----------
can_sudo() { sudo -n true 2>/dev/null; }

# ---------- 3. 生成 service 内容到临时文件 ----------
_tmp_file() { mktemp "/tmp/${SERVICE_NAME}.service.XXXXXX"; }

service_content() {
    local path="$1"
    cat > "$path" <<EOF
[Unit]
Description=clashaux panel (mihomo 辅助管理面板)
After=network.target mihomo.service

[Service]
Type=simple
ExecStart=${EXE}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

# ---------- 4. 子命令 ----------
cmd_install() {
    echo -e "${GRN}==> 安装 clashaux 为 systemd 服务${NC}"
    echo -e "    可执行文件: ${YLW}${EXE}${NC}"

    local tmp; tmp="$(_tmp_file)"; service_content "$tmp"

    if can_sudo; then
        sudo cp "$tmp" "${SERVICE_FILE}"
        sudo systemctl daemon-reload
        sudo systemctl enable --now "${SERVICE_NAME}"
        rm -f "$tmp"
        echo -e "${GRN}✓ 已写入 ${SERVICE_FILE} 并 enable --now${NC}"
        echo -e "    状态:  ${YLW}systemctl status ${SERVICE_NAME}${NC}"
        echo -e "    日志:  ${YLW}journalctl -u ${SERVICE_NAME} -f${NC}"
    else
        echo -e "${YLW}当前会话无免密 root，请复制以下命令到终端执行：${NC}"
        echo "--- 开始复制 ---"
        echo "sudo tee ${SERVICE_FILE} << EOF"
        cat "$tmp"
        echo "EOF"
        echo "sudo systemctl daemon-reload"
        echo "sudo systemctl enable --now ${SERVICE_NAME}"
        echo "--- 复制结束 ---"
        rm -f "$tmp"
    fi
}

cmd_status() {
    systemctl status "${SERVICE_NAME}" --no-pager || true
}

cmd_logs() {
    journalctl -u "${SERVICE_NAME}" -n 200 --no-pager
}

cmd_uninstall() {
    echo -e "${YLW}==> 卸载 clashaux 服务（保留可执行文件与数据）${NC}"
    if can_sudo; then
        sudo systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
        sudo systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
        sudo rm -f "${SERVICE_FILE}"
        sudo systemctl daemon-reload
        echo -e "${GRN}✓ 服务已卸载${NC}"
    else
        echo -e "${YLW}请执行：${NC}"
        echo "sudo systemctl stop ${SERVICE_NAME}"
        echo "sudo systemctl disable ${SERVICE_NAME}"
        echo "sudo rm -f ${SERVICE_FILE}"
        echo "sudo systemctl daemon-reload"
    fi
}

# ---------- 5. 分发 ----------
action="${1:-install}"
case "$action" in
    install)   cmd_install   ;;
    status)    cmd_status    ;;
    logs)      cmd_logs      ;;
    uninstall) cmd_uninstall ;;
    *)
        echo "用法: $0 [install|status|logs|uninstall] [可执行文件路径]" >&2
        exit 1
        ;;
esac
