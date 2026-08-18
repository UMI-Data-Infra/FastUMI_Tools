# FastUMI Tools 官方 APT 软件源

这里是 FastUMI Team 发布的官方签名二进制软件源，面向 **Ubuntu 20.04 LTS（Focal）amd64**。源代码不在本公开仓库中提供。

## 安装

首次使用时下载并检查软件源配置脚本：

```bash
curl -fsSLO https://km-data-pipeline.github.io/FastUMI_APT/install-fastumi-repository.sh
less install-fastumi-repository.sh
sudo sh install-fastumi-repository.sh
sudo apt install fastumi-tools
```

也可以在配置软件源后立即安装：

```bash
sudo sh install-fastumi-repository.sh --install
```

安装程序包含两个包：

- `fastumi-tools`：Web 控制台、系统服务和桌面入口。
- `fastumi-tools-resources`：经过 SHA-256 校验的官方 XVSDK、固件和厂商工具资源；默认随主程序一起安装。

安装后从应用菜单打开 **FastUMI Tools**。服务只监听本机地址 `http://127.0.0.1:8765`，请使用桌面入口打开，以便自动携带本机操作令牌。

## 升级和检查

```bash
sudo apt update
sudo apt install fastumi-tools
dpkg-query -W fastumi-tools fastumi-tools-resources
systemctl is-active fastumi-tools
```

## 使用注意事项

- 刷新固件时只连接一台相机，并使用 USB 3.x 接口。
- 刷新过程中不要拔线、断电或启动其他相机程序。
- ROS 数据源与相机实时预览会竞争同一 USB 接口，不能同时运行。
- 预览可以通过 FastUMI Tools Web 页面中的“关闭预览”停止。
- 软件以相机重新报告的真实固件版本作为刷新成功依据。

## 卸载

```bash
sudo apt remove fastumi-tools fastumi-tools-resources
```

卸载 FastUMI Tools 不会自动卸载单独安装的 XVSDK，也不会回退相机固件。

## 软件源签名

APT 签名密钥指纹：

```text
8572 EAED 4E96 2EBF D857 D46F 2CD6 6EE3 737A 4EC8
```

软件源使用独立的 `signed-by` keyring 验证，不会把密钥加入系统的全局 APT 信任范围。

## 授权和联系

Copyright © 2026 FastUMI Team. All rights reserved.

FastUMI Tools 是专有商业软件。允许安装和使用官方未修改版本；未经 FastUMI Team 书面许可，禁止修改、制作衍生版本或擅自重新分发。软件安装包内的 `LICENSE` 为完整授权条款，第三方 SDK、固件和厂商工具仍分别受其权利人的条款约束。

授权、商务合作和问题反馈：[yding25@binghamton.edu](mailto:yding25@binghamton.edu)
