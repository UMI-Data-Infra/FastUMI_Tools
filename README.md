# FastUMI Tools

FastUMI Tools 是 FastUMI 设备的一体化本机管理控制台。它将原先分散在版本检测、`FastUMI_SDK`、`FastUMI_Monitor` 和 `FastUMI_Camera` 中的常用能力集中到一个简洁的 Web 界面中。

项目版本以根目录的 [`VERSION`](VERSION) 为唯一来源；已发布版本及安装包见 [GitHub Releases](https://github.com/KM-Data-Pipeline/FastUMI_Tools/releases)。

> 系统要求：仅支持 **Ubuntu 20.04 LTS（Focal）amd64**，不支持 Ubuntu 22.04（Jammy）。软件会在不受支持的系统上阻止 SDK 安装和固件刷新。

## 功能

- 自动检测 FastUMI / XVisio USB 相机、序列号、USB 2.0/3.x、固件和 SDK 版本。
- 在 Web 界面选择并安装 2026-04-30 或 2026-05-22 XVSDK，仅提供 Ubuntu 20.04 Focal 版本。
- 选择并刷新 2026-04-30 或 2026-05-14 PMD-TOF 固件。
- 固件刷新前检查单设备、USB 连接、进程占用、SDK 最低版本和资源 SHA-256。
- ROS1 设备发现、Topic 频率检测、ROS1 wrapper 安装和 RViz 可视化。
- V4L2 低延迟相机预览和 RGB 标定控制台。
- 后台任务实时日志与历史记录。

Web 服务只监听 `127.0.0.1`。修改系统的操作要求桌面入口提供的临时访问令牌，后端只接受预定义动作和参数，不提供任意命令接口。

## 界面入口

安装后在应用菜单中打开 **FastUMI Tools**。默认地址：

```text
http://127.0.0.1:8765
```

请使用桌面入口打开，入口会自动携带本机操作令牌。直接输入网址可以查看状态，但不能执行 SDK 安装或固件刷新。

## 受管版本

| 类型 | 版本 | 系统/设备 | 状态 |
| --- | --- | --- | --- |
| XVSDK | 2026-04-30 | Ubuntu 20.04 Focal | 稳定版，可回退 |
| XVSDK | 2026-05-22 | Ubuntu 20.04 Focal | 正式推荐版 |
| 固件 | 2026-04-30 | PMD-TOF 标准版相机 | 稳定版，可回退 |
| 固件 | 2026-05-14 | PMD-TOF 标准版相机 | 推荐版，搭配 2026-05-22 SDK |

所有资源及兼容关系由 [`payloads/manifest.json`](payloads/manifest.json) 管理。XVSDK 包内部 Debian 版本都为 `3.2.0`，因此 FastUMI Tools 另外记录交付版本、文件哈希和安装历史。

## 开发运行

要求 Python 3.8 或更新版本。主服务只使用 Python 标准库。

```bash
cd /home/yan/Github/FastUMI_Tools
export PYTHONPATH="$PWD/app"
export FASTUMI_TOKEN_FILE=/tmp/fastumi-tools-token
export FASTUMI_STATE_DIR=/tmp/fastumi-tools-state
python3 -m fastumi_tools.server --port 8765
```

开发模式下可以查看完整界面和只读诊断。SDK 安装与固件刷新必须通过安装后的 root systemd 服务运行。

## 测试

```bash
./scripts/test.sh
```

测试包括 Python 单元测试、生产资源 SHA-256、JavaScript 语法检查、Debian 构建和包内容检查。

## 构建

构建核心 Debian 包：

```bash
./scripts/build_deb.sh
```

输出：

```text
dist/fastumi-tools_<VERSION>_amd64.deb
```

构建包含全部 SDK 和固件的离线包：

```bash
./scripts/build_offline_bundle.sh
```

离线电脑解压后运行：

```bash
sudo ./install.sh
```

核心 `.deb` 管理程序、systemd 服务、桌面入口和系统卸载；离线包管理不断更新的大型 SDK/固件资源。

## 版本管理

- `VERSION` 是 FastUMI Tools 软件版本的唯一来源。Python 后端、Web 界面、Debian 包名和离线包名都会在运行或构建时读取它。
- 发布时先按语义化版本规则更新 `VERSION`，合并到 `main` 后创建同名 Git tag，例如 `v0.1.2`，并用 GitHub Release 保存对应安装包。
- 日常功能开发使用短期分支，合并后删除；不为每个历史版本保留长期 branch。历史版本通过不可移动的 Git tag 查询。
- `payloads/manifest.json` 只管理 SDK、固件、兼容关系及 `catalog_version`，不再重复记录软件版本。资源更新与软件版本可以独立演进。

## 项目结构

```text
app/                         Python 后端与 Web 前端
assets/                      RViz、监控、相机预览与标定资源
payloads/                    SDK、固件和版本清单
packaging/                   Debian、systemd 与桌面入口
scripts/                     构建、测试与离线安装脚本
tests/                       单元与资源完整性测试
```

## 固件刷新安全规则

1. 只允许连接一台 FastUMI 相机。
2. USB 2.0 连接会被阻止，必须使用 USB 3.x。
3. 相机被 ROS、采集程序或其他进程占用时会被阻止。
4. 目标固件的最低 SDK 不满足时会被阻止。
5. 资源必须通过清单中的 SHA-256 校验。
6. 用户必须在界面二次确认相机序列号、目标版本及不可断电提示。
7. 软件不会自动杀死采集进程，也不会自动重启电脑。

## 来源项目

本仓库整合并逐步替代以下独立工具仓库的使用入口，但不会修改它们的历史：

- FastUMI SDK：SDK、固件与厂商工具资源。
- FastUMI Monitor：ROS Topic、设备发现、RViz 模板与轨迹工具。
- FastUMI Camera：V4L2 预览与厂商 RGB 标定程序。
- Xvision Version Installer：SDK、固件与 USB 自动诊断逻辑。

大型 SDK、固件和完整离线包通过 GitHub Releases 与百度云盘镜像发布，源码仓库保留版本清单和校验信息，并继续由同一份 manifest 管理。
