# AI 深挖能力对标（2026-09-11 第二轮）

**范围**：只回答一个问题——对标成熟产品，我们在「AI 深度挖掘」与「功能开发」上还缺什么。
**与 `COMPETITIVE_BENCHMARK_2026-09-11.md` 的分工**：那份是**定位级**（五赛道、竞争格局、获客）；本份是**AI 能力级**（机制、架构、可落地差距），全部基于当日公开资料 + 对本仓库代码的当日实读核实。

---

## 0. 我们的事实基线（今日代码实读，防止自评失真）

| 能力 | 现状 | 证据 |
|---|---|---|
| 检索 | **混合检索已存在且形态现代**：向量（ChromaDB + bge-small-zh-v1.5，384 维 ONNX，模型已预置于 `backend/data/models`）+ FTS5 + LIKE 兜底 → **RRF 融合** → 启发式重排（关键词覆盖率/位置加权/长度归一化）→ 章节邻域上下文 → 目录兜底 → 越界防护 | `backend/app/services/rag/retriever.py:33-90`、`rag/vector.py` |
| 向量开关 | `settings.vector_search` **默认 false**，设置页可开；关闭时可 unload 释放内存 | `config.py:61`、`api/settings.py:214` |
| 重排 | 有**启发式**重排；无 cross-encoder 模型重排 | `retriever.py` `rerank()` |
| 主张审计 | claim → `[B:C:P]` 锚点 → supported/partial/needs_review/unsupported + **synthesis_relation（consensus/conflict/complementary/single_source/unresolved）+ counterpoint** | `api/study.py` |
| 研究规划 | 单轮：subquestions / analysis_axes / evidence_needs / report_outline | `api/study.py:424-435` |
| 长任务工程 | 全部后台任务 + SSE + 取消 + 研究报告草稿逐段保存 + **思考型模型心跳看门狗**（当日新增，见 §4） | `worker/tasks.py`、`api/study.py:_stream_answer` |
| 去 AI 味 | 4 硬禁令 + 13 白名单规则 + **信息守恒闸门**（数字/限定词丢失即拒绝）+ 逐条 diff 人工复核 | `services/writing_lab.py` |

---

## 1. 能力矩阵（当日公开资料）

| 维度 | Elicit | Consensus | Scite | Undermind | NotebookLM | 我们 |
|---|---|---|---|---|---|---|
| 证据级溯源 | **句级**引文 | 论文级 | 引用语句级 | 行内引文 | 段/表/图级 | **chunk+页码锚点** |
| 主张/冲突检测 | 无独立分类器 | 共识度计（问题级） | **Smart Citations**（supporting/contrasting/mentioning，1.2–1.6 亿条引用语句） | 间或 | 跨文档矛盾问答 | **claim 级 conflict/consensus 标注**（限单篇报告内） |
| 研究规划 | 协议驱动 | 无 | 无 | **迭代式**：澄清问题→递归自适应检索→引用图遍历 | agentic（Gemini 3.5 + 云代码执行） | 单轮规划 |
| 系统综述 | **PRISMA 2020**（95% 检索召回 / 97% 摘要筛选 / 99% 全文筛选 / 96% 抽取，994 篇 Cochrane 验证；80 篇/报告） | 无 | 无 | 无 | 无 | 无 |
| 规模 | 1.38 亿论文 | 2.2 亿 | 3500 万全文 | 2 亿（Semantic Scholar） | 用户上传 | **用户私有库** |
| 长任务 | 秒–分钟 | 秒 | 秒 | 3–6 分钟（有进度） | 分钟 | 分钟（有进度/取消/恢复） |
| 输出可交付 | 报告/表格/Alerts | 答案+过滤 | 引用报告 | 报告+阅读顺序+覆盖率估计 | **11 种导出**（PPTX/XLSX/音频概览…） | 报告/Word/PPTX |

---

## 2. 逐维度差距（只列有机制支撑的）

### 2.1 证据级溯源 —— 基本对齐
Elicit 句级、Ponder/NotebookLM 页级，我们锚到 **chunk + 页码 + 章节**，粒度不落后甚至更细（有页码定位）。差距不在锚点，在**呈现**：竞品的引文是可点击跳转回原文高亮的；我们的锚点多数场景下是文本标记。

