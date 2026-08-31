# 研究报告工作区：方法与实现边界

## 产品立场

综合研读由用户在研究路由中选择的模型根据研究问题和文章内容作出分析判断，不把 Nature 风格、固定论文结构或任一外部 skill 当作答案模板。外部方法只提供可审计的过程约束：研究规划、范围内检索、来源回链、证据强度判断与反例检查。

当前方法职责分为四层：`scientific-critical-thinking` 检查证据质量、偏倚与因果外推；`peer-review` 检查研究设计、统计、报告完整性和复现边界；`hypothesis-generation` 只生成带竞争解释与可证伪条件的候选假设；`scientific-writing` 将输出主张绑定到用户已选知识对象。它们是过程护栏，不替代人工学术判断。

结构化主张使用 `consensus / complementary / conflict / single_source / unresolved` 描述跨文献关系，并单独记录证据质量、偏倚风险和替代解释。报告可一次保存为批判性审查笔记及逐条证据卡，重复保存不会产生副本。后续写作和 PPTX 必须显式选择这些知识对象，不能直接从风格语料或未选择的原文扩写事实。

界面展示的是可供用户检查的研究计划，包括材料类型、子问题、分析维度和证据需求；不请求、不展示、也不持久化模型隐性思维链。

## 借鉴的方法

- [PaperQA2](https://github.com/Future-House/paper-qa)：采用“问题驱动的迭代检索—收集证据—形成带引用回答”，并要求引用定位到真实文本块。
- [GPT Researcher](https://github.com/assafelovic/gpt-researcher)：采用规划、执行、来源汇总和报告发布的阶段分离；本产品保持单模型、串行后台任务，不引入多代理常驻服务。
- [Microsoft RD-Agent](https://github.com/microsoft/RD-Agent)：吸收“提出路径—用材料验证—根据反馈修正”的循环思想；其完整框架仅支持 Linux 且依赖额外运行环境，因此不直接安装。
- [Scientific Agent Skills](https://github.com/K-Dense-AI/scientific-agent-skills)：吸收批判性思维、文献综述、科学写作与完整性审查的职责分离。
- [Claude Scientific Writer 的 literature-review skill](https://github.com/K-Dense-AI/claude-scientific-writer/blob/main/.claude/skills/literature-review/SKILL.md)：采用“主题综合优先于逐篇摘要”的综述组织原则。
- [Evidence Synthesis skill](https://github.com/cdeust/zetetic-team-subagents/blob/main/skills/evidence-synthesis/SKILL.md)：采用 claim–evidence–warrant、最强反证、质性编码和解释学审查维度。
- [Humanities Superpowers](https://github.com/icerain-cmd/humanities-superpowers) 与 [Academic Narratologist skill](https://github.com/bestagentkits/agency-skills/blob/main/skills/academic/academic-narratologist/SKILL.md)：补充人文研究的文本细读、叙事结构、语境和竞争性阐释。
- [Scientific Literature Review skill](https://github.com/yigityildiz0/scientific-agent-skills/blob/main/skills/common/scientific-literature-review/SKILL.md)：补充自然科学/生物医学中的研究设计、证据等级、相关—因果边界和来源核验。
- 本地 `scientific-critical-thinking` 与 `literature-review` skills：用于明确事实与解释边界、证据相称性、替代解释、主题综合和研究缺口，不复制其报告格式。
- [Anti-Defensive Writing](https://github.com/Kiterlin/anti-defensive-writing)：采用“主张前置、必要限制只写一次、删除假想反对者导向的免责声明、用具体范围替代修饰词堆叠”。不采用隐藏真实局限、回避反证或选择性删除不利证据的做法。

以上仓库不作为产品依赖，未复制其代码，也不会改变现有 DeepSeek 接入。

## 运行路径

1. 用户明确选择书目，可进一步限定章节和已有笔记。
2. 深度模式调用当前研究模型生成小型研究计划；快速模式跳过独立规划调用。
3. 未限定章节/笔记时，以研究问题和最多三个子问题逐本公平检索，避免单本文献垄断上下文；有限定时严格只使用所选材料。
4. 当前研究模型围绕中心论题自主组织连贯中文文章；允许标识为“本文推断”的概念联系、机制解释和研究延伸，同时另行输出结构化主张审计与待核查问题。
5. 后端验证每个来源锚点是否真实存在于本次上下文；无效来源不得保持“支持”状态。
6. 成文时把机器锚点转换为上标编号，正文与 Word 不显示 `B/CH/C/P` 内部标识；完整锚点只保存在主张审计，来源索引显示文献名、章节和页码。

## 研读方式

| 方式 | 适用情境 | AI 重点 |
|---|---|---|
| AI 自主研读 | 问题开放或材料类型混合 | 自主识别问题结构和最有效的分析维度 |
| 观点与理论比较 | 多文献概念或理论比较 | 定义、机制、证据、边界、一致与冲突 |
| 证据与方法审查 | 论文、案例、因果或政策主张 | 数据与解释、证据强度、偏差、替代解释 |
| 研究缺口探索 | 选题、综述和后续研究设计 | 共识、争议、未知、可验证的下一步问题 |

## 多文献综述的跨学科组合

多文献综述复用“规划—证据—综合—审计”的共同骨架，但不同学科不能套用同一套质量清单：

| 学科镜头 | 重点证据 | 必须避免 |
|---|---|---|
| 社会科学 | 理论概念与操作化、样本/情境、资料生产、因果识别、替代机制、定量不确定性或质性编码与反身性 | 把相关写成因果；忽略情境和外部效度；把不同概念直接计票 |
| 人文与文学 | 原文措辞、叙事结构、修辞/意象、历史语境、解释框架、竞争性阐释与反向阅读 | 用论文数量多数表决阐释；把解释框架写成文本事实；用摘要替代细读 |
| 自然科学与生物医学 | 研究设计、样本与对照、实验条件、效应方向/大小、不确定性、混杂/偏倚、重复性 | 把观察性关联写成因果；只看显著性；忽略阴性结果和复现边界 |

共同输出必须按主题综合，区分共识、冲突、互补证据和最强反证，并给出真实 `[B:C:P]` 锚点。`narrative / scoping / evidence_map` 描述的是所选个人知识库内的组织方式；没有独立的系统检索、去重、纳排和质量评价记录时，不使用“系统综述”“元分析”或“PRISMA 合规”表述。

## 来源与资源边界

- 来源锚点格式为 `B{书目}:CH{章节}:P{页码}:C{文本块}` 或 `B{书目}:NOTE{笔记}`。
- 主张分为事实描述、关联判断、因果判断和解释判断，另有支持状态、置信度、理由和反例/限制。
- 规划预览最多 18K 字符，综合证据最多 48K 字符；最多 30 条主张和 10 个待核查问题。
- 深度模式两次模型调用，快速模式一次；任务仍由全局 FIFO 串行执行，不新增常驻进程、索引副本或模型内存。
