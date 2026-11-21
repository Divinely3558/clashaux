# clashaux

**v26.11.21a**

mihomo (Clash Meta) 辅助管理面板，提供 Web UI 来管理配置、订阅和服务。

## 功能

- **安装检测** — 启动/登录前检测 mihomo 是否安装；未安装时禁止登录，页面提示「你未安装 mihomo」并给出 [GitHub 下载地址](https://github.com/MetaCubeX/mihomo/releases/latest)、**本机适配建议**（自动读取 `/proc/cpuinfo` 指令集判定可用的最高 GOAMD64 档位，给出应下载的安装包文件名）与档位选择说明；选中安装包后即时提示架构/档位是否合适；可自行下载 `.deb` 执行 `sudo apt install ./xxx.deb`（或 `sudo dpkg -i`），也可在阻断弹窗直接上传安装包安装（.deb 自动 dpkg 安装，.gz 解压装二进制并按需补 systemd 单元），点击「重新检测」通过才会开放登录并自动释放 metacubexd
- **配置管理** — 上传/粘贴配置，自动换头拼接 base.yaml，mihomo -t 校验后写入，支持备份与回滚
- **基础设置** — external-controller、external-ui、secret、mixed-port 网页修改，保存后置于配置文件最头部并自动重载
- **订阅管理** — 保存订阅链接，拉取 → 换头 → 校验 → 一键生效；「全部更新」批量逐个生效并汇总 toast 结果；拉取失败自动 DoH 解析与本机代理兜底；写入期间各更新按钮互斥
- **内核升级** — 内核状态卡「升级」按钮上传更高版本安装包（.deb/.gz），弹窗给出本机适配建议与应选文件名；安装前校验：新版本必须高于当前版本、GOAMD64 档位与当前内核相同（v1→v1/v2→v2/v3→v3）、平台为 linux、CPU 架构相同，任一不符拒绝并提示原因；档位取自**包内二进制内嵌的编译参数**（比文件名可信），并识别官方全部命名变体（`go120/go122/go124` 工具链变体、`compatible`、`softfloat/hardfloat`、`armv7/loong64/riscv64/…` 等）与改名包；升级成功自动重新释放面板与 geo 数据
- **服务控制** — 启动/停止、设为自启/取消自启均为随服务状态自动切换的单按钮（未知状态禁用，可点「刷新状态」重新获取），支持重载配置
- **base.yaml 编辑** — 在线编辑头部模板（基础连接设置已独立到「基础设置」卡片）；「保存」只更新模板待下次换头生效，「保存并应用」立即保留正文、只替换头部并重载 mihomo
- **内置 geo 数据** — geoip.dat / geosite.dat / country.mmdb 打包进可执行文件，检测到 mihomo 后释放到其数据目录（`-d` 目录），mihomo 可直接加载 GeoIP/GeoSite 规则；可执行文件同目录 `assets/` 下放置同名文件可外部覆盖（单文件粒度，缺失回退内置），免重新打包即可更新 geo 数据
- **内置面板** — metacubexd 打包进程序，检测到 mihomo 后自动释放到 **mihomo 数据目录**（systemd 单元 `-d` 参数，deb 安装默认为 `/etc/mihomo`）下的 `metacubexd/`，无需联网下载；「基础设置」卡的「重新释放面板」按钮可随时手动重新释放（面板文件丢失、损坏或被误删时恢复）；「打开面板」跳转时把当前 `secret` 写在 URL 片段里（`#/setup?hostname=&port=&secret=`），metacubexd 读后即连并替换为 `#/overview`，无需再输密码——片段不发给内核、不进浏览器历史，直接访问或收藏 `/ui/` 仍需手输密钥
- **配置备份** — 自动保留最近 5 份配置备份，支持下载与上传；上传备份先经 mihomo -t 校验，重名自动追加序号，超出 5 份自动清理最旧；可直接回滚
- **面板设置** — 点击左上角 Logo 弹出设置弹窗，验证当前 Token 后可修改端口与 Token；改 Token 即时生效，改端口保存后自动重启绑定新地址
- **网站图标** — favicon.svg 优先、favicon.png 兜底，浏览器标签页/侧边栏显示面板图标；鉴权白名单放行图标文件
- **界面主题** — 内置「极光深空」深色主题：4 条漂移极光光带 + HUD 网格 + 星点 + 缓慢扫描线组成的氛围背景层、半透明玻璃卡片、渐变发光描边与状态指示；卡片支持悬停浮起与光标微光，桌面/手机自适应，并跟随系统「减少动效」偏好关闭全部动画
- **命令面板** — 按 `Ctrl/Cmd + K` 打开命令面板，输入关键词即可跳转到对应功能卡片

mihomo 的安装与卸载由用户自行完成；升级可在面板「内核状态」卡上传更高版本安装包完成（版本/档位/架构校验通过后自动安装），也可自行用系统包管理处理。

## mihomo 自行安装（档位选择指南）

mihomo 必须先于面板使用前自行安装。推荐用 deb 包安装（自动创建 systemd 服务、数据目录为 `/etc/mihomo`）：

| 档位 | 判定条件（`/proc/cpuinfo` 指令集） | 适用 CPU |
|------|-----------------------------------|----------|
| v3 | 含 `sse4_2 popcnt cx16 movbe` 且含 `avx avx2 bmi1 bmi2 fma osxsave` | 2013 年后买的普通 PC、笔记本（Intel 4 代以后 / AMD 锐龙） |
| v2 | 含 `sse4_2 popcnt cx16 movbe` | 2008~2013 年老电脑、老服务器 |
| v1 | 其余 x86-64 | 很老的 CPU（J1900、G1840、初代至强等），最稳 |

面板按上表读取本机指令集自动判定，判定结果直接显示在安装/升级弹窗的「本机适配建议」里，并给出应下载的文件名（如 `mihomo-linux-amd64-v2-*.deb`）；非 x86-64 架构（arm64 / armv7 / loong64 / riscv64 等）没有档位之分。也可手动核对：

```bash
flags=$(grep -m1 -E '^flags|^features' /proc/cpuinfo | cut -d: -f2)
has() { echo "$flags" | tr ' ' '\n' | grep -qx "$1"; }
level=v1
has sse4_2 && has popcnt && has cx16 && has movbe && level=v2
has avx && has avx2 && has bmi1 && has bmi2 && has fma && has osxsave && level=v3
echo "GOAMD64=$level"
```

发布页的资源名格式为 `mihomo-<平台>-<架构>-<档位>-<变体>-v<版本>.<后缀>`，其中 `compatible` 等价于 v1，`go120/go122/go124` 是 Go 工具链变体（与档位无关，可忽略），`softfloat/hardfloat`、`abi1/abi2` 为部分架构的浮点与 ABI 变体；面板只接受 `linux` 平台的 `.deb` / `.gz`，其余平台与后缀请用系统包管理器安装。

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
sudo uv run main.py
```

默认监听 `0.0.0.0:8686`，首次运行自动生成 `clashaux.json` 配置文件；启动后终端会显示本机/局域网访问地址与登录 Token。

### 修改端口

推荐在面板内操作：点击左上角 Logo 打开「面板设置」，验证当前 Token 后即可修改端口与 Token；改端口保存后面板自动重启并跳转到新地址。

也可手动编辑 `clashaux.json` 后重启：

```json
{
  "port": 9090
}
```

### 打包为可执行文件

```bash
./build.sh
```

生成的可执行文件在 `dist/clashaux`，metacubexd 面板、base.yaml 与 geo 数据（geoip.dat / geosite.dat / country.mmdb）均已内置，单文件即可运行；检测到 mihomo 后自动在其数据目录释放 `metacubexd/` 与 `geoip.dat`/`geosite.dat`/`country.mmdb`，并在程序同目录生成 `base.yaml`。

打包前请确认 `assets/` 目录齐全（`metacubexd.tgz`、`geoip.dat`、`geosite.dat`、`country.mmdb`），缺失时 build.sh 会报错退出。

**ARM / 其它架构请自行打包**：PyInstaller 产物只适配打包它的那台机器，x86-64 上打出的 `dist/clashaux` 拷到 ARM 会直接 `Exec format error`——ARM（arm64 / armv7 / 树莓派 / 香橙派 / Armbian 等）需在目标机器上克隆本仓库、装好 `uv` 后重新执行一次 `./build.sh`，没有可跨架构复用的预编译包。32 位 armv6/armv7 无 PyInstaller 轮子，建议直接 `sudo uv run main.py` 源码运行。

面板程序本身与架构无关，档位判定只在 x86-64 上生效；「上传安装 / 内核升级」依赖 `dpkg`/`dpkg-deb`，需 Debian 系发行版（Raspberry Pi OS、Armbian、Ubuntu arm64 等），Arch Linux ARM / Alpine ARM 上自行装好 mihomo 后，面板的检测、配置、订阅与服务控制仍可用。

## 文件说明

| 文件 | 说明 |
|------|------|
| `clashaux.json` | 运行配置（端口、token 等），首次运行自动生成 |
| `config.yaml` | 生效配置，位于 mihomo 数据目录（deb 安装为 `/etc/mihomo/config.yaml`，可手动替换为指向外部文件的软链接）；不存在时面板自动生成「基础设置 + base.yaml」最小配置 |
| `basic.json` | 基础连接设置（external-controller/external-ui/secret/mixed-port），首次运行自动生成 |
| `base.yaml` | 配置换头的头部模板（不含基础连接设置） |
| `subscriptions.json` | 已保存的订阅链接 |
| `mihomo-install.json` | 最近一次通过面板安装/升级的包信息（架构/GOAMD64 档位/版本），供内核升级校验比对（当前档位优先读内核二进制内嵌的编译参数，此文件作兜底）；首次面板内安装或升级后生成 |
| `assets/` | 打包内置资源目录：`metacubexd.tgz`（面板包）、`geoip.dat` / `geosite.dat` / `country.mmdb`（GeoIP/GeoSite/MMDB 数据源），build.sh 从此目录读取并打进可执行文件，运行时释放到 mihomo 数据目录 |
| `install-service.sh` | 面板服务管理脚本（打包进可执行文件，启动时释放到程序目录）：安装/启用/取消开机自启、查看状态日志、卸载面板服务、完全卸载（面板 + mihomo + 数据文件，保留可执行文件）；无参数运行进入交互菜单 |
| `backups/` | 配置备份（最近 5 份） |

## 前置条件

- Linux 系统，mihomo 已由用户自行安装（推荐 deb 包，面板自动识别 systemd 单元中的二进制与 `-d` 数据目录；未安装时面板拒绝登录）
- 面板需以 root 运行（操作 systemd reload、写入 mihomo 数据目录配置）
- x86-64 与 ARM 均支持；ARM 机器上的可执行文件需在该机自行 `./build.sh` 打包（见「打包为可执行文件」）

## 技术栈

- Python 3.13+
- FastAPI + Uvicorn
- httpx
- PyYAML

## 更新日志

### v26.11.21a

**变更**

- 版本号推进一个字母后缀，用于验证 GitHub 与 Gitee 双平台发布流程；程序功能与 v26.11.21 相同
- 仓库忽略规则收敛：私有脚本与发版产物统一放 `.mydata/`（不随仓库分发、不进版本库），并补齐运行时数据（订阅地址、登录令牌、内核配置、缓存库）与凭据文件的兜底忽略项

### v26.11.21

**新增**

- 首次安装阻断弹窗与「升级内核」弹窗均显示「本机适配建议」：按 `/proc/cpuinfo` 指令集判定本机可用的最高 GOAMD64 档位（升级场景另显示当前内核实际档位），并直接给出应下载的安装包文件名
- 选中安装包后即时提示架构 / 档位 / 平台是否合适，不等上传完再报错
- 「打开面板」免密跳转：按 metacubexd 的 `#/setup?hostname=&port=&secret=` 跳转参数打开内核面板，连上即跳到 `#/overview`；密钥取自基础设置的 `secret`（留空则不带），仅存在于跳转的 URL 片段中，不写入任何静态文件、不随请求发给内核；改完 `secret` 后再点即按新密钥连接

**变更**

- 档位判定改用指令集精确匹配（v2 需 `sse4_2+popcnt+cx16+movbe`，v3 另需 `avx+avx2+bmi1+bmi2+fma+osxsave`），非 x86-64 架构不再套用档位
- 识别官方发布页的全部资源命名变体：`go120/go122/go124` 工具链变体、`compatible`（按 v1 处理）、`softfloat/hardfloat`、`abi1/abi2`，以及 `armv5/armv6/armv7/386/loong64/mips*/ppc64le/riscv64/s390x` 等架构与 `.rpm/.zip/.pkg.tar.zst` 等后缀
- 安装包 GOAMD64 档位改为读取**包内二进制内嵌的编译参数**判定（当前内核档位同样读二进制），被改名、文件名不带档位标识的包也能正确校验；二进制读不出时回退文件名，仍读不出则拒绝升级
- 升级/安装新增平台校验：拒绝 darwin / windows / freebsd / android 等非 Linux 包，档位超出本机 CPU 上限的包直接拒绝（避免装上后 SIGILL 崩溃）
- 非 amd64 机器（arm64 / armv7 / loong64 / riscv64 等）不再做档位判定，也不再解包读编译参数；`.gz` 安装失败的提示按本机架构给出应上传的文件名（如 `mihomo-linux-arm64-*.gz`）
- 档位选择指南补充指令集判定条件、手动核对脚本与发布包命名说明
- 文档明确 ARM 架构需自行打包：可执行文件与打包机架构绑定，ARM / 树莓派 / Armbian 等设备须在目标机上重新 `./build.sh`（32 位 armv6/armv7 改用源码运行），并说明非 Debian 系 ARM 发行版的功能可用范围

### v26.11.12

**新增**

- 「极光深空」深色界面主题：4 条极光光带、HUD 网格、星点与缓慢扫描线组成的氛围层，半透明玻璃卡片、渐变发光描边
- 卡片光标微光与悬停浮起效果，状态徽章呼吸发光

**变更**

- 主按钮、危险按钮、输入框、Toast、弹窗、滚动条统一为极光青紫配色
- 手机端窄屏布局、按钮排布与字号重新适配，支持系统「减少动效」偏好
- 文档补充界面主题与命令面板 `Ctrl/Cmd + K` 说明
- 快速开始文档改用 `uv run` 启动，修改端口补充面板内操作方式
- 版本号统一为 `26.11.12`（`pyproject.toml`、`uv.lock` 与静态资源缓存参数 `?v=`）

**修复**

- 未安装 mihomo 阻断弹窗中，深色背景下深棕文字对比度过低的问题

## 许可

本项目基于 [MulanPSL-2.0（木兰宽松许可证 第 2 版）](LICENSE) 开源，中英文文本具有同等法律效力。内置的 metacubexd 面板、mihomo 内核与 GeoIP/GeoSite 数据分别遵循其上游项目许可。
