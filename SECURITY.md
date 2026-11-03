# 安全策略（Security Policy）

## 适用范围

本应用是**本地单用户**工具，默认只监听 `127.0.0.1`，不对外网开放。

## 威胁模型与已采取的措施

| 威胁 | 措施 |
|---|---|
| 恶意网页跨源调用本地 API | CORS 白名单 + Origin 校验中间件（带非本地 Origin 的 `/api/*` 返回 403） |
| AI 输出注入恶意脚本（XSS） | 所有 Markdown/HTML 渲染经 DOMPurify 消毒 |
| 上传文件路径穿越 | 文件名做 `Path(...).name` 清洗；类型限制 PDF/DOCX/PPTX + 200MB 上限；ZIP 解压上限与签名校验 |
| 历史脏路径越界读写 | 所有按 `books.file_path` 访问磁盘的入口经 `resolve_under` 限制在 uploads 内：越界读取返回 404，删除记录时不删除数据目录外文件 |
| AI 输出注入 SVG/HTML | Markdown/HTML 经 DOMPurify；draw.io 生成的 SVG 经 `sanitizeSvg`（禁用 script/foreignObject/内联样式） |
| 安全响应头缺失 | `X-Content-Type-Options: nosniff`、`X-Frame-Options: SAMEORIGIN`、`Referrer-Policy: same-origin` |
| 自定义模型地址的 SSRF | base URL 仅允许 HTTPS 或本机 HTTP，拒绝 userinfo；自定义公网 HTTPS 地址属于用户主动行为 |
| 后台并发写库 | 后台任务 FIFO 串行 + 每任务独立数据库 Session |
| API Key 泄露 | `.env` 与 `backend/data/` 已 gitignore；设置页仅显示脱敏值；备份默认排除 `.env` |

## 已知边界（不承诺防护）

- **本机其他进程**：本地程序拥有与用户同等的文件读取权限，可直接读取 SQLite 数据，应用无法也不试图阻止。请勿在不受信任的多用户主机上运行并存放敏感资料。
- **未做登录鉴权**：这是单用户本地工具，没有账号体系。请勿改绑 `0.0.0.0` 暴露到公网。

## 报告漏洞

请通过 GitHub Issue 反馈（勿公开漏洞细节），或邮件联系维护者。我们会尽快确认并修复。
