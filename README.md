# 龙族：卡塞尔之门 Android 研究工程

本分支用于《龙族：卡塞尔之门》Android 客户端的 APK 检查、设备启动验证、资源结构研究与后续适配。

> 本仓库默认分支 `main` 已切换为龙族工作基线。原《幻书启世录》工程完整保存在 `backup/huanshu-pre-dragon-2026-11-20` 备份分支；在龙族工程完成验证前，不要删除该备份分支。

## 当前基线

- 客户端包名：`com.zulong.drc.mi`
- 客户端版本：`2.1.5`（versionCode `30623`）
- Android：minSdk 23，targetSdk 33
- 引擎：Unreal Engine 4.27.2
- UE4 原生库：当前 APK 仅包含 `arm64-v8a`
- 纹理：ASTC cooked flavor
- 图形接口：清单声明 OpenGL ES 3.1；UE4 元数据标记不使用 Vulkan
- 资源：APK 内含 `assets/res_base/package/` 下的资源包

APK 原文件不提交到 Git。它体积较大且属于第三方游戏客户端；请在本地私下保存，并通过脚本参数提供给检查工具。

## 目录

- `docs/APK_PROFILE.md`：已验证的 APK 元数据
- `docs/MIGRATION_PLAN.md`：迁移步骤与技术风险
- `scripts/inspect_apk.py`：无第三方 Python 依赖的 APK 静态检查
- `scripts/run_on_device.sh`：通过 adb 安装、启动并采集日志

## 检查 APK

需要 Python 3.10+：

```bash
python scripts/inspect_apk.py /path/to/dragon.apk
```

## GitHub ARM64 云端首次启动测试

仓库的 Actions 中提供 `Dragon Android first-boot download test` 工作流。它使用 GitHub 的 `ubuntu-24.04-arm` 托管运行器和 ARM64 Android 35 系统映像，下载 APK、安装、启动并等待首次资源下载，同时保存 logcat、屏幕截图和外部存储数据目录清单。由于 APK 不在 Git 仓库中，手动运行工作流时需要提供可由 GitHub runner 直接访问的 HTTPS APK 下载地址。不要把带账号口令或长期访问令牌的 URL 粘贴到公开仓库工作流输入中。该工作流只上传诊断信息，不会把游戏资源包发布到公开仓库。

## 在 Android 设备上安装与启动

需要 Android Platform Tools（adb），并在设备上启用 USB 调试：

```bash
bash scripts/run_on_device.sh /path/to/dragon.apk
```

建议先在 ARM64 真机上测试。x86_64 模拟器即使能安装 APK，也可能因缺少 ARM64 UE4 原生库而无法启动游戏。

## 当前边界

本工程目前是客户端检查与适配基线，不代表已完成龙族私有服务器、离线模式或完整游戏重建。APK 中的资源包也不是可直接替换进幻书客户端的 NPK 资源。后续必须分别验证客户端引擎、资源加载、登录链路和服务端协议。
