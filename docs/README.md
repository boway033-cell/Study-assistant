# Study Assistant 文档索引

本目录只保留当前仍具维护价值的产品、工程和专项设计文档。若文档与实现不一致，
以源码、数据库迁移和自动化测试为准。

## 当前权威文档

| 文档 | 用途 |
|---|---|
| [产品文档.md](产品文档.md) | 产品定位、功能范围与视觉交互规范 |
| [PROJECT_HANDOVER.md](PROJECT_HANDOVER.md) | 当前开发状态、运行方式、已知边界和交接记录 |
| [LIGHTWEIGHT_DOCUMENT_PIPELINE.md](LIGHTWEIGHT_DOCUMENT_PIPELINE.md) | 当前文档解析、OCR、目录证据与内存约束 |
| [nature-literature-workflow.md](nature-literature-workflow.md) | 文献归档、证据卡片、PPTX 与合法全文工作流 |
| [RESEARCH_REPORT_METHOD.md](RESEARCH_REPORT_METHOD.md) | AI 自主研读、精确来源审计与资源边界 |

## 技术参考

| 文档 | 用途 |
|---|---|
| [01-architecture.md](01-architecture.md) | 系统分层与运行架构参考 |
| [02-database.md](02-database.md) | SQLite 数据模型参考 |
| [03-api.md](03-api.md) | API 端点参考；最终以 FastAPI OpenAPI 为准 |

## 维护规则

- 阶段性决策、一次性审计和已经完成的旧路线图不再长期保留；结论应合并进产品文档或交接文档。
- 新增专项文档前，先确认无法合理归入上述权威文档。
- 删除的历史文档仍可通过 Git 提交记录恢复，不在仓库当前版本重复保存。
