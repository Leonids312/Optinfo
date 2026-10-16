# Optinfo 客户端

Flutter 原生客户端，支持 Android 和 Windows。个人阅读器通过现有 Tailscale 网络访问校园服务器；应用打开后读取已生成内容，不集成厂商推送 SDK。

## 连接

1. 手机 / 电脑先连接与服务器相同的 Tailscale 网络。
2. 打开 Optinfo，点击「设置连接」。
3. 填写管理员提供的独立 Optinfo HTTPS 服务地址（不含 `/api`）和设备访问令牌。
4. 「验证并保存」会实际读取 bootstrap 接口，再将令牌写入系统安全存储。仓库与安装包不包含服务器地址、令牌或学校账号。

应用有今日、历史、企业、收藏四个入口。宽屏使用左侧导航，窄屏使用底部导航。今日与历史查询使用本地缓存，切换页面时保留筛选和阅读位置，优先显示已加载内容。缓存过期后后台刷新，也可手动刷新；离线时会显示缓存状态。今日的缓存按北京时间日期区分，不会把昨天内容冒充今天。搜索 / 期刊筛选覆盖全部日期，日期按钮返回指定日期简报。公司详情汇总其全部历史资讯。

长文章的正文与宽屏右侧探究面板各自滚动；手机等窄屏可从固定入口随时打开探究面板。问答草稿在当前阅读页内保留，不将问答记录写入本地持久缓存。

关注变更从次日开始生效。每条资讯有一个 DeepSeek 会话，服务端在七天到期时删除；请求失败时客户端保留问题输入，允许重试。收藏不受会话到期影响。

## 开发与打包

使用 Flutter stable（Dart 3.6 或更新）。

```powershell
flutter pub get
flutter analyze
flutter test
flutter build apk --release
flutter build windows --release
```

Android 构建需要 Android SDK / JDK，Windows 构建需要 Visual Studio Desktop development with C++。应用只需要互联网访问，无摄像头、定位或厂商推送权限。发布 APK 应使用私有签名密钥；CI 测试安装包的签名方式由仓库工作流说明。

接口契约见 `../docs/API.md`。安全默认值要求 HTTPS；仅调试模式允许 localhost HTTP。客户端不会绕过 TLS 验证，也不会向重定向目标转发 Bearer 令牌。

## 自动发布

将 `pubspec.yaml` 的版本号和构建号递增，添加 `docs/releases/v版本号.md`，提交后推送同名 `v版本号` 标签。GitHub Actions 通过后端测试、客户端分析与测试、两端构建和 APK 签名验证后，自动发布 Windows ZIP、Android APK、`SHA256SUMS.txt` 和 `BUILD_INFO.json`。发布失败的草稿可重跑工作流；已经公开的发布包不会被重跑覆盖。私有安卓签名材料继续从仓库加密 Secrets 读取。

中文界面内置 Noto Sans SC 字体，离线使用不依赖 Google 字体 CDN。字体来自 Google Fonts `ofl/notosanssc`，按 SIL Open Font License 1.1 分发；完整许可见 `OFL-NotoSansSC.txt`，也打包在应用资产内。
