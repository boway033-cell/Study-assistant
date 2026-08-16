# 变更日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增
- 知识图谱 / 双向链接：自动抽概念（关键词 + 定理名，过滤停用词）→ 全局力导向图谱 → 点概念反查所有出处（章节/页码）
- 批注自动回流知识树：创建批注时自动创建知识树节点并关联
- 学习计划与目标：设定考试日期 → 按掌握度（弱→强）倒推每日任务 → 打卡日历

## [1.0.0] - 2026-08-16

### 新增
- 资料导入（PDF/DOCX/PPTX）、章节树、中文全文搜索
- PDF 原生阅读器（pdf.js）：批注、高亮、位置记忆、视觉解读
- AI 问答（DeepSeek RAG，flash/pro 切换，出处定位）
- 知识树（大纲 + 思维导图双视图、跨树引用、掌握度、AI 生成/展开）
- 深度分析（三级目录 → AI 补全 → 逐章精读 → Markdown）
- AI 研读（综合阅读报告 + 思维训练：出题训练 / 自由陪练）
- 刷题自测、错题本、学习统计
- 文献自动分类、Word/PPT 阅读器

### 安全
- Markdown/HTML 输出统一 DOMPurify 消毒（XSS 防护）
- CORS 白名单 + Origin 校验（防恶意网页跨源调用）
- 后台任务 FIFO 串行 + 每任务独立 Session
- `stop.bat` 改用 PID 文件安全停止

### 工程
- 依赖锁定精确版本；测试依赖拆分 `requirements-dev.txt`
- 新增 MIT 许可证、PRIVACY.md、SECURITY.md、CI
