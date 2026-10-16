# 部署与维护

Optinfo 使用独立的服务用户、数据目录、回环端口和 Tailscale Serve HTTPS 端口。已有服务的 Serve 路由、监听端口和凭据不得覆盖。

对于 CentOS 7，官方 Harness Linux wheel 需要 glibc 2.28+，不能直接安装到宿主环境。本部署使用指定目录内的 Ubuntu 22.04 用户空间（chroot），保留宿主系统与现有服务依赖。它不增加服务器，也不改变校园网出口。该运行环境用于兼容；仍需对宿主系统进行常规安全维护。

部署目录：

```
/share/xdq/Optinfo/
  runtime/                 Ubuntu 用户空间
    app/                   应用代码
    data/                  SQLite、缓存与聊天
    config/                私有配置（不进 Git）
    harness/               指向独立 Harness 目录的挂载点
  downloads/               校验过的运行环境包
  ops/                     运维脚本与上线前路由快照
/share/xdq/DeepSeek Harness/
  venv/                    官方 SDK 与服务 Python 依赖
  optinfo-runtime/         专用 Harness profile 与运行文件
```

服务器使用北京时间。每日 timer 04:00 运行，启用 `Persistent=true` 以补跑停机错过的任务；重大事件另有周期任务。API 只使用一个 Uvicorn worker，由 SQLite WAL 和任务文件锁协调写入。失败来源独立记录，不以虚构条目填满版面。

升级前备份 SQLite（使用 SQLite backup API）与私有配置。备份中包含私人收藏和聊天，不得发布。七天聊天清理针对在线库与 Harness 会话，若运维另建历史备份，也应给备份设置匹配的保留政策。

回滚先停止新增 Optinfo timers/service，再恢复 Optinfo 的旧代码；只移除新增的 Tailscale Serve 端口，不能执行全局 `tailscale serve reset`。操作前后核对现有 Kazumi 的 443 路由与 HTTP 响应状态。

Android 签名私钥必须离线备份，后续版本使用同一密钥才能覆盖安装。GitHub Actions 只接收加密 secrets，在临时 runner 内签名；签名材料不作为构建 artifact 上传。
