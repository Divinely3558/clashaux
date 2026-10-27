# clashaux

mihomo (Clash Meta) 辅助管理面板，提供 Web UI 来管理配置、订阅和服务。

## 功能

- **安装检测** — 启动/登录前检测 mihomo 是否安装；未安装时禁止登录，页面提示「你未安装 mihomo」并给出 [GitHub 下载地址](https://github.com/MetaCubeX/mihomo/releases/latest) 与 CPU 档位（GOAMD64 v1/v2/v3）选择说明；可自行下载 `.deb` 执行 `sudo apt install ./xxx.deb`（或 `sudo dpkg -i`），也可在阻断弹窗直接上传安装包安装（.deb 自动 dpkg 安装，.gz 解压装二进制并按需补 systemd 单元），点击「重新检测」通过才会开放登录并自动释放 metacubexd
- **配置管理** — 上传/粘贴配置，自动换头拼接 base.yaml，mihomo -t 校验后写入，支持备份与回滚
- **基础设置** — external-controller、external-ui、secret、mixed-port 网页修改，保存后置于配置文件最头部并自动重载
- **订阅管理** — 保存订阅链接，拉取 → 换头 → 校验 → 一键生效；「全部更新」批量逐个生效并汇总 toast 结果；拉取失败自动 DoH 解析与本机代理兜底；写入期间各更新按钮互斥
- **内核升级** — 内核状态卡「升级」按钮上传更高版本安装包（.deb/.gz），安装前校验：新版本必须高于当前版本、GOAMD64 档位相同（v1→v1/v2→v2/v3→v3）、CPU 架构相同（amd64/arm64），任一不符拒绝并提示原因；升级成功自动重新释放面板与 geo 数据
- **服务控制** — 启动/停止、设为自启/取消自启均为随服务状态自动切换的单按钮（未知状态禁用，可点「刷新状态」重新获取），支持重载配置
- **base.yaml 编辑** — 在线编辑头部模板（基础连接设置已独立到「基础设置」卡片）；「保存」只更新模板待下次换头生效，「保存并应用」立即保留正文、只替换头部并重载 mihomo
- **内置 geo 数据** — geoip.dat / geosite.dat / country.mmdb 打包进可执行文件，检测到 mihomo 后释放到其数据目录（`-d` 目录），mihomo 可直接加载 GeoIP/GeoSite 规则；可执行文件同目录 `assets/` 下放置同名文件可外部覆盖（单文件粒度，缺失回退内置），免重新打包即可更新 geo 数据
- **内置面板** — metacubexd 打包进程序，检测到 mihomo 后自动释放到 **mihomo 数据目录**（systemd 单元 `-d` 参数，deb 安装默认为 `/etc/mihomo`）下的 `metacubexd/`，无需联网下载
- **配置备份** — 自动保留最近 5 份配置备份，支持下载与上传；上传备份先经 mihomo -t 校验，重名自动追加序号，超出 5 份自动清理最旧；可直接回滚
- **面板设置** — 点击左上角 Logo 弹出设置弹窗，验证当前 Token 后可修改端口与 Token；改 Token 即时生效，改端口保存后自动重启绑定新地址
- **网站图标** — favicon.svg 优先、favicon.png 兜底，浏览器标签页/侧边栏显示面板图标；鉴权白名单放行图标文件

mihomo 的安装与卸载由用户自行完成；升级可在面板「内核状态」卡上传更高版本安装包完成（版本/档位/架构校验通过后自动安装），也可自行用系统包管理处理。

## mihomo 自行安装（档位选择指南）

mihomo 必须先于面板使用前自行安装。推荐用 deb 包安装（自动创建 systemd 服务、数据目录为 `/etc/mihomo`）：

| 档位 | 适用 CPU |
|------|----------|
| v3 | 2013 年后买的普通 PC、笔记本（Intel 4 代以后 / AMD 锐龙），需 AVX2 指令集 |
| v2 | 2008~2013 年老电脑、老服务器，需 SSE4.2 / POPCNT 指令集 |
| v1 | 很老的 CPU（J1900、G1840、初代至强等），任意 x86-64，最稳 |

```bash
# 在 https://github.com/MetaCubeX/mihomo/releases/latest 下载与 CPU 匹配的 .deb 包后：
sudo apt install ./mihomo-linux-amd64-v3-*.deb   # 文件名按实际档位选择
# 或
sudo dpkg -i mihomo-linux-amd64-v3-*.deb
```

安装完成后可执行 `mihomo -v` 验证，再回到面板点击「重新检测」。升级可在面板「内核状态」卡点击「升级」上传更高版本安装包（新版本必须更高、档位与架构需与已装一致），卸载使用系统包管理自行处理。

## 快速开始

### 直接运行

```bash
# 安装依赖
uv sync

# 启动（需要 root 权限来操作 systemd 和写入 mihomo 数据目录）
sudo .venv/bin/python main.py
```

默认监听 `0.0.0.0:8686`，首次运行自动生成 `clashaux.json` 配置文件；启动后终端会显示本机/局域网访问地址与登录 Token。

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

生成的可执行文件在 `dist/clashaux`，metacubexd 面板、base.yaml 与 geo 数据（geoip.dat / geosite.dat / country.mmdb）均已内置，单文件即可运行；检测到 mihomo 后自动在其数据目录释放 `metacubexd/` 与 `geoip.dat`/`geosite.dat`/`country.mmdb`，并在程序同目录生成 `base.yaml`。

打包前请确认 `assets/` 目录齐全（`metacubexd.tgz`、`geoip.dat`、`geosite.dat`、`country.mmdb`），缺失时 build.sh 会报错退出。

## 文件说明

| 文件 | 说明 |
|------|------|
| `clashaux.json` | 运行配置（端口、token 等），首次运行自动生成 |
| `config.yaml` | 生效配置，位于 mihomo 数据目录（deb 安装为 `/etc/mihomo/config.yaml`，可手动替换为指向外部文件的软链接）；不存在时面板自动生成「基础设置 + base.yaml」最小配置 |
| `basic.json` | 基础连接设置（external-controller/external-ui/secret/mixed-port），首次运行自动生成 |
| `base.yaml` | 配置换头的头部模板（不含基础连接设置） |
| `subscriptions.json` | 已保存的订阅链接 |
| `mihomo-install.json` | 最近一次通过面板安装/升级的包信息（架构/GOAMD64 档位/版本），供内核升级校验比对；首次面板内安装或升级后生成 |
| `assets/` | 打包内置资源目录：`metacubexd.tgz`（面板包）、`geoip.dat` / `geosite.dat` / `country.mmdb`（GeoIP/GeoSite/MMDB 数据源），build.sh 从此目录读取并打进可执行文件，运行时释放到 mihomo 数据目录 |
| `install-service.sh` | 面板服务管理脚本（打包进可执行文件，启动时释放到程序目录）：安装/启用/取消开机自启、查看状态日志、卸载面板服务、完全卸载（面板 + mihomo + 数据文件，保留可执行文件）；无参数运行进入交互菜单 |
| `backups/` | 配置备份（最近 5 份） |

## 前置条件

- Linux 系统，mihomo 已由用户自行安装（推荐 deb 包，面板自动识别 systemd 单元中的二进制与 `-d` 数据目录；未安装时面板拒绝登录）
- 面板需以 root 运行（操作 systemd reload、写入 mihomo 数据目录配置）

## 技术栈

- Python 3.13+
- FastAPI + Uvicorn
- httpx
- PyYAML
