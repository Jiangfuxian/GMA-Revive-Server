# 龙族 APK 检查记录

检查对象：用户提供的两个 RAR 分卷解出的 APK。

## 完整性

- 文件大小：900,767,097 bytes
- SHA-256：`f48eee0b4d547349c7e8d5dc158fa3cf432e5510c3155d7aef0b599b358245e1`
- ZIP 条目数：586
- ZIP 完整性检查：通过（`ZipFile.testzip()` 未发现损坏条目）

这说明当前提取出的 APK 容器可完整读取；不等于已验证所有运行时资源都能被游戏正常加载。

## Android 元数据

| 项目 | 值 |
| --- | --- |
| 包名 | `com.zulong.drc.mi` |
| versionCode | `30623` |
| versionName | `2.1.5` |
| minSdkVersion | `23` |
| targetSdkVersion | `33` |
| 启动 Activity | `com.epicgames.ue4.SplashActivity` |
| 游戏 Activity | `com.epicgames.ue4.GameActivity` |
| Application | `com.mx.shell.MXApplication` |

## 引擎与图形

- Unreal Engine：`4.27.2`
- Project：`Azure`
- Cooked flavor：`ASTC`
- UE4 原生库位于 `lib/arm64-v8a/`，其中包括 `libUE4.so`
- APK 的 UE4 元数据声明 OpenGL ES 路径，不启用 Vulkan
- Manifest 要求 OpenGL ES 3.1，并声明 ASTC 纹理压缩支持

APK 内虽然还有面向 x86/x86_64 的其他 SDK 库，但这不代表 UE4 游戏本体具备 x86_64 ABI。启动测试必须以 `libUE4.so` 的 ABI 为准。

## 资源包

`assets/res_base/package/` 下可见以下 17 个包：

- buildings
- cinematics
- configs
- data
- effects
- engine
- fight
- lua
- maps
- miscs
- models
- parentmaterials
- plugins
- scenes
- shadercode
- sound
- ui

这些文件使用 PNG 扩展名，但不能据此视为普通 PNG 图片。应先分析其文件头、索引和内部结构，再选择适当的解析方式。

## 已知限制

1. 当前只确认 APK 容器和 Manifest 可读取，尚未在 Android 设备上完成实际启动验证。
2. APK 内的游戏资源不等于服务端数据、账号系统或完整离线运行环境。
3. 不能把幻书的 NPK 资源索引或 Go 服务端协议直接套用到龙族。
4. 正式测试前应先在用户自己的 ARM64 设备上验证安装、启动、资源加载和登录阶段，并保存 logcat。
