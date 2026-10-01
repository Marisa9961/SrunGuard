# Srun Guard

基于 Python + PySide6 的深澜 Srun 校园网守护工具，支持断网自动认证和托盘常驻。不预设学校或账号，网络功能独立实现。

## 特性

- 后台检测、断网确认、认证复检与失败退避。
- 一键启动／暂停，最小化到托盘，启动时自动保存设置。
- 时间支持秒、分钟、小时，最大退避可设为 7 天。
- 重要事件在界面显示，详细日志写入磁盘，重复消息限流合并。
- 密码可保存在系统凭据库，不写入配置或日志。

默认每 **30 分钟**检测一次，连续 **2 次**失败后尝试认证；认证失败后冷却 **3 小时**。均可在界面调整。

## 快速开始

主要面向 **Windows 10/11 x64**。已有构建产物时，直接运行 `SrunGuard.exe`，无需安装 Python。

源码运行需要 **Python 3.10+**，在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Linux/macOS 可使用 `.venv/bin/python`，托盘和凭据库支持取决于桌面环境，需自行验证。

首次启动填写认证网址、账号和密码，然后点击“启动守护”。网址使用 `https://主机[:端口]`，不要附带路径或参数；AC ID 和 HMAC 模式按实际服务器要求设置。

- **启动／暂停**：同一个主按钮切换；暂停不会注销网络。
- **立即检测／重连**：在线时不重复登录，离线时跳过确认和冷却等待。
- **退出**：默认关闭窗口只收起到托盘，请使用托盘菜单退出。升级前也应先退出旧进程。

仅适用于兼容的 Srun 门户，不支持验证码等额外交互，也不能修复 Wi-Fi、网线或上游网络故障。请使用可信的 HTTPS 认证地址。

## 日志

界面仅展示断网、认证结果、冷却等重要事件。需要排查时，展开“日志 → 日志设置与排查”打开详细日志。

Windows 默认数据目录为 `%APPDATA%\SrunGuard\`：

- `settings.json`：非密码配置，包含账号，请勿直接公开。
- `guard.log`：重要事件，可另选存储目录。
- `logs/diagnostics.log`：详细诊断，固定保存在默认目录。

日志自动轮转并合并重复消息，分享前请检查并脱敏。

## 构建与测试

使用 Nuitka 构建 Windows 单文件程序，首次构建需要联网下载工具链：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe build.py
```

产物为 `dist/SrunGuard.exe`，附带 SHA-256 校验文件。支持 `--mode standalone` 目录分发和 `--compiler msvc`。这是独立程序，不是所有依赖全静态链接。

运行测试（不需要真实校园网账号）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

欢迎提交 Issue 或 PR；请附复现步骤和脱敏日志，并为行为变更补充测试。

## 许可证

本项目采用 [MIT License](LICENSE)，Copyright © 2026 Marisa。

第三方依赖遵循各自的许可证；分发时请保留相应许可声明。
