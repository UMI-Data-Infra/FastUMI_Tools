# FastUMI Tools

FastUMI Tools 是 FastUMI 设备的一体化本机管理控制台。它将原先分散在版本检测、`FastUMI_SDK`、`FastUMI_Monitor` 和 `FastUMI_Camera` 中的常用能力集中到一个简洁的 Web 界面中。

项目版本以根目录的 [`VERSION`](VERSION) 为唯一来源；已发布版本及安装包见 [GitHub Releases](https://github.com/UMI-Data-Infra/FastUMI_Tools/releases)。

Copyright © 2026 FastUMI Team. All rights reserved. 本项目是专有软件，允许安装和使用官方未修改版本，但禁止擅自修改、制作衍生版本或重新分发。完整条款见 [`LICENSE`](LICENSE)，许可与商务问题请联系 [yding25@binghamton.edu](mailto:yding25@binghamton.edu)。

> 系统要求：仅支持 **Ubuntu 20.04 LTS（Focal）amd64**，不支持 Ubuntu 22.04（Jammy）。软件会在不受支持的系统上阻止 SDK 安装和固件刷新。

## 功能

- 相机插入后自动检测序列号、USB 2.0/3.x，并通过隔离的只读 SDK 运行时读取真实固件；无需先安装或刷新 SDK/固件。
- 分别显示系统已安装的 XVSDK 与设备读取所用的 SDK 交付版本，避免把探测运行时误认为系统 SDK。
- 在“SDK 和固件”页明确选择“一代相机”或“二代相机”，只显示对应代际的 XVSDK，避免混装。
- 一代相机可安装 2026-04-30 / 2026-05-22 SDK，并刷新 2026-04-30 / 2026-05-14 PMD-TOF 固件。
- 二代相机可安装稳定的 2026-05-08 无 ToF SDK，或选择尚待验证的 2026-08-12 含 ToF SDK；不提供固件刷新入口。
- 固件刷新前检查单设备、USB 3.x、真实 SDK 运行时、进程释放、HID 可读写、镜像版本和资源 SHA-256；自动停止受管 ROS。
- USB Loader 与主固件均要求出现 DFU 下载完成证据，写入后再读取相机真实版本；未通过验真绝不会记录为成功。
- 受管 ROS1 数据源、带消息类型验证的 Topic 频率检测、ROS1 wrapper 安装，以及实时位姿与原生轨迹合并的 RViz 可视化。
- V4L2 低延迟相机预览和经过进程就绪验证的双终端 RGB 标定控制台；停止 ROS 数据源时自动恢复 UVC 视频设备。
- 浅灰侧栏与设备列表、可切换的设备详情、清晰的工具面板，以及浅色/深色/跟随系统主题和完整中英文切换。
- 后台任务实时日志与历史记录。

Web 服务只监听 `127.0.0.1`。修改系统的操作要求桌面入口提供的临时访问令牌，后端只接受预定义动作和参数，不提供任意命令接口。

> XVSDK ROS wrapper 与 V4L2 预览会竞争同一相机的 USB 接口，不能同时运行。请在“数据监控”中启动 ROS 数据源；需要实时预览时，点击“停止并恢复相机预览”，程序会停止受管 ROS 进程并重新绑定 `uvcvideo`。

## 界面入口

安装后在应用菜单中打开 **FastUMI Tools**。默认地址：

```text
http://127.0.0.1:8765
```

请使用桌面入口打开，入口会自动携带本机操作令牌。直接输入网址可以查看状态，但不能执行 SDK 安装或固件刷新。

## 用户使用说明

### 启动与首次检查

1. 将相机直接连接到电脑的 USB 3.x 接口；刷新一代固件时只连接一台 FastUMI 相机。
2. 从应用菜单启动 **FastUMI Tools**，不要直接以 root 用户运行浏览器。
3. 页面每 5 秒检查一次热插拔。空闲相机接入后会自动显示实际固件和“读取 SDK”，不需要先执行 SDK 安装或固件刷新。
4. 在“SDK 和固件”页面先明确选择“一代相机”或“二代相机”，再选择该代际的系统 SDK。
5. 一代相机可以继续选择固件并按页面提示完成刷新；刷新过程中不要拔线、断电、关闭服务或启动其他相机程序。
6. 二代相机页面只提供 SDK 安装。二代固件必须使用 Windows 升级工具，FastUMI Tools 不会在 Linux 中尝试刷新。
7. 操作完成后，以页面重新读取到的实际版本为准；仅有任务记录而没有设备版本验真，不会显示为刷新成功。

自动读取只会在设备空闲时运行；检测到 ROS、实时预览或其他进程占用后会等待释放。探测器使用资源包中的隔离运行库，不会把 XVSDK 安装到系统目录，并会在读取后恢复 UVC 接口。管理员可在 `/etc/fastumi-tools.conf` 中设置 `FASTUMI_AUTO_DEVICE_PROBE=0` 关闭自动探测。

### 数据监控、预览与标定

- “数据监控”中的 ROS 数据源与“相机工具”中的实时预览会占用同一相机接口，不能同时运行。
- 使用 SLAM、Visual Pose、Topic 频率和 RViz 轨迹前，先在“数据监控”中启动 ROS 数据源。
- 打开实时画面前，先停止 ROS 数据源；也可以使用页面中的“停止并恢复相机预览”。
- 预览窗口可以在 Web 页面中点击“关闭预览”结束，不需要在终端中手动结束进程。
- RGB 标定前应关闭预览和 ROS 数据源，再从“相机工具”启动标定控制台。

### 升级

配置过官方软件源后，使用以下命令升级软件及 SDK/固件资源：

```bash
sudo apt update
sudo apt install fastumi-tools
```

升级后可用下面的命令确认已安装版本和服务状态：

```bash
dpkg-query -W fastumi-tools fastumi-tools-resources
systemctl is-active fastumi-tools
```

### 卸载

```bash
sudo apt remove fastumi-tools fastumi-tools-resources
```

卸载主程序不会自动卸载用户另外安装的 XVSDK，也不会自动修改相机中已经刷入的固件。

## 通过 APT 安装

官方 APT 软件源仅支持 Ubuntu 20.04 Focal amd64。首次使用时下载并检查软件源配置脚本：

```bash
curl -fsSLO https://umi-data-infra.github.io/FastUMI_Tools_APT/install-fastumi-repository.sh
less install-fastumi-repository.sh
sudo sh install-fastumi-repository.sh
```

此后可直接通过 APT 安装和升级：

```bash
sudo apt update
sudo apt install fastumi-tools
```

APT 默认同时安装 `fastumi-tools-resources`，其中包含经过 SHA-256 清单校验的 XVSDK 和固件资源。仓库的 `InRelease` 由 FastUMI Team 专用密钥签名，系统通过独立的 `signed-by` keyring 验证，不会把该密钥加入全局 APT 信任范围。

官方 APT 签名密钥指纹：

```text
8572 EAED 4E96 2EBF D857 D46F 2CD6 6EE3 737A 4EC8
```

源码仓库与 [`FastUMI_Tools_APT`](https://github.com/UMI-Data-Infra/FastUMI_Tools_APT) 软件包仓库保持私有，不授予修改或制作衍生版本的许可。

## 受管版本

| 类型 | 版本 | 系统/设备 | 状态 |
| --- | --- | --- | --- |
| XVSDK | 2026-04-30 | 一代相机 · Ubuntu 20.04 Focal | 稳定版，可回退 |
| XVSDK | 2026-05-22 | 一代相机 · Ubuntu 20.04 Focal | 一代推荐版 |
| XVSDK | 2026-05-08 | 二代相机 · Ubuntu 20.04 Focal | 二代推荐版，无 ToF，较稳定 |
| XVSDK | 2026-08-12 | 二代相机 · Ubuntu 20.04 Focal | 含 ToF，待验证 |
| 固件 | 2026-04-30 | 一代 PMD-TOF 标准版相机 | 稳定版，可回退 |
| 固件 | 2026-05-14 | 一代 PMD-TOF 标准版相机 | 推荐版，搭配 2026-05-22 SDK |

所有资源及兼容关系由 [`payloads/manifest.json`](payloads/manifest.json) 管理。XVSDK 包内部 Debian 版本都为 `3.2.0`，因此 FastUMI Tools 另外记录交付版本、相机代际、文件哈希和安装历史。诠视自带的 `xvsdk-viewer_3.2.0-2_amd64.snap` 当前不纳入 FastUMI Tools。

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

构建独立的 SDK 与固件资源包：

```bash
./scripts/build_resources_deb.sh
```

核心包通过 `Recommends` 安装资源包；这样软件版本与资源清单可以独立更新，同时 `sudo apt install fastumi-tools` 在默认 APT 配置下仍会安装完整功能。

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
- 发布时先按语义化版本规则更新 `VERSION`，合并到 `main` 后创建同名 Git tag，例如 `v0.1.3`，并用 GitHub Release 保存对应安装包。
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

以下规则只适用于一代相机。二代固件升级仅支持 Windows，FastUMI Tools 不提供二代固件资源或刷新操作。

1. 只允许连接一台 FastUMI 相机。
2. USB 2.0 连接会被阻止，必须使用 USB 3.x。
3. 相机被 ROS、采集程序或其他进程占用时会被阻止。
4. 目标固件的最低 SDK 不满足时会被阻止。
5. 写入前必须检测到并打开目标相机的 XVisio HID 设备；必要时只重绑该相机的 HID 接口。
6. USB Loader 和主固件分别验证 DFU 下载完成，任一步骤异常立即停止。
7. 相机重启后使用 XVSDK 读取真实固件版本，只有与目标版本一致才写入成功记录。
8. 资源必须通过清单中的 SHA-256 校验。
9. 用户必须在界面二次确认相机序列号、目标版本及不可断电提示。
10. 软件只自动停止受管 ROS，不会强制结束其他采集进程，也不会自动重启电脑。

## 来源项目

本仓库整合并逐步替代以下独立工具仓库的使用入口，但不会修改它们的历史：

- FastUMI SDK：SDK、固件与厂商工具资源。
- FastUMI Monitor：ROS Topic、设备发现、RViz 模板与轨迹工具。
- FastUMI Camera：V4L2 预览与厂商 RGB 标定程序。
- Xvision Version Installer：SDK、固件与 USB 自动诊断逻辑。

大型 SDK、固件和完整离线包通过 GitHub Releases 与百度云盘镜像发布，源码仓库保留版本清单和校验信息，并继续由同一份 manifest 管理。
