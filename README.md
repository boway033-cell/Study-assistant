<div align="center">

# Study Assistant

### 把自己的资料，建成能追问、能核查的研究助手

本地优先的个人知识库：管理书籍与论文，沿原文研读，在书架或项目范围内提问，再把判断写成可回溯的研究成果。

[![Version](https://img.shields.io/badge/version-v2.5-8B5A2B)](CHANGELOG.md)
[![CI](https://github.com/boway033-cell/Study-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/boway033-cell/Study-assistant/actions/workflows/ci.yml)
[![Platform](https://img.shields.io/badge/platform-Windows-304747)](docs/DOWNLOAD_AND_INSTALL.md)

[下载 v2.5](https://github.com/boway033-cell/Study-assistant/releases/tag/v2.5) · [开始使用](#开始使用) · [资料与隐私](#资料与隐私) · [开发与文档](#开发与文档)

</div>

## 为什么做这个项目

读过的材料会越积越多，研究问题却会不断变化。Study Assistant 让论文、专著、教材和工作资料留在同一个知识库里：你可以按书架或项目确定范围，回到原页核对 AI 的解释，保存自己的判断，并在写作时继续使用这些证据。

它适合已有一批资料、需要长期阅读、比较和写作的学生、教师、研究者及知识工作者。当前产品是 **Windows 本地运行的 Web 应用**，界面在浏览器中打开，资料默认保存在本机。

~~~mermaid
flowchart LR
    A["导入资料"] --> B["书架或项目"]
    B --> C["阅读、标注与全文研读"]
    C --> D["限定范围的助手问答"]
    D --> E["核对原页并保存判断"]
    E --> F["研究报告、写作与汇报"]
    E -->|新问题与修订| C
~~~

## 一次研究如何进行

1. **建立资料范围。** 导入 PDF、DOCX 或 PPTX，把相关文献放进书架；研究项目可以引用书架和单本资料。解析后的正文、目录和检索索引留在本机。
2. **读懂并标记原文。** 阅读器支持目录跳转、高亮、批注和来源定位。长书可结合 PDF 书签、印刷目录及正文标题识别章节；识别有误时可以人工修订。
3. **让助手使用所选资料。** 在“我的助手”选择书架或项目，查看逐本状态和全文研读预算。问答限定在当前范围；已有有效研读结果时，助手还可参考论证节点。支持连续追问、会话总结和对象澄清，历史记录可继续提问；它会区分原文、用户保存的记录和 AI 推断。引用编号与词面筛查分别显示，结论仍需核对原文。
4. **把问题变成可修订的判断。** 研究档案可保存问题、竞争解释、阅读任务和复核结果。带原页的线索可以回跳；资料变化后，相关结果会提示重新核对。
5. **形成作品。** 研究报告比较所选材料中的共识、分歧和反例，作品库承接人工编辑、表达审阅和 Word 输出；汇报工作区从已选材料制作可编辑的 PPTX。

例如，研究一个跨书目问题时，可以先把相关书籍放进项目，逐本查看论证，再询问不同作者如何定义同一概念。打开回答中的原页，保存仍有争议的判断，最后据此组织报告和阅读计划。

## 核心能力

| 工作 | 当前可用的能力 | 需要留意 |
| --- | --- | --- |
| 管理资料 | 本地导入、书架、项目、排序、OCR、目录修订和知识库健康检查 | 扫描质量差或版式复杂的页面需要核对解析结果 |
| 阅读与检索 | 阅读位置记忆、批注、全文检索、来源回跳和可选向量检索 | 引用位置可检查，原文是否支持结论仍需判断 |
| 个人助手 | 按书架或项目限定问答范围，复用有效的全文研读结果与用户明确保存的记忆 | “研读完成”表示已处理可提取文本，不代表模型或用户已经理解 |
| 研究与发现 | 论证地图、跨文献概念比较、研究档案、竞争解释和阅读任务 | 发现只针对已选材料；外部线索不能自动升级为全文证据 |
| 写作与汇报 | 研究报告、作品库、Writing DNA、表达审阅、Word 与 PPTX | AI 草稿、数字、引文、图表和最终版式需要人工审阅 |

这里的“个人助手”通过**限定资料范围、检索原文、复用研读结果和用户确认的记录**工作。添加书籍不会训练或微调底层模型，也不会把 AI 的猜测自动写成你的观点。

## 开始使用

1. 从 [v2.5 Release](https://github.com/boway033-cell/Study-assistant/releases/tag/v2.5) 下载 <code>study-assistant-v2.5.zip</code>，完整解压到固定目录。需要 Windows 10/11 和 64 位 Python 3.12+。
2. 双击 <code>install.bat</code>。安装向导会建立虚拟环境、安装依赖并启动应用；中断后可以继续。需要排查环境时运行 <code>diagnose.bat</code>。
3. 在“资料库”导入自己的文件。没有模型密钥也能使用阅读、标注和本地搜索，并可加载虚构演示资料。
4. 使用 AI 功能前，到“设置 → 模型连接”配置自己的供应商连接，检查任务路由、发送范围和预算。

源码安装需要 Node.js 22+；Release 压缩包已包含构建后的前端：

~~~powershell
git clone https://github.com/boway033-cell/Study-assistant.git
cd Study-assistant
git checkout v2.5
.\install.bat
~~~

升级前请停止旧服务并备份整个 <code>backend/data/</code>。完整步骤见[下载与安装指南](docs/DOWNLOAD_AND_INSTALL.md)。

## 资料与隐私

| 操作 | 数据去向 |
| --- | --- |
| 导入、解析、OCR、阅读、批注和本地检索 | 默认保存在本机 <code>backend/data/</code> |
| 主动运行 AI 问答、全文研读、研究、写作或汇报 | 所选任务需要的文字发送给配置的模型供应商 |
| 主动解读页面图像 | 页面图像发送给所选的多模态模型供应商 |
| 主动使用 Crossref 检索 | 检索词和配置的联系邮箱发送给 Crossref |

全文研读可能分批发送所选资料的全部**已提取文字**。提交前应核对书目范围、模型与估算用量；实际费用以供应商账单为准。API Key 和原文件默认留在本机。细节见[隐私说明](PRIVACY.md)。

## 使用边界

目录、OCR、引用定位、论证关系与 AI 解释都可能出错。重要判断请打开原页，检查引文、图表、公式和被遗漏的页面。应用的跨文献比较只覆盖本次选定的资料，不能代替系统综述所需的全面检索、纳排与质量评价。

长书目录的可复验样本目前覆盖 4 本大卫·哈维著作和 4 本其他书籍；结果及尚未覆盖的扫描书场景见[目录样本库报告](docs/reports/TOC_CORPUS_REVALIDATION_2026-09-27.md)。

研究报告会先保存正文，再独立核查原文与论证。核查失败时可单独重试；界面显示引文匹配、推理跳步及覆盖范围，保留人工复核结果。具体设计、公开方案对比和验证边界见[AI 通路改进报告](docs/reports/AI_PATH_RESEARCH_AND_IMPLEMENTATION_2026-10-07.md)。

## 开发与文档

后端使用 FastAPI、SQLite 与本地任务队列；前端使用 Vue。模型连接按任务路由，支持多供应商和自定义接口。代码仓库不包含用户的数据库、原始文献或 API Key。

如通过反向代理部署在自有域名，须在 `CORS_ALLOWED_ORIGINS` 中填写完整站点源（例如 `https://study.example`），并让代理保留原始 `Host`。本机默认地址已内置，无须设置此项。

~~~powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
cd frontend
npm ci
npm run test:unit
npm run build
~~~

[变更日志](CHANGELOG.md) · [架构说明](docs/01-architecture.md) · [API 文档](docs/03-api.md) · [贡献指南](CONTRIBUTING.md) · [安全策略](SECURITY.md)

主项目采用 [MIT 许可证](LICENSE)。随附的第三方材料保留各自许可，其中公文写作部分含 PolyForm Noncommercial 1.0.0 材料；分发或商业使用前请核对[第三方许可说明](THIRD_PARTY_NOTICES.md)。
