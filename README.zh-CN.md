# EP AI DLP — 端点 AI 数据保护

**探索如何在 Windows 应用向 AI 服务发送敏感数据之前进行检查和控制。**

[English](README.md) · [한국어](README.ko.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md)

> 这是实验性研究预览，不是生产级 DLP 替代方案，也不保证全面防止数据泄露。英文 README 是基准文档；管理控制台使用英文。

截图来自实际控制台，使用合成 UI 数据。设备名称、计数和策略状态仅用于展示，不代表客户使用情况或实时拦截证据。

![Console overview](docs/assets/console-overview.png)

<table><tr><td width="50%"><img src="docs/assets/console-policy.png" alt="Policy"></td><td width="50%"><img src="docs/assets/console-devices.png" alt="Devices"></td></tr></table>

![Inspection events](docs/assets/console-events-dark.png)

## 已实现

- Rust 运行时：选择性 TCP/TLS 检查、有界请求解析和确定性检测。
- Windows 服务按配置的可执行文件路径捕获新进程；此路径无需专用浏览器配置文件或代理启动参数。
- 11 类规则：凭据模式、私钥标记、电子邮件、银行卡号、韩国手机号和居民登记号。
- 一次性注册、绑定设备的 Ed25519 签名策略、按规则阻止或监控，以及策略版本报告。
- Next.js、NestJS、PostgreSQL 控制台；事件仅包含元数据，不包含受保护的正文。

目前为单租户共享策略。目标域名和程序路径在代理端配置。不包括响应检查、文件内容提取、上下文分类或 Agent IAM。

```text
Windows application -> TCP capture -> Rust TLS / DLP -> AI destination
                           ^
                  signed policy / metadata
                           |
              Next.js -> NestJS -> PostgreSQL
```

## 启动本地控制台

需要 Node.js 22.19+、npm 和 Docker Compose。启动控制台不会自动保护端点。

```bash
git clone https://github.com/hellocosmos/ep-ai-dlp.git
cd ep-ai-dlp
npm ci
npm run bootstrap
docker compose --env-file .local/managed.env -f deploy/compose.yaml up -d
npm run build
```

在两个终端中分别运行 `npm run dev:server` 和 `npm run start:console`，打开 `http://127.0.0.1:3100`。随机初始凭据写入被忽略的 `.local/managed.env`。

## 证据与限制

在受控 Windows HTTPS 目标上，正常请求到达，合成敏感请求未增加接收计数。验证了服务重启后普通 Chrome 的允许和阻止行为，以及一次正常 ChatGPT 对话。这不证明广泛的服务兼容性。

**ChatGPT 辅助请求仍存在未解决的误报可能。真实文件上传、更多浏览器和服务、拦截提示、性能、重启与崩溃恢复、防篡改均需进一步验证。服务故障可能释放流量捕获，不能保证持续的故障关闭保护。尚未实现 macOS 端点捕获和不可变审计存储。**

## 为何公开

由网络安全产品架构师 Jaemyung Kim 设计并验证，借助 AI 编程代理实施。公开架构、威胁模型、验收依据和限制，而不只展示拦截演示。

原创代码采用 MIT 许可证；Windows redirector、WinDivert 等第三方组件保留各自许可证。早期 Python 浏览器和 .NET 实验作为独立路径保留。公开版本不含运行凭据、原始实验记录或内部商业文档。

[English reference](README.md) · [Setup](docs/getting-started.md) · [Windows lab](docs/windows-lab.md) · [Architecture](docs/architecture.md) · [Validation](docs/validation.md) · [Screenshots](docs/screenshot-provenance.md) · [Security](SECURITY.md) · [Licenses](THIRD_PARTY.md)
