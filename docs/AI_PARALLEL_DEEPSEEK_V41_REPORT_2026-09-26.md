# AI 调用并行化与 DeepSeek V4.1 Flash 接入说明

## 流程结构

```text
功能请求 / 批量请求
  → 校验输入、连接 ID 和单次调用数量
  → 读取功能路由、已加密密钥和本机任务预算
  → 解析队列（1 worker）或交互 / 后台 AI 队列（各 2 worker）
  → 同一连接的全局并发门（最多 2 个请求）
  → 协议适配器（OpenAI Chat / Anthropic Messages / Gemini GenerateContent）
  → 流式正文、失败后按配置降级、中文写作规范与格式检查
  → 各任务独立保存结果；批量调用逐项返回成功或错误
```

`AI_INTERACTIVE_WORKERS` 和 `AI_BACKGROUND_WORKERS` 可在进程启动前设置为 1～4；默认各为 2。解析仍保持单 worker，避免 OCR 和渲染抢占内存。AI 任务在远程请求等待期间交错运行，不复用请求级数据库会话；进度按任务分别节流落库。供应商并发门跨本进程的不同事件循环生效，并在请求结束或取消时释放；默认上限为 2，可通过 `AI_PROVIDER_PARALLEL_LIMIT` 在进程启动前调到 1～4。它是本地保护上限，不代表供应商承诺的限流额度；多个服务进程之间没有分布式配额协调。

## 接口扩展

- `POST /api/ai/batch`：同一次请求提交多个提示词，并为每项指定一个或多个已保存连接。缺省使用相应功能的模型路由；显式指定连接时不自动使用降级链。最多 12 个模型调用，单请求并发 1～4，单项超时 180 秒。结果顺序与提交顺序一致，一项失败不覆盖其他项。所有调用继续经过本机预算、中文写作指令、格式整理与风格检查。
- `PUT /api/settings`：内置 DeepSeek 连接现在可配置 `deepseek_base_url` 与任意有效模型 ID。设置页使用与其他模型相同的编辑表单，支持模型列表发现和最小真实请求检测。已存密钥不因只修改模型或地址而清空。可额外创建多个 DeepSeek 连接，并按功能分配。
- 现有 `openai_chat`、`anthropic_messages`、`google_generate` 协议继续支持；本次没有把应用业务提示词与供应商地址耦合。题目、知识框架及写作流程不再强制覆盖用户选定的 DeepSeek 档位。知识节点、画图、文献分类、思维训练和演示文稿改为检查当前路由是否已配置，可使用无需 Key 的本机兼容接口。

请求示例：

```json
{
  "task": "research",
  "items": [
    {"id": "summary", "prompt": "概括这段材料的主要论点"},
    {"id": "critique", "prompt": "列出这段材料最需要核查的证据"}
  ],
  "provider_ids": ["deepseek", "glm-research"],
  "max_concurrency": 3,
  "reasoning_effort": "low"
}
```

## DeepSeek V4.1 Flash 兼容性

DeepSeek 官方把 V4.1 Flash 的 API 模型名定义为 `deepseek-flash`，OpenAI 格式 Base URL 为 `https://api.deepseek.com`，调用路径为 `/chat/completions`。旧应用设置中的 `flash`、`deepseek-v4-flash`、`deepseek-v4-flash-vision-exp` 均解析为新模型名，`pro` 继续解析为 `deepseek-v4-pro`；其他手动填写的模型 ID 原样使用。官方说明旧 Flash 名仅为暂时兼容映射，因此新配置不再依赖旧名。V4.1 Flash 支持图文输入；页面图像解读仍走同一 OpenAI 兼容图文消息。官方接口上可把 `reasoning_effort` 设为 `low`，或设为 `none` 并关闭思考模式以降低短任务延迟。

依据：[DeepSeek 更新日志](https://api-docs.deepseek.com/updates/)、[模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)、[对话补全参数](https://api-docs.deepseek.com/api/create-chat-completion/)、[图像输入](https://api-docs.deepseek.com/guides/vision/)。

## 验证

自动化测试覆盖：旧档位到新模型 ID 的映射、可配置地址与密钥保留、DeepSeek 请求体和无思考参数、多模型批量调用的并发与局部失败、同一连接最多两个并发请求、交互任务双 worker、各任务进度独立落库。后端 377 项通过、1 项跳过；前端 92 项通过，生产构建通过。

已在当前本地配置上实际验证：DeepSeek `/models` 返回 `deepseek-flash`；该模型的最小生成探测成功；两项任务同时调用 DeepSeek 与通义千问，4/4 项成功，整批约 5.18 秒；通过 OpenAI 兼容图文消息向 `deepseek-flash` 发送本地生成的 PNG，返回了非空颜色描述。这些结果证明当前密钥、模型名、端点及图文消息可用；它们是小输入连通测试，不代表长文任务的全程吞吐、供应商持续可用性或账单成本。
