# 大知识库容量与检索评测基线

## 1. 适用范围

当前 SQLite + WAL + FTS5 架构定位为单机、单用户、以读取为主的个人知识库。以下数值是产品运行护栏，不是 SQLite 理论极限。

## 2. 容量分级

| 等级 | 建议范围 | 产品策略 |
|---|---:|---|
| 正常 | ≤1,000 本、≤250,000 chunks、数据库 ≤8 GB、FTS P95 ≤500 ms | 继续使用 SQLite；分页、按需详情和批量索引 |
| 预警 | 1,000–2,000 本、250,000–1,000,000 chunks、数据库 8–20 GB | 设置页提示容量预警；执行索引/完整性检查；停止增加同步重型查询 |
| 迁移 | >2,000 本、>1,000,000 chunks、数据库 >20 GB、FTS P95 连续三次 >1 s，或需要多用户/多写入者 | 迁移到 PostgreSQL；不得只通过提高超时掩盖问题 |

任一条件达到更高等级即采用该等级。图片、OCR 缓存和原文件不计入数据库大小，但单独受存储治理约束。

## 3. 可重复容量测试

```powershell
.venv\Scripts\python scripts\benchmark_large_library.py `
  --books 1000 --chunks-per-book 100 --queries 50 `
  --output backend\eval\reports\capacity.json
```

测试只创建临时合成数据库，不读取或写入 `backend/data/study.db`。它记录数据库体积、导入耗时、40 本分页列表和 FTS Top-10 的 P50/P95。合成测试不包含 PDF 解析、OCR、向量模型和多用户并发，因此只能用作版本间回归基线。

### 3.1 已保存基线（2026-08-31）

| 规模 | 数据库 | 导入 | 列表 P95 | FTS P95 | 结论 |
|---:|---:|---:|---:|---:|---|
| 1,000 本 / 100,000 chunks | 77.35 MB | 1.168 s | 0.062 ms | 9.319 ms | 通过合成阈值 |
| 3,000 本 / 300,000 chunks | 237.58 MB | 3.845 s | 0.227 ms | 42.013 ms | 通过合成阈值；按资料本数进入迁移评审区 |

报告分别保存在 `backend/eval/reports/capacity-1000-books.json` 和
`backend/eval/reports/capacity-3000-books.json`。3,000 本结果只证明本机合成短文本在单进程读取下可用，
不能外推为 3,000 本真实长文档已经可用。下一阶段仍需加入真实页数分布、OCR 缓存、向量索引、并发导入、
备份恢复时长与失效锚点全量审计。

## 4. PostgreSQL 迁移路线

1. 保持 API、SQLAlchemy Model 和稳定 source ID 不变，先把数据访问从模块级全局 engine 收敛为 repository/session 接口。
2. 增加 PostgreSQL 双写实验：SQLite 仍为主库，PostgreSQL 校验行数、外键和 source ID。
3. FTS5 映射为 `tsvector + GIN`；中文分词仍在应用层生成 token，避免迁移时改变召回语义。LIKE 兜底映射为 `pg_trgm`。
4. 可选向量索引从 ChromaDB 迁至 `pgvector`，RRF 和重排接口保持不变。
5. 在线导出采用主键游标，每批 1,000 行；迁移后执行行数、哈希抽样、引用锚点和固定检索集双跑。
6. 只有固定评测指标不下降、备份恢复演练通过后才切换读流量；SQLite 快照保留为只读回滚点。

不在尚未达到迁移等级时引入 PostgreSQL 运行依赖，以维持双击启动和低维护成本。

## 5. 固定检索评测

当前固定集位于 `backend/eval/retrieval_v2.json`，包含 114 题：102 个可回答问题和 12 个无答案问题，
覆盖社科、人文与文学、自然与生物医学、数理计算、扫描 OCR、英文和跨文献检索。它使用稳定字符串 ID，
不依赖真实库主键，也不会向模型发送内容。v1 继续保留用于历史比较。

```powershell
.venv\Scripts\python scripts\evaluate_retrieval.py `
  --output backend\eval\reports\retrieval.json
```

指标包括：

- `Recall@1/3/5`：相关 chunk 是否进入前 K 项；
- `MRR`：首个相关结果排名；
- `citation_correct_rate`：确定性抽取式回答引用首条结果时，该结果是否属于金标准来源；
- `no_answer_rejection_rate`：无答案问题是否零命中并触发拒答。

数据集版本和最低阈值随报告保存。新增案例只能发布新版本，不得为通过回归而静默修改旧案例或降低阈值。
`retrieval-v2-baseline.json` 当前结果为 Recall@1 0.9706、Recall@3 0.9902、MRR 0.9853、
引用正确率 0.9804、无答案拒答率 1.0。它是人工构造的固定回归集，下一版应持续吸收脱敏后的真实失败问题，
并把“检索到了相似内容但不足以回答”的难负例与人工引用判定加入评测。
