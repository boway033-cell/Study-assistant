# 秒悟持久化迁移方案

## 当前结论

秒悟镜像容器的本地文件系统是临时的。当前应用把 SQLite、原始上传文件、OCR/版面缓存、PPTX 和 Word 输出都写入 `DATA_DIR`，因此首次镜像部署只能作为空数据实例的运行验证，不能承载需要保留的用户资料。

部署包必须排除本机 `.env`、`backend/data/` 和所有用户文件。容器暂时使用 `/tmp/study-assistant-data`，实例重建后数据丢失是预期行为。

## 目标架构

| 数据类别 | 当前位置 | 持久化目标 | 迁移原则 |
|---|---|---|---|
| 书目、章节、批注、笔记、报告、设置、任务 | SQLite | 秒悟 Supabase PostgreSQL | 保留现有 SQLAlchemy 领域模型，新增 PostgreSQL 配置和正式迁移；移除 SQLite PRAGMA/FTS5 专用路径 |
| PDF、DOCX、PPTX 原文件 | `data/uploads/` | Supabase Storage 私有桶 `library-files` | 对象键使用 `user_id/book_id/version`，数据库只保存对象键、哈希、大小和 MIME |
| Writing DNA 语料及写作输出 | `data/writing/` | 私有桶 `writing-files` | 数据库保存版本、所有权、来源关系和对象键 |
| PPTX/渲染结果 | `data/presentations/` | 私有桶 `generated-artifacts` | 生成完成后上传；下载使用短时签名 URL |
| OCR、版面、全文索引等可再生数据 | 本地目录/SQLite FTS5 | 临时磁盘或派生表 | 缓存允许丢失；必要时从 Storage 原文件重建 |
| API Key | 本地加密设置或 `.env` | 秒悟 Secrets / 容器环境变量 | 禁止进入前端、Git、镜像源码包和日志 |

## 用户与权限

1. 启用 Supabase Auth，为业务表增加 `user_id uuid NOT NULL`。
2. 所有用户数据表启用 RLS；读取、写入、删除策略均限制为 `auth.uid() = user_id`。
3. Storage 使用私有桶，策略限制对象路径首段为当前用户 ID。
4. FastAPI 只在服务端使用 `SUPABASE_SERVICE_ROLE_KEY`；浏览器只使用 anon key/用户令牌。
5. 现有无登录的本地模式继续保留，但云端模式必须先完成认证，不能公开共享一个 SQLite 数据库。

## 分阶段实施

### 阶段 0：首次镜像验证

- 使用空的 `/tmp/study-assistant-data` 启动。
- 不上传本机资料和 API Key。
- 只验证构建、首页、`/api/health` 和基础无数据流程。
- 页面中产生的数据不保证保留，不用于正式使用。

### 阶段 1：数据库迁移

- 执行 `meoo cloud enable`，随后执行 `meoo cloud pull-env`。
- 为当前 SQLAlchemy 模型设计 PostgreSQL DDL 和版本迁移。
- 把 `_migrate()` 中的 SQLite `PRAGMA` 和手写 `ALTER TABLE` 迁到正式迁移脚本。
- 用 PostgreSQL 全文检索替代 SQLite FTS5，或将其明确降级为可再生索引。
- 编写 SQLite 到 PostgreSQL 的一次性导入器，并用行数、外键和抽样哈希校验。

### 阶段 2：文件迁移

- 建立三个私有 Storage 桶并配置 RLS。
- 上传文件时直接或分片写入 Storage；后台任务按需下载到临时目录。
- 生成物完成后上传 Storage，再清理临时文件。
- 编写本地目录批量迁移工具，以 SHA-256 去重并支持断点续传。

### 阶段 3：认证和切换

- 接入 Supabase Auth，并给所有业务查询补充用户边界。
- 完成备份、演练迁移和回滚方案后，冻结本地写入并执行最终增量迁移。
- 通过数据完整性、权限隔离、容器重启和冷启动测试后，才把秒悟实例作为正式环境。

## 验收门槛

- 容器重建后数据库记录与原文件仍存在。
- 两个测试用户无法读取或修改对方的数据和文件。
- 本机 `.env`、SQLite、上传文件不在部署归档中。
- 迁移前后核心表行数、文件数量和 SHA-256 校验一致。
- 所有后台任务可在临时文件被清理后从持久化源恢复或重试。
