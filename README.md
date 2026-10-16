# clashaux

**当前版本：v26.10.16**

mihomo (Clash Meta) 辅助管理面板，提供 Web UI 来管理配置、订阅和服务。

## 功能

- **安装检测** — 启动/登录前检测 mihomo 是否安装；未安装时禁止登录，页面提示「你未安装 mihomo」并给出 [GitHub 下载地址](https://github.com/MetaCubeX/mihomo/releases/latest) 与 CPU 档位（GOAMD64 v1/v2/v3）选择说明；可自行下载 `.deb` 执行 `sudo apt install ./xxx.deb`（或 `sudo dpkg -i`），也可在阻断弹窗直接上传安装包安装（.deb 自动 dpkg 安装，.gz 解压装二进制并按需补 systemd 单元），点击「重新检测」通过才会开放登录并自动释放 metacubexd
- **配置管理** — 上传/粘贴配置，自动换头拼接 base.yaml，mihomo -t 校验后写入，支持备份与回滚
- **基础设置** — external-controller、external-ui、secret、mixed-port 网页修改，保存后置于配置文件最头部并自动重载
- **订阅管理** — 保存订阅链接，拉取 → 换头 → 校验 → 一键生效；拉取失败自动 DoH 解析与本机代理兜底；写入期间各更新按钮互斥
- **服务控制** — 启动、重载、设为自启，实时日志流
- **base.yaml 编辑** — 在线编辑头部模板（基础连接设置已独立到「基础设置」卡片）；「保存」只更新模板待下次换头生效，「保存并应用」立即保留正文、只替换头部并重载 mihomo
- **内置 geo 数据** — geoip.dat / geosite.dat / country.mmdb 打包进可执行文件，检测到 mihomo 后释放到其数据目录（`-d` 目录），mihomo 可直接加载 GeoIP/GeoSite 规则
- **内置面板** — metacubexd 打包进程序，检测到 mihomo 后自动释放到 **mihomo 数据目录**（systemd 单元 `-d` 参数，deb 安装默认为 `/etc/mihomo`）下的 `metacubexd/`，无需联网下载

mihomo 的安装、升级、卸载均由用户自行完成，面板不再提供这些功能。

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

安装完成后可执行 `mihomo -v` 验证，再回到面板点击「重新检测」。升级/卸载同样使用系统包管理自行处理。

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

## 更新日志

### v26.10.16

**新增**

- `deploy.sh` 一键部署脚本：停止 clashaux systemd 服务 → 复制 `dist/clashaux` 到 `Project/vpn/` → 赋权 777 → 重启服务并显示状态（打包后直接 `./deploy.sh` 即可）
- 配置备份支持下载与上传：点击备份文件名即可下载到本地；「上传备份」把本地 YAML 配置存入备份列表（先经 mihomo -t 校验，失败拒收），重名自动追加 `-1`/`-2`…，超出 5 份自动清理最旧备份，上传的备份可直接回滚
- 备份文件名显示简化为时间戳部分（去掉 `config-` 前缀与 `.yaml` 后缀）
- 面板设置弹窗：点击左上角 Logo / 品牌文字弹出，先输入当前 Token 验证，通过后可修改 `clashaux.json` 的端口与 Token；确认后立即生效——仅改 Token 即时更新（本地会话自动同步，无需重新登录），改端口先检查可用性再保存，0.8 秒后进程原地重启绑定新端口，浏览器 3 秒后自动跳转新地址

**变更**

- 控制台卡片布局重排为五排：第一排「服务控制 + 基础设置」、第二排「内核状态 + 运行日志」（日志栏更宽）、第三排「头部模板」通铺、第四排「订阅更新」通铺、第五排「配置更新 + 配置备份」；左侧导航顺序同步为新排布
- 首排改为左列纵向堆叠「服务控制 + 内核状态」、右列「基础设置」：内核状态填充服务控制下方空隙、移除说明文字、键值横排一行，左列整体与基础设置同高（内核状态 flex 拉伸补齐）；服务控制磁贴内边距/字号/间距收紧更紧凑
- 运行日志独占一排通铺；实时跟踪改为认证后常开（断线 5 秒自动重连），移除勾选开关、行数选择与「加载日志」按钮（默认加载 200 行）
- 「重新释放 metacubexd 面板」按钮从内核状态移至基础设置按钮行（与保存并应用/还原并列）
- 卡片标题简化：「mihomo 内核状态」改为「内核状态」，「base.yaml · 头部模板」改为「头部模板」
- 防溢出优化：双列网格子项统一 `min-width: 0`；内核状态键值（数据目录等长路径）允许任意处换行；备份/订阅行的文件名与时间改为省略号截断；≤1240px 所有双列自动回退为单列
- 配置备份列表精简：只显示文件名与大小（移除时间），行内边距收紧更纤细，「回滚」按钮固定不压缩不折行

**修复**

- 修复「基础设置」半宽卡片内 4 列键值网格（标签+输入框×2）最小宽度超过卡片宽度导致的严重溢出，改为 2 列逐行堆叠
- 修复「服务控制」卡片被「基础设置」等高拉伸导致的臃肿空白：首排改为顶部对齐，服务控制高度由内容决定，磁贴内边距与按钮区间距同步收紧

### v26.10.15f

**优化**

- 合并 `core/config.py`、`core/mihomo.py`、`core/service.py` 中重复的 `_run()` 函数到共享模块 `core/utils.py`
- 所有 API 端点改为异步（`async def`），阻塞操作（subprocess、文件 I/O、HTTP 请求）通过 `asyncio.to_thread()` 在线程池执行，避免阻塞事件循环
- `mihomo.detect()` 添加 10 秒 TTL 缓存，减少 15 秒轮询的重复 subprocess 调用
- 删除 `core/net.py` 中未使用的 `stream_download()` 函数及相关 CSS 死代码
- `UNIT = "mihomo"` 常量提取到 `core/paths.py`，消除 mihomo.py 与 service.py 的重复定义
- 修复 `config.py` 中 `bare except Exception: pass` 改为捕获具体异常（`yaml.YAMLError`, `OSError`）
- 修复 `config.py` 中 lambda 赋值改为命名函数
- 统一 `subscription.py` 中 JSON 文件写入的尾部换行
- 删除无用的 `/api/token` 端点（无认证保护但又被认证中间件拦截，实际无法使用）
- 提取前端 `renderStats()` 函数消除重复的 stats HTML 模板
- 修复 `loadBasic()` 在认证启动时被重复调用
- 日志框限制最大 2000 行，防止 SSE 长时间运行时内存泄漏
- 标签页后台时清除 15 秒轮询，前台时恢复，减少不必要的服务器请求
- 添加 `beforeunload` 事件，未保存 base.yaml 更改时离开页面会提示确认
- 可执行文件非 root 运行时显示提示并要求以 root 或 sudo 运行，随后退出
- 「重新释放 metacubexd 面板」按钮增加 token 认证检查，未登录时弹出登录框

### v26.10.15e

**修复**

- 修复 `.gz` 安装时生成的 systemd 单元缺少 `ExecReload=` 导致 `systemctl reload mihomo` 报错「Job type reload is not applicable」；服务重载逻辑增加降级机制，reload 不可用时自动改用 restart

### v26.10.15d

**变更**

- `.gz` 二进制安装位置与 `.deb` 布局统一（以 deb 为准）：二进制安装到 `/usr/bin/mihomo`，systemd 单元写到 `/usr/lib/systemd/system/mihomo.service`，数据目录仍为 `/etc/mihomo`；卸载脚本同步按此路径清理，并兜底清理旧版安装在 `/etc/systemd/system/` 与 `/usr/local/bin/` 的残留

### v26.10.15c

**变更**

- 打包内置资源集中到 `assets/` 目录（metacubexd.tgz 与 geoip.dat / geosite.dat / country.mmdb 从项目根与 `.text/` 移入），build.sh 从 assets/ 读取，缺失时报错退出；程序按 `BUNDLE_DIR/assets/` 路径读取，打包产物内外的布局保持一致

### v26.10.15b

**变更**

- 手动运行时 Ctrl+C 优雅退出：不再打印 KeyboardInterrupt/CancelledError 堆栈，终端仅显示「面板已停止」；systemd 停止时 journal 同步记录该日志

### v26.10.15

**新增**

- base.yaml 卡片新增「保存并应用」：保存模板后立即套用到生效配置（保留现有正文，只替换头部，基础设置始终在最头部），经 mihomo -t 校验、备份后原子写入并重载 mihomo；生效配置缺失时按模板生成最小配置
- geo 数据内置：geoip.dat / geosite.dat / country.mmdb 打包进可执行文件，检测到 mihomo 后释放到其数据目录（`-d` 目录），大小与内置文件一致时跳过、打包件更新后自动覆盖
- 启动横幅：终端启动时显示面板本机/局域网访问地址与登录 Token，便于确认启动状态与首次登录
- install-service.sh 升级为服务管理脚本：无参数运行进入交互菜单，支持安装/设为自启（enable --now）、取消开机自启（disable，服务继续运行）、查看状态/日志、卸载面板服务，以及完全卸载——移除 clashaux 服务、mihomo（deb 包 purge 或删除 `/usr/local/bin/mihomo` 与 systemd 单元）、`/etc/mihomo` 数据目录与面板数据文件，保留可执行文件与脚本自身；完全卸载需 `[y/N]` 确认，无免密 root 时输出可复制执行的命令块

### v26.10.14

**新增**

- 登录前安装检测：未检测到 mihomo 时禁止登录，阻断弹窗提示「你未安装 mihomo」，提供 GitHub Releases 下载地址、本机 CPU 推荐 GOAMD64 档位与 `.deb` 自行安装步骤；安装后点击「重新检测」通过才释放 metacubexd 并开放登录
- 生效配置自动生成：mihomo 数据目录下不存在 config.yaml 时（启动 / 打开面板 / 读取当前配置 / 保存基础设置 / 换头任一环节），自动生成「基础设置 + base.yaml」最小配置并通过 mihomo -t 校验后写入；dangling 软链接会写入其指向的真实路径
- 阻断弹窗内上传安装：选择 `.deb` 或 `.gz` 安装包并输入面板 Token 即可安装（.deb 由 dpkg 安装、缺依赖自动 `apt-get -f` 修复后重试；.gz 解压校验 ELF 后安装到 `/usr/local/bin/mihomo`，无 systemd 单元时自动创建并 enable）；安装成功自动重新检测并释放 metacubexd
- mihomo 二进制与数据目录动态识别：解析 systemd 单元 ExecStart 的 `-d` 参数，PATH 与 `/etc/mihomo` 兜底，适配用户自行 deb/gz 安装的布局
- metacubexd 面板改为释放到 mihomo 数据目录（deb 安装默认 `/etc/mihomo/metacubexd`），基础设置默认 external-ui 同步指向该目录

**变更**

- 面板启动时仅在检测到 mihomo 后才释放 metacubexd 面板，未安装时仅记录告警
- 「mihomo 管理」卡片改为「mihomo 内核状态」，只展示版本、数据目录与运行状态及面板重新释放按钮

**修复**

- 写入生效配置时继承原文件的属主与权限位：此前原子替换会重置为进程身份 + 0644，用户自行设为 0600/其他属主的配置被覆盖后无法按原权限修改（面板以 root 运行时会重新 chown 回原属主）
- base.yaml 保存时自动剥离 external-controller/external-ui/secret/mixed-port：此前把整份配置粘进模板会把基础连接设置写进 base.yaml，与「基础设置」卡片重复
- 默认 base.yaml 头部模板移除过时的 `# external-ui` 注释行，模板只保留 allow-lan/mode/unified-delay/tcp-concurrent/geodata-mode/geox-url/log-level

**移除**

- 网页一键安装 mihomo（含 SSE 安装流程、.gz/.deb 上传安装与相关后端接口）
- 网页一键更新/换档重装 mihomo（含 GitHub 版本检测、GOAMD64 档位选择、上传更新与相关后端接口）
- 网页一键卸载 mihomo（含 token 二次确认弹窗、清理逻辑与相关后端接口）
- core/upgrade.py 模块（安装检测与面板释放能力迁移至新模块 core/mihomo.py）

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
