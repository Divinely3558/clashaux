#!/usr/bin/env bash
# clashaux 服务管理脚本
# 用法: ./install-service.sh [install|start|stop|restart|status|logs|uninstall|purge] [可执行文件路径]
# 无参数运行时进入交互菜单
set -euo pipefail

SERVICE_NAME="clashaux"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
MIHOMO_SERVICE_FILE="/usr/lib/systemd/system/mihomo.service"
MIHOMO_BIN="/usr/bin/mihomo"
MIHOMO_ETC="/etc/mihomo"
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
    echo -e "${RED}文件不存在：${EXE}${NC}" >&2
    exit 1
fi
chmod +x "$EXE" 2>/dev/null || true
EXE_DIR="$(dirname "$EXE")"

# ---------- 2. sudo 辅助 ----------
can_sudo() { sudo -n true 2>/dev/null; }

# 需要 root 的操作：有免密 sudo 则直接执行，否则打印命令供用户手动复制
run_privileged() {
    local desc="$1"; shift
    if can_sudo; then
        sudo "$@"
    else
        echo -e "${YLW}当前会话无免密 root，请手动执行：${NC}"
        echo "sudo $*"
    fi
}

# ---------- 3. 生成 service 内容 ----------
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
    echo -e "${GRN}==> 安装 clashaux 为 systemd 服务（开机自启）${NC}"
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
        echo -e "${YLW}请复制以下命令到终端执行：${NC}"
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

cmd_start()   { run_privileged "启动服务" systemctl start  "${SERVICE_NAME}"; }
cmd_stop()    { run_privileged "停止服务" systemctl stop   "${SERVICE_NAME}"; }
cmd_restart() { run_privileged "重启服务" systemctl restart "${SERVICE_NAME}"; }

cmd_status() { systemctl status "${SERVICE_NAME}" --no-pager || true; }

cmd_logs() { journalctl -u "${SERVICE_NAME}" -n 200 --no-pager; }

cmd_uninstall() {
    echo -e "${YLW}==> 卸载 clashaux 服务（保留可执行文件、数据与 mihomo）${NC}"
    if can_sudo; then
        sudo systemctl stop    "${SERVICE_NAME}" 2>/dev/null || true
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

# 完全卸载的 root 操作集中到一个临时脚本
purge_script() {
    local path="$1"
    cat > "$path" <<EOF
#!/usr/bin/env bash
set -e
# clashaux 面板服务
systemctl stop ${SERVICE_NAME} 2>/dev/null || true
systemctl disable ${SERVICE_NAME} 2>/dev/null || true
rm -f ${SERVICE_FILE}
# mihomo 服务与程序
systemctl stop mihomo 2>/dev/null || true
systemctl disable mihomo 2>/dev/null || true
if dpkg -s mihomo >/dev/null 2>&1; then
    apt-get purge -y mihomo || dpkg -P mihomo || true
fi
rm -f ${MIHOMO_BIN}
rm -f ${MIHOMO_SERVICE_FILE} /etc/systemd/system/mihomo.service
systemctl daemon-reload
# mihomo 数据目录（config.yaml 若为软链接，只删链接，不删指向的外部文件）
rm -rf ${MIHOMO_ETC}
# 面板数据文件（保留可执行文件与 install-service.sh）
rm -f "${EXE_DIR}/clashaux.json" "${EXE_DIR}/basic.json" \\
    "${EXE_DIR}/base.yaml" "${EXE_DIR}/subscriptions.json"
rm -rf "${EXE_DIR}/backups" "${EXE_DIR}/__pycache__"
EOF
}

cmd_purge() {
    echo -e "${RED}==> 完全卸载：clashaux 服务 + mihomo + 全部数据文件${NC}"
    echo -e "    将删除："
    echo -e "      - clashaux systemd 服务（${SERVICE_FILE}）"
    echo -e "      - mihomo（deb 包 purge，或 ${MIHOMO_BIN} + systemd 单元）"
    echo -e "      - mihomo 数据目录 ${MIHOMO_ETC}（配置/缓存/metacubexd/geo 数据；"
    echo -e "        config.yaml 软链接指向的外部文件不会删除）"
    echo -e "      - 面板数据（clashaux.json / basic.json / base.yaml / subscriptions.json / backups/）"
    echo -e "    ${GRN}保留：可执行文件 ${EXE} 与本脚本${NC}"
    read -r -p "确认完全卸载？[y/N] " ans
    if [[ "${ans:-}" != "y" && "${ans:-}" != "Y" ]]; then
        echo "已取消"
        exit 0
    fi

    local tmp; tmp="$(_tmp_file)"; purge_script "$tmp"

    if can_sudo; then
        sudo bash "$tmp"
        rm -f "$tmp"
        echo -e "${GRN}✓ 完全卸载完成（可执行文件已保留：${EXE}）${NC}"
    else
        echo -e "${YLW}当前会话无免密 root，请复制以下命令到终端执行：${NC}"
        echo "--- 开始复制 ---"
        echo "sudo bash <<'PURGE_EOF'"
        cat "$tmp"
        echo "PURGE_EOF"
        echo "--- 复制结束 ---"
        rm -f "$tmp"
    fi
}

cmd_menu() {
    while true; do
        echo
        echo -e "${GRN}==> clashaux 服务管理${NC}（可执行文件: ${YLW}${EXE}${NC}）"
        echo "  1) 安装并开机自启"
        echo "  2) 启动服务"
        echo "  3) 停止服务"
        echo "  4) 重启服务"
        echo "  5) 查看状态"
        echo "  6) 查看日志"
        echo "  7) 卸载面板服务（保留 mihomo 与数据）"
        echo "  8) 完全卸载（面板 + mihomo + 数据，保留可执行文件）"
        echo "  0) 退出"
        read -r -p "请选择 [0-8]: " sel
        case "$sel" in
            1) cmd_install   ;;
            2) cmd_start     ;;
            3) cmd_stop      ;;
            4) cmd_restart   ;;
            5) cmd_status    ;;
            6) cmd_logs      ;;
            7) cmd_uninstall ;;
            8) cmd_purge     ;;
            0) exit 0        ;;
            *) echo -e "${RED}无效选择${NC}" ;;
        esac
    done
}

# ---------- 5. 分发 ----------
action="${1:-menu}"
case "$action" in
    menu)      cmd_menu      ;;
    install)   cmd_install   ;;
    start)     cmd_start     ;;
    stop)      cmd_stop      ;;
    restart)   cmd_restart   ;;
    status)    cmd_status    ;;
    logs)      cmd_logs      ;;
    uninstall) cmd_uninstall ;;
    purge)     cmd_purge     ;;
    *)
        echo "用法: $0 [install|start|stop|restart|status|logs|uninstall|purge] [可执行文件路径]" >&2
        echo "无参数运行时进入交互菜单" >&2
        exit 1
        ;;
esac
