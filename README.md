# 保研复习助手（Study Assistant）

一个本地部署的**个人学习辅助软件**，帮助保研学生把大量专业课 PDF/Word 资料转化为
「AI 问答 + 自测刷题 + 知识树 + 掌握度统计」的完整学习闭环。

> 核心思路：把「读资料」变成「主动回忆 + 自测 + 查漏补缺」。
>
> **AI 策略**：文本解析/切块/检索全部在本地完成（不上传资料）；仅将「提问 + 检索片段」发送到 **DeepSeek 云端**生成回答。

## 🚀 快速启动

```bat
:: 双击 start.bat（首次运行自动装依赖）
```

然后浏览器访问 `http://127.0.0.1:8000`。

### 手动启动

```bash
# 后端
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# 前端（开发模式，可选）
cd frontend
npm install
npm run dev        # http://localhost:5173
npm run build      # 构建产物由后端自动托管
```

### 首次使用

1. 到 [DeepSeek 开放平台](https://platform.deepseek.com) 注册并创建 **API Key**
2. 打开应用，按引导弹窗前往 **设置** 页粘贴 Key 并保存
3. 设置页点击「重新检测」确认连接成功，即可开始 AI 问答
4. 模型档位：**flash**（deepseek-v4-flash，快速）/ **pro**（deepseek-v4-pro，深度推理），设置页或问答页随时切换

> 开发环境也可在项目根 `.env` 中配置 `DEEPSEEK_API_KEY`（不纳入版本控制）。
>
> **PDF 原文阅读**：内置 pdf.js 阅读器（Mozilla 开源，Firefox 同内核），直接在页面内渲染教材 PDF，无需下载。

## ✅ 当前状态

| 模块 | 状态 | 说明 |
|---|---|---|
| 资料管理 | ✅ 完成 | PDF/DOCX/PPTX 上传、章节树、版面分析、智能分析、中文全文搜索（全部本地） |
| AI 问答 | ✅ 完成 | RAG 检索 + DeepSeek 云端流式回答，答案带出处页码；右侧原文面板（pdf.js 阅读器）；flash/pro 切换 |
| 知识树 | ✅ 完成 | 大纲/导图双视图、手动+章节导入+AI 生成框架、关联教材章节右侧看原文、节点笔记 |
| 刷题自测 | ✅ 完成 | **AI 分析教材自动生成题目**（选书/章即可），自动判分 + 自评，错题本 |
| 学习统计 | ✅ 完成 | 总览、章节掌握度（基于作答）、作答趋势、薄弱章节排行 |
| 设置 | ✅ 完成 | DeepSeek API Key、flash/pro 模型切换、连接探测 |

**测试情况**：单元测试 34/34 通过 · Playwright 浏览器 UI 测试 18/18 通过（资料库/搜索/知识树双视图/刷题/统计/设置/问答/pdf.js 阅读器）。

## 📚 文档

| 文档 | 内容 |
|---|---|
| [docs/00-PRD.md](docs/00-PRD.md) | 产品需求 |
| [docs/01-architecture.md](docs/01-architecture.md) | 技术架构 |
| [docs/02-database.md](docs/02-database.md) | 数据库设计 |
| [docs/03-api.md](docs/03-api.md) | API 接口 |
| [docs/04-roadmap.md](docs/04-roadmap.md) | 开发路线图 |
| [docs/PROJECT_HANDOVER.md](docs/PROJECT_HANDOVER.md) | 项目交接（含踩坑记录） |
| [docs/experience-pdf-analysis.md](docs/experience-pdf-analysis.md) | 教材 PDF 分析方法论 |

## 🔒 隐私与数据

- **文本分析 100% 本地**：上传的 PDF/Word 只在本地解析、切块、建索引，从不发送到云端
- **云端仅发送**：AI 问答时仅把「你的提问 + 检索到的教材片段」发给 DeepSeek；资料全文不会上传
- 所有数据在 `backend/data/`（SQLite + 上传文件 + 可选向量库），备份 = 复制该目录
- API Key 保存在本地数据库设置中，设置页只显示脱敏值
- 专业术语词典：`backend/data/userdict.txt`（每行一个词，重启后生效）

## 🧪 测试

```bash
# 单元测试
.venv/Scripts/python -m pytest backend/tests/ -q

# 浏览器 UI 测试（需先启动后端）
.venv/Scripts/python -m playwright install chromium
.venv/Scripts/python backend/tests/ui_test.py
```

---
*版本 v0.4（DeepSeek 云端化 + 知识树，卡片学习已移除）*