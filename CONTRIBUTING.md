# 贡献指南

欢迎贡献。请先阅读 [PROJECT_HANDOVER.md](docs/PROJECT_HANDOVER.md) 了解架构与踩坑记录。

## 环境

- Python 3.12+，运行依赖见 `requirements.txt`，测试依赖见 `requirements-dev.txt`
- 前端：`cd frontend && npm install`

## 提交前

1. 单元测试：
   ```bash
   python -m pytest backend/tests/test_unit.py backend/tests/test_enhance.py backend/tests/test_semantic.py backend/tests/test_toc_heuristic.py -q
   ```
2. 前端改动后：`cd frontend && npm run build`

## 规范

- 提交信息用 `type: 描述`（feat / fix / docs / chore）
- 涉及云端传输的功能，请同步更新 `PRIVACY.md`
- 不要提交 `.env`、`backend/data/`、`frontend/dist/`、虚拟环境、`node_modules/`