### 2.2 主张与冲突 —— 单报告内有，**库级没有**
我们的 `synthesis_relation: conflict` 是每份报告内部的主张级冲突标注，机制上与 Scite 的 citation polarity 同构（我们靠模型判定，Scite 靠 1.2 亿引用语句库——后者不可复制，前者的思路我们已经有了）。**缺的是聚合**：N 份报告、M 张 EvidenceCard 之间，「哪些主张互相矛盾」「库内共识是什么」没有任何视图。NotebookLM 2026 已把「Which authors disagree on this point?」做成一等公民。

### 2.3 研究规划 —— **单轮 vs 迭代，这是与 Undermind/NotebookLM 的代差**
我们：规划 → 检索 → 成文，**一次成型**，证据不足时只会在报告里标 needs_review。
Undermind：检索 → 发现缺口 → **改写检索式再检索**，递归自适应，还给覆盖率估计（"80% converge"）。
这不是模型差距，是**控制流**差距：我们缺一个「写前缺口评估」回路。

### 2.4 系统综述自动化 —— 无，但**建议不做**
Elicit 的 PRISMA 流水线（筛选日志、纳入排除、可审计流程图）服务的是循证医学/政策场景。我们是个人研究助手，私有库通常 < 百篇；为此引入 screening pipeline 属于定位漂移。**唯一的可借鉴点**：它的「自定义抽取列」（用户定义字段→批量填充→格内引文）非常适合我们的多文献综述——把「综述」从纯散文升级为「结构化抽取表 + 散文」双输出。

### 2.5 检索架构 —— 形态对齐，两处欠账
我们已是行业主流形态（混合 + RRF + 重排 + 上下文扩展），对照 Obsidian 生态 2026 的最佳实践（Smart Connections 向量 / Neural Composer 接 LightRAG 做图检索）：
- **欠账 A：向量默认关**。竞品的语义检索是开箱默认；我们要用户手动开。bge-small-zh 只有 ~100MB 且已预置，CPU 可跑——应评估默认开启或首次导入时引导。
- **欠账 B：图检索（GraphRAG）**。Obsidian 生态已在做「向量找相近 + 图找关系（contradicts/depends_on/causes 边）」。我们的知识图谱是**展示层**，不参与检索。Elicit/Scite 的「引用极性」本质就是图上带标签的边。

### 2.6 长文生成 —— 分段成文对齐 Story Engine，缺「结构化事实记忆」
Sudowrite 的做法（当日核实）：**Story Bible**（角色/世界观/风格的持久结构化库）+ 每次生成前 **Context Scraper**（前 2000 词 + Bible + 本章摘要注入）+ **生成后自动清洗 AI 腔**（post-processing strip AIisms）+ 模型路由。
对照我们：
- Writing DNA ≈ 它的 Style 层，且更结构化（7 层蒸馏、v1–v5 版本管理）——**风格记忆我们领先**；
- 但**事实记忆**我们只有「前文尾部 3500 字」——没有「中心论点 / 已立论点 / 已用证据锚点」的结构化状态卡，段间一致性全靠模型自觉；
- **生成后清洗**：我们对报告只做 `ai_flavor_violations` 检测标注（`tone_violations`），**不跑 clean_blocks**——而去 AI 味管线明明就在旁边。

### 2.7 去 AI 味 —— **我们领先，且是可解释的领先**
当日 humanizer 市场全貌：端到端黑盒改写 + 检测器军备竞赛（Turnitin 2025-08 加了 paraphrase 检测、每季度重置通过率）；质量基准开始考核「语义保持 >90%」。
最接近我们的 UX 是 StealthWriter 的 Deep Scan（逐句高亮 + 每句多个备选），但它是**黑盒**。
我们的「禁令检测 → 白名单改写 → 信息守恒闸门 → 逐条 diff 复核」**在业界没有同构产品**：改什么、为什么改、改坏了会拒收，全程可解释可回滚。这是应当写进产品文档卖点的差异化，也侧面解释了为什么它长期缺测试网没人发现（黑盒产品自己也不知道哪条规则失效了）。

### 2.8 长任务工程 —— 当日补齐后的水位
行业：Undermind 3–6 分钟带进度；NotebookLM agentic research 带阶段展示。
我们：研究报告六级阶段 + 逐段草稿 + 取消 + 断线恢复 + （当日新增）**思考型模型心跳**——模型先推理 2–4 分钟不出正文时不再误杀，且进度文案显示「模型正在推理（已思考 N 字）」。
**剩余欠账**：成本/耗时透明（竞品大多显示预计时间或用量）。

