# 轻量文档解析管线

## 产品决策

MinerU 不再作为标准安装或默认导入依赖。默认链路为：

1. `PDFText/pypdfium2` 提取文本块、行、坐标和字体；失败时回退 PyMuPDF。
2. `RapidOCR + ONNX Runtime` 仅处理文本层不足的页，逐页渲染、逐页释放。
3. 内置字号/坐标版面分析处理普通文档；PP-DocLayout-M ONNX 单模块是显式可选增强。
4. 目录由书签、编号语义、版面证据和负面句式共同评分，不凭空补标题。
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

目录、Markdown、切块和来源定位从该结构派生，不能用生成后的 Markdown 覆盖它。

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
