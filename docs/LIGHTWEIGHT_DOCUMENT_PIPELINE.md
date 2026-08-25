# 轻量文档解析管线

## 产品决策

MinerU 不再作为标准安装或默认导入依赖。默认链路为：

1. `PDFText/pypdfium2` 提取文本块、行、坐标和字体；PyMuPDF 补充图片引用与坐标，文本后端失败时整体回退 PyMuPDF。
2. `RapidOCR + ONNX Runtime` 仅处理文本层不足的页，逐页渲染、逐页释放。
3. 内置字号/坐标版面分析处理普通文档；PP-DocLayout-M ONNX 单模块是显式可选增强。
4. 目录由书签、编号语义、版面证据和负面句式共同评分，再以全书编号链校验缺号/重号/倒序/父级/同页顺序，不凭空补标题。
5. 底层保存结构化 JSON，Markdown 是可重复生成的末端视图。

## 资源边界

- PDFText 固定单 worker；PDFium 不跨线程共享。
- OCR、可选版面增强、深度分析和 PPTX 共用重型任务锁，并发固定为 1。
- OCR 默认 180 DPI，每次内存只保留一页位图。
- 页面结果立即写入页级缓存；中断后从缓存继续。
- 默认不加载 PaddlePaddle、PyTorch、MinerU 或本地 VLM。

## 结构化 JSON

导入后的证据文件保存到 `backend/data/structured/<file_hash>.json`，包含：

- 页码、页面尺寸；
- 文本块与块坐标；
- 块内原始行；
- 字号、字重、来源和置信度；
- OCR 页的来源标记。
- 图像资源 ID、图像坐标及图注关联；
- 页内 `reading_order`，双栏页面按全宽块分区后先左栏、再右栏。

目录、Markdown、切块和来源定位从该结构派生，不能用生成后的 Markdown 覆盖它。

## Word / PowerPoint 原版与结构双轨

- 原版页面：Windows 上按需启动 Microsoft Word/PowerPoint，把 DOCX/PPTX 只读渲染为 PDF；输出按源文件 SHA256 缓存，渲染后立即关闭 Office COM 进程。
- 结构文本：DOCX 按 OOXML 正文顺序抽取段落、表格和图片标记；PPTX 按形状坐标抽取文本、表格、图片、图表和分组对象。
- Office 不存在或渲染失败时，结构文本仍可阅读，接口返回明确的 503 原因，不启动常驻替代服务。
- MinerU 不参与任何标准、备用或后台解析路径。

## 可选 PP-DocLayout-M 单模块

仅在用户安装增强组件并设置 `LAYOUT_BACKEND=pp-doclayout-m` 时启用：

- 调用官方 `LayoutDetection(model_name="PP-DocLayout-M", engine="onnxruntime")`；
- 不实例化 `PPStructureV3`，不加载 OCR、公式、图表、表格识别子管线；
- CPU 线程默认 2、批量固定 1、输入边长默认 960；
- 以独立子进程运行，完成后退出；模型只下载和缓存一次；
- 建议用独立环境安装 `requirements-pp-doclayout.txt`，通过 `PP_DOCLAYOUT_PYTHON` 指向它；
- 启动前要求至少 3 GB 可用内存；资源不足或可选环境不存在时自动回退内置分析。

模型输出只能为已有原文块增加角色标签，不能采用模型生成的替代文本。

PaddleOCR 与模型仍不是标准依赖，避免把可选版面能力带入基础安装包。

Windows 可选安装示例：

```powershell
py -3.12 -m venv .pp-doclayout-venv
.\.pp-doclayout-venv\Scripts\python.exe -m pip install -r requirements-pp-doclayout.txt
```

环境位于项目内时会自动发现；放在其他位置则在 `.env` 设置 `PP_DOCLAYOUT_PYTHON`。

## AI 审核边界

AI 输入只包含候选 ID、标题、页码、评分原因、问题类型和最多 600 字页内摘录。输出必须：

- 引用已有 `candidate_id`；
- 保持标题文字与原文一致；
- 只调整 1–4 级层级或拒绝候选；
- 无法回查原文的标题一律丢弃。

## 目录自修正与人工校正

- 编号逻辑是有父级作用域的状态机，不对全书所有“一、二、”混合计数。
- 层级根据实际上下文推导：没有“第X节”时，“一、”可作为章下二级；不再绝对映射为三级。
- 同页错序只在子项能唯一续接最近前一父项时自动回挂；多解时不动。
- 带多个句内逗号的编号行会被降为“疑似正文换行”，防止把“统\n一、效能的原则…”误当标题。
- 阅读器的目录校正会显示可解释问题，支持全量编辑并事务化重建 chunk/source-map/FTS 章节归属。
- 每次自动或人工保存都写入 `toc_revisions` 前后快照，识别结果不再是无法追溯的黑盒。

## 验收记分卡

| 指标 | 目标 |
|---|---:|
| 普通 PDF 无 OCR 导入峰值 | < 800 MB |
| OCR 额外页面位图常驻数 | 1 |
| OCR 并发 | 1 |
| 目录父子异常 | 0 |
| AI 无证据新增标题 | 0 |
| Markdown 页眉/页脚重复 | 0 |
| 结构化证据可回查率 | 100% |

回归样本至少包含《行政管理学夏书章》、扫描题库、中文论文、双栏论文和带表格 PDF。

当前自动化回归包含弱页选择、缓存前置、逐页惰性渲染、跨页段落恢复、目录证据约束和
PP-DocLayout-M 不改写原文，共同位于 `backend/tests/test_lightweight_document_pipeline.py`。

## 磁盘清理边界

`scripts/cleanup_workspace.ps1` 默认只预览，添加 `-Apply` 才删除项目源码区的 Python/pytest
缓存和 UI 测试截图。脚本固定保护 `.git`、`.venv`、`node_modules`、`backend/data` 与
`frontend/dist`，因此不会删除文献、数据库、备份、模型、运行环境或网页入口产物。