---

## 3. 真实差异化（当日复核后的修正版）

1. **可核验证据链贯穿全流程**（锚点→审计→复核→沉淀→再取材）——NotebookLM 克制但无沉淀回库，Scite 有引用语境但不管写作。维持既有结论。
2. **可解释去 AI 味管线**——上节所述，业界无同构。
3. **本地优先 + 私有混合检索栈**——竞品的 1–2 亿论文规模我们永远没有，但「你的库 + 你的机器 + 页码级锚点」是它们都没有的组合。

---

## 4. 当日工程事实：思考型模型的心跳（方法论价值大于单点修复）

对活动模型 `qwen3.8-max-0902` 抓原始 SSE 实测：**首帧 `reasoning_content` 2.2s 到达 → 流式思考 133s → 首帧正文**。provider 只读 `delta.content`，于是「正文首字」与「端点存活」被混为一谈——旧看门狗在思考阶段必然误杀，无论阈值调到 120 还是 240。
修复：`last_delta_at` 心跳（任何一帧含思考帧都算存活）+ 看门狗改判「端点静默」+ 进度文案显示思考字数。**方法论教训**：这类 bug 的根因在「存活信号定义错误」，靠调参数永远修不好，必须抓原始协议帧定案。

---

## 5. 高杠杆改进清单（按性价比排序）

| # | 改进 | 对标 | 成本 | 为什么可行 |
|---|---|---|---|---|
| 1 | **生成后接去 AI 味**：报告成文后对正文跑 `clean_blocks`，改写候选进复核侧栏 | Sudowrite post-processing | 低——管线现成，纯接线 | `ai_flavor_violations` 已在测，`clean_blocks` 就在同文件 |
| 2 | **库级矛盾地图**：聚合所有 StudyReport.claims 的 status/synthesis_relation 成「共识/分歧」矩阵视图 | Scite / NotebookLM | 中——数据已齐（claims_json），纯聚合 + 一个前端视图 | 不需要新模型，只是把已有结构化数据换个方式看 |
| 3 | **写前缺口评估回路**：检索完成后、成文前，让模型对照 evidence_needs 评估缺口，缺口大→补一轮检索或明示「材料不足」 | Undermind 迭代式 | 中——一次小 LLM 调用 + 控制流分支 | evidence_needs 的 schema 已存在，只差消费它 |
| 4 | **向量检索默认开启评估** | 全行业标配 | 低——改默认值 + 引导文案；需先测内存 | 模型已预置，`ensure_model_ready()` 已有 |
| 5 | **论证状态卡**：分段成文时注入「中心论点/已立论点/已用锚点」结构化清单，替代裸的前文尾部 | Sudowrite Story Bible | 中 | 报告大纲已有，扩展为状态对象即可 |
| 6 | （不推荐）PRISMA 筛选流水线 | Elicit | 高 | 定位漂移，见 §2.4 |

---

## 6. 来源（当日检索）

- Elicit 官网及评测页（PRISMA 2020、95/97/99/96% 指标、80-paper Reports、Research Agents）
- Scite 评测（Smart Citations 12 亿+ 引用语句、Reference Check、70–75% 分类准确率的第三方实测）
- Undermind 官网与第三方评测（递归检索、3–6 分钟、阅读顺序、覆盖率估计）
- NotebookLM 2026 更新汇总（Gemini 3.5、2M 上下文、跨文档矛盾、Antigravity 代码执行、11 种导出）
- Ponder / Jenova / ToolsRadar 的 2026 横评（工具阶段分工：发现/筛选/提取/阅读/综合）
- Sudowrite 2026 评测多篇（Story Bible / Context Scraper / post-processing / Muse）
- AI Humanizer 市场 2026 评测多篇（StealthWriter Deep Scan、语义保持基准、检测器军备）
- Obsidian 本地 RAG 2026 实践（Smart Connections、Neural Composer + LightRAG 图检索、sqlite-vec/ChromaDB 栈）
- 本仓库代码：`rag/retriever.py`、`rag/vector.py`、`api/study.py`、`services/writing_lab.py`、`services/llm/__init__.py`
