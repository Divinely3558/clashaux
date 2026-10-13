# clashaux

**当前版本：v26.10.13b**

mihomo (Clash Meta) 辅助管理面板，提供 Web UI 来管理配置、订阅和服务。

## 功能

- **配置管理** — 上传/粘贴配置，自动换头拼接 base.yaml，mihomo -t 校验后写入，支持备份与回滚
- **基础设置** — external-controller、external-ui、secret、mixed-port 网页修改，保存后置于配置文件最头部并自动重载
- **订阅管理** — 保存订阅链接，拉取 → 换头 → 校验 → 一键生效；拉取失败自动 DoH 解析与本机代理兜底；写入期间各更新按钮互斥
- **服务控制** — 启动、重载、设为自启，实时日志流
- **base.yaml 编辑** — 在线编辑头部模板（基础连接设置已独立到「基础设置」卡片）
- **mihomo 更新** — 检测 GitHub 最新版本，GOAMD64 档位（v1/v2/v3）自动推荐并可手动选择，选错档位可换档重装；GitHub 不可用时支持上传 .gz / .deb 安装包手动安装
- **卸载 mihomo** — 网页一键卸载（输入面板 Token 二次确认），保留配置备份与面板数据，可随时重装
- **内置面板** — metacubexd 打包进程序，首次启动自动释放到程序同目录，无需联网下载

## 档位选择指南

| 档位 | 适用 CPU |
|------|----------|
| v3 | 2013 年后买的普通 PC、笔记本（Intel 4 代以后 / AMD 锐龙），需 AVX2 指令集 |
| v2 | 2008~2013 年老电脑、老服务器，需 SSE4.2 / POPCNT 指令集 |
| v1 | 很老的 CPU（J1900、G1840、初代至强等），任意 x86-64，最稳 |

选错档位导致 mihomo 无法运行时，换选正确档位再点「一键更新」即可自动用新档位重装替换。

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

生成的可执行文件在 `dist/clashaux`，metacubexd 面板与 base.yaml 均已内置，单文件即可运行；首次启动自动在程序同目录生成 `base.yaml` 与 `metacubexd/`。

mihomo 也安装到程序同一目录（二进制 `程序目录/mihomo`，数据目录即程序目录），由面板一键安装时自动创建 systemd 服务。

## 文件说明

| 文件 | 说明 |
|------|------|
| `clashaux.json` | 运行配置（端口、token 等），首次运行自动生成 |
| `mihomo` | mihomo 内核二进制（面板一键安装/更新时释放） |
| `config.yaml` | mihomo 生效配置（可手动替换为指向外部文件的软链接） |
| `basic.json` | 基础连接设置（external-controller/external-ui/secret/mixed-port），首次运行自动生成 |
| `base.yaml` | 配置换头的头部模板（不含基础连接设置） |
| `subscriptions.json` | 已保存的订阅链接 |
| `metacubexd.tgz` | 内置面板包（打包进可执行文件） |
| `install-service.sh` | 开机自启安装脚本（打包进可执行文件，启动时释放到程序目录） |
| `backups/` | 配置备份和 mihomo 二进制备份 |

## 前置条件

- Linux 系统（deb 系可上传 .deb 安装，其他发行版上传 .gz）
- 面板需以 root 运行（操作 systemd、写配置文件）；mihomo 未安装时可用面板「一键安装」

## 技术栈

- Python 3.13+
- FastAPI + Uvicorn
- httpx
- PyYAML

## 更新日志

### v26.10.13b

**新增**

- 网页一键卸载 mihomo：更新卡片新增卸载按钮，弹窗输入面板 Token 二次确认后删除二进制、systemd 服务与运行文件（config.yaml / cache.db / ruleset/），保留 backups/ 备份、metacubexd/ 与面板自身数据
- 安装包手动上传支持 .gz 与 .deb 双格式（.deb 自动解包提取二进制）
- install-service.sh 打包进可执行文件，首次启动自动释放到程序同目录，运行即可把面板设为开机自启（`./install-service.sh`）

**变更**

- mihomo 安装位置改为面板程序目录：二进制 `程序目录/mihomo`，数据目录即程序目录，配置/面板/备份集中存放
- 一键更新/一键安装改为直接下载 gz 二进制解压安装，不再依赖 dpkg 与 /usr/bin

**移除**

- SAFE_PATHS 适配逻辑（新布局下 external-ui 位于 mihomo home 内，无需放行）

**修复**

- 页面加载时自动更新检测用了不存在的 `d.level` 字段（应为 `d.detected`），导致 CPU 档位显示 `undefined`、换档按钮状态未初始化；抽成 `upCheckRender(d)` 统一渲染函数，init 与手动检查共用
- 基础设置卡片 external-ui placeholder 还是旧布局 `/etc/mihomo/metacubexd`，改为 `留空默认（程序目录/metacubexd）`

### v26.10.13

**新增**

- 「基础设置」卡片：网页修改 external-controller、external-ui、secret、mixed-port，保存后置于配置文件最头部并自动重载 mihomo；secret 支持一键生成随机 10 位大小写字母
- mihomo 更新支持自选 GOAMD64 档位（v1/v2/v3，附选择指南），系统自动检测推荐档位；选错档位可换档重装（自动替换旧档位二进制）
- deb 手动上传安装：GitHub 无法访问时，可在网页直接上传 deb 包安装
- metacubexd 面板打包进可执行文件，首次启动自动释放到程序同目录，并提供「重新释放面板」按钮
- 网络请求失败自动回退：直连失败后依次尝试 DoH 解析直连、本机代理兜底，解决 DNS 污染导致的订阅拉取失败与 GitHub 查询失败

**变更**

- base.yaml 不再包含 external-controller、external-ui、secret、mixed-port（旧文件首次启动自动剥离迁移到 basic.json）
- 「打开面板」按钮端口改为跟随基础设置中的 external-controller
- 一键安装流程中 metacubexd 改为本地释放内置包，不再联网下载

**修复**

- mihomo 更新卡片描述文案错误（升级失败不可回滚，备份仅作兜底）
- 订阅写入期间其他订阅的「一键更新」按钮未禁用，可能同时更新多个
