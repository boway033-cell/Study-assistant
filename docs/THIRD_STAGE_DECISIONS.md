# 第三阶段：识别引擎与产品入口决策

日期：2026-08-24

## 已落地

- 图谱、知识树、AI 绘图均以用户明确选择的 `book_ids` 为来源范围。
- 多书知识树按书生成独立根树，不把目录和概念压入同一棵无来源树。
- 中文教材目录支持“章 → 节 → 一、 →（一）”四级结构、中文编号连续性、跨行标题和异常空格。
- 检索切片改为页面唯一归属，消除父章/子节重叠复制。
- `nature-reader` 的来源锚点、来源不足明示、图文/段落可复核原则已进入深度阅读输出契约；`nature-polishing` 的“先重建逻辑、再重排句子”用于 Markdown 结构整理，不允许 AI 改写原文事实。
- 双页阅读和本地一键启动入口已修复。

## GitHub 中英文调研结论

| 方案 | 适用点 | 资源与风险 | 接入建议 |
|---|---|---|---|
| [MinerU](https://github.com/opendatalab/MinerU) | 中文 PDF、扫描件、复杂版面、Markdown/JSON、阅读顺序 | 官方当前完整本地部署建议内存较高；模型和依赖体积大 | 作为“高精度解析”可选外部进程，不随主应用常驻 |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | 中文 OCR、版面结构、表格 | 仍需模型，但可按 OCR 能力拆分 | 作为扫描页按需 OCR provider；逐页/小批次运行 |
| [Docling](https://github.com/docling-project/docling) | 文档结构、阅读顺序、统一文档模型 | 中文教材效果仍需用真实样本基准测试 | 保留 provider 接口，先基准后决定是否内置 |
| [nature-reader](https://github.com/Yuan1z0825/nature-skills/tree/main/skills/nature-reader) | source map、段落/图表锚点、可复核 Markdown 阅读材料 | 是工作流契约，不是 OCR 引擎 | 用于规范产物与审计层 |
| [nature-polishing](https://github.com/Yuan1z0825/nature-skills/tree/main/skills/nature-polishing) | 逻辑重构、术语一致、句段可读性 | 不应改写来源原文 | 只作用于派生阅读材料，原文层保持不可变 |

## 推荐的低内存解析架构

```text
导入任务
  ├─ 默认：PyMuPDF + 本地四级目录规则（零模型常驻）
  ├─ 扫描件：按需启动 PaddleOCR provider，逐页处理后退出
  └─ 用户选择“高精度”：启动 MinerU CLI/API 子进程，产出 source map 后退出
          ↓
统一结构审计：编号连续性 + 跨行合并 + 来源定位 + 低置信度标记
          ↓
原文层（不可改写） / 阅读版 Markdown（可重排） / AI 派生内容（明确标识）
```

主应用不加载 OCR/VLM 权重；高精度解析任务进入全局任务中心，同一时刻只运行一个重型任务。模型退出后释放内存，缓存只保存结构化 JSON、页面锚点和必要图片。

## 需要产品负责人确认

1. **旧书重解析**：代码修复不会静默覆盖现有数据库。需确认是否立即对《行政管理学夏书章》执行“重新解析”；该操作会重建章节、切片和 FTS，并使旧深度分析结果失效后重新生成，但保留原 PDF。
2. **网页唤醒形态**：普通 `http://127.0.0.1:8000` 链接无法在服务停止时启动本机程序。推荐注册当前用户级 `study-assistant://open` 协议，点击后先启动服务再打开浏览器；备选是登录后后台常驻。前者不常驻、省内存，但需要用户主动安装一次协议入口。
3. **高精度引擎**：建议先用 10 本代表性中文教材做 A/B 基准（目录 F1、段落顺序、峰值内存、处理时间），再决定默认安装 PaddleOCR 还是仅提供 MinerU 外部接入。
