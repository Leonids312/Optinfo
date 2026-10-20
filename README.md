# Optinfo

个人用光学、半导体与重大事件简报。服务器定时采集可信来源，由官方 DeepSeek Harness SDK 整理；Windows 和 Android 客户端通过 Tailscale 私网读取。

## 使用

从 [Releases](https://github.com/Leonids312/Optinfo/releases) 下载 Android APK 或 Windows ZIP。Windows 解压整个目录后运行 `optinfo.exe`。两端先连接同一 Tailscale 网络，再在 Optinfo 的连接设置中填写服务器 HTTPS 地址和个人访问令牌。地址与令牌由部署者私下提供，公开安装包不包含凭据。

- 今日简报分为研究进展、产业动态、重大事件；支持日期、关键词、期刊和公司检索。
- 兴趣选择按分区保存，从下一个北京时间日期生效；已经生成的日报保留原貌。
- 可收藏资讯与企业。企业图鉴显示业务、上市信息、薪酬证据和相关历史资讯；缺乏可靠薪酬来源时显示待补充。
- 每条资讯独立问答，会话从创建起保留七天，过期清除消息和 Harness 会话文件；之后可以重新提问。
- 论文先概括成果，再展示背景、方案、结果、关键原图和评价，中文图注与 LaTeX 公式贯穿全文。必要正文或图片未取得时留待补充。
- 检索覆盖 Nature / Science 系列与按中科院口径选入的光学、材料期刊；企业资讯兼顾多个公司，近期不足时由近及远补充未推送的事件。
- [推送内容 skill](skills/optinfo-content/SKILL.md) 记录用户的选题和文风偏好，并由 Harness 每次生成、问答直接读取。正文聚焦进展、机制和影响，避免反复评价新闻可靠性。

第一版采用打开客户端后读取/刷新，不接厂商推送；关闭应用时不会收到系统级重大事件通知。服务器仍定时检查重大事件，重新打开后即可阅读。

## 结构

```
client/              Flutter Windows / Android 客户端
backend/optinfo/      FastAPI、SQLite、来源采集、Harness、定时任务
content/             可维护的来源和兴趣配置
deploy/              部署文件及运行说明
docs/                API 和维护说明
```

服务只监听回环地址，由 Tailscale Serve 的独立 HTTPS 端口转发。数据库和模型密钥留在服务器，客户端仅保存个人访问令牌。模型处理来源文本及用户对该资讯的提问；不把学校账号交给模型。来源页面中的指令不应被执行，Harness 使用禁止文件/命令工具的专用配置。

## 开发

后端需要 Python 3.10+：

```sh
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
python -m pytest backend/tests -q
```

设置 `OPTINFO_TOKEN`、数据目录和 `DEEPSEEK_API_KEY` 后，在 `backend/` 下运行：

```sh
uvicorn optinfo.api:app --host 127.0.0.1 --port 18766 --workers 1
```

客户端需要 Flutter stable：

```sh
cd client
flutter pub get
flutter analyze
flutter test
flutter build apk --release
flutter build windows --release
```

Android 发布签名从 `android/key.properties` 读取，密钥不入库。GitHub Actions 运行测试并构建两端。Windows 包尚未购买代码签名证书。

## 数据边界

默认优先使用开放接口、RSS、公开元数据和获准公开全文。校园 IP 的订阅访问能力不等同于无限制批量下载权限；出版社拒绝、验证码和不可访问页面会保留来源链接与获取状态，不尝试绕过。企业工资仅作为带出处、地区、年份和口径的参考信息。

`server/`、`files/`、本地密钥、开题报告原文件、会话和数据库均被 Git 忽略。公开仓库不包含用户账号或服务器登录信息。
