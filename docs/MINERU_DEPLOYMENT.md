# MinerU 按需部署方案

日期：2026-08-24

## 结论

本机适合部署 **独立 Python 3.12 环境 + pipeline 后端 + 每次任务临时启动、完成后退出**。不建议 Docker/vLLM 常驻，也不建议本机 VLM 后端。

MinerU 当前 CLI 在未指定 `--api-url` 时会临时启动本地 `mineru-api`，任务完成后由客户端管理该进程。这与本产品“无常驻模型”的目标一致。主应用只负责提交任务、读取结果和显示进度，不导入 MinerU/Torch 到自身 Python 3.14 进程。

## 本机基线

| 项目 | 实测 | 判断 |
|---|---:|---|
| 内存 | 15.7 GB，总空闲约 4.8 GB | 达到官方最低总量，但当前空闲不足 |
| GPU | RTX 4050 Laptop，6 GB VRAM | 满足 pipeline 4 GB 门槛；不满足 VLM 8 GB 门槛 |
| D 盘 | 约 63.6 GB 可用 | 可容纳 20 GB 级安装，需保留清理余量 |
| 当前应用后端 | 约 99 MB Working Set | 不是主要内存瓶颈 |
| Python | 主环境 3.14；另有 3.13 | Windows MinerU 需要 3.10–3.12，必须另装 3.12 |
| Docker/WSL | Docker 未安装；WSL 尚未完成安装 | 不采用容器路线 |

## 运行形态

```text
用户选择“高精度解析”
        ↓
预检：空闲 RAM ≥ 8 GB、D 盘 ≥ 30 GB、无其他重型任务
        ↓
主应用全局任务中心进入 waiting / mineru_parsing
        ↓
独立 Python 3.12 执行 mineru CLI -b pipeline
MINERU_MODEL_SOURCE=modelscope
并发=1，单文献，长文档按窗口处理
        ↓
读取 Markdown / JSON / 图片 / 阅读顺序 → 统一 source map
        ↓
MinerU 临时 API 与模型进程退出，释放 RAM/VRAM
        ↓
本地四级目录连续性审计；低置信度结果等待人工确认
```

## 资源守卫

1. MinerU 与 OCR、深度分析、PPTX 图片处理共用“重型任务锁”，同一时间只运行一个。
2. 提交前运行 `scripts/mineru_preflight.ps1`；空闲内存不足 8 GB 时任务保持等待并提示关闭 Chrome、IDE 或其他模型程序。
3. 设置 `MINERU_API_MAX_CONCURRENT_REQUESTS=1`，避免官方默认多并发放大峰值。
4. 模型使用 ModelScope 或预下载后的 local 模式，避免每次唤醒探测和重复下载。
5. 完成后只保留 Markdown、JSON、source map 和用户需要的图片；临时 API 输出按短周期清理。
6. 处理 400 页以上教材时先以 20–40 页窗口进行基准，稳定后再放大全文窗口。
7. 若进程 Working Set 超过 11 GB、系统可用内存低于 1.5 GB或连续 3 分钟无进度，终止本轮并回退内置解析结果。

## 安装步骤（确认后执行）

1. 安装 Python 3.12，不替换产品现有 Python：

   `winget install -e --id Python.Python.3.12`

2. 创建独立环境，例如 `.runtime/mineru-py312`。
3. 在该环境安装 `uv`，再安装官方 `mineru[all]`。中国大陆网络使用官方文档给出的阿里云 PyPI 镜像和 `MINERU_MODEL_SOURCE=modelscope`。
4. 先解析 20 页样本，记录目录 F1、段落顺序正确率、峰值 RAM/VRAM、耗时。
5. 达到验收线后才在 UI 开放“高精度解析”；不满足则保留为实验功能或改用远程 `*-http-client`。

## 验收记分卡

| 指标 | 保留条件 |
|---|---:|
| 一级至四级标题准确率 | ≥ 95% |
| 标题召回率 | ≥ 92% |
| 段落阅读顺序人工抽检 | ≥ 95% |
| 峰值系统内存 | ≤ 14 GB |
| 峰值显存 | ≤ 5.5 GB |
| 失败时回退 | 内置解析结果完整可用 |
| 任务结束资源释放 | 60 秒内 MinerU 进程归零 |

## 许可证与发布

MinerU 当前采用基于 Apache 2.0、带附加条件的自定义开源许可证。开发期可做本机外部进程集成；在把 MinerU 或模型打入公开 Release 前，应单独核对许可证、模型权重条款和再分发要求。优先让用户独立安装运行时，产品只调用 CLI，可降低安装包、更新和许可证耦合。
