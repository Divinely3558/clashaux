# clashaux

mihomo (Clash Meta) 辅助管理面板，提供 Web UI 来管理配置、订阅和服务。

## 功能

- **配置管理** — 上传/粘贴配置，自动换头拼接 base.yaml，mihomo -t 校验后写入，支持备份与回滚
- **订阅管理** — 保存订阅链接，拉取 → 换头 → 校验 → 一键生效
- **服务控制** — 启动、重载、设为自启，实时日志流
- **base.yaml 编辑** — 在线编辑外部控制头（mixed-port、external-controller 等）
- **mihomo 更新** — 检测 GitHub 最新版本，自动判断 GOAMD64 档位，一键下载 deb 安装

## 快速开始

### 直接运行

```bash
# 安装依赖
uv sync

# 启动（需要 root 权限来操作 systemd 和写入 /etc/mihomo）
sudo .venv/bin/python main.py
```

默认监听 `0.0.0.0:8686`，首次运行自动生成 `clashaux.json` 配置文件。

### 修改端口

编辑 `clashaux.json`：

```json
{
  "port": 9090
}
```

重启生效。

### 打包为可执行文件

```bash
./build.sh
```

生成的可执行文件在 `dist/clashaux`，将 `dist/clashaux` 和 `base.yaml` 放到同一目录即可运行。

## 文件说明

| 文件 | 说明 |
|------|------|
| `clashaux.json` | 运行配置（端口等），首次运行自动生成 |
| `base.yaml` | 配置换头的头部模板（external-controller、mixed-port 等） |
| `subscriptions.json` | 已保存的订阅链接 |
| `backups/` | 配置备份和 mihomo 二进制备份 |

## 前置条件

- Linux 系统，已安装 mihomo 并配置 systemd 服务（`systemctl start mihomo`）
- `/etc/mihomo/config.yaml` 为生效配置（支持软链接）
- 面板需以 root 运行（操作 systemd、写入 /etc/mihomo）

## 技术栈

- Python 3.13+
- FastAPI + Uvicorn
- httpx
- PyYAML
