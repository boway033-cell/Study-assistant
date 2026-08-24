# Nature 文献工作流本地化设计

本轮参考 [Yuan1z0825/nature-skills](https://github.com/Yuan1z0825/nature-skills) 中
`nature-polishing`、`nature-reader`、`nature-paper-card`、`nature-paper2ppt`、
`nature-literature-pipeline` 与 `nature-downloader` 的公开方法，将其原则适配到
Study Assistant 的本地优先架构。这里的“适配”指产品方法与数据契约，不代表直接
运行外部 Skill，也不会绕过付费墙或机构认证。

## 方法映射

| 外部方法 | 本地产品实现 |
|---|---|
| nature-polishing | 先恢复结构再处理句子；保护术语、数字与证据边界；OCR/PDF 硬换行重排不改写原文 |
| nature-reader | 原版阅读、结构精读双层视图；chunk—章节—PDF 页码 source map；无法定位时明确降级为结构定位 |
| nature-paper-card | 深度分析生成固定 01–16 节证据卡片；关键判断引用稳定 source ID；来源不足明确标记 |
| nature-paper2ppt | 论文类型路由 + 问题—方法—证据—边界叙事；选章/选段生成中文可编辑 PPTX、来源备注与质量审计 |
| nature-literature-pipeline | DOI/arXiv/文件哈希去重基础、分类标签、阅读状态、收藏、进度和归档索引 |
| nature-downloader | OA/arXiv/Unpaywall + 当前 Chrome 馆藏交接；验证 PDF 与哈希并保存无秘密 manifest，不绕过认证 |

## 导入与结构恢复

1. 解析原始 PDF/DOCX/PPTX；扫描 PDF 使用 RapidOCR，并保留分页缓存。
2. 去除重复页眉页脚和明显 OCR 抖动。
3. 根据空行、标题、列表、公式和句末标点恢复段落；英文换行默认补空格，只在整行词片段或连字符断词时粘合。
4. 融合 PDF 书签、全文编号标题和字号/坐标标题块。
5. 支持“第X章/节”“一、”“（一）”“1.1/1.1.1”及英文论文 section 标题；同页可识别多个层级。
6. 深度分析时检查编号连续性和扁平目录，AI 只从带页码的原文候选中补缺，不得凭常识生成不存在的标题。

## 来源与归档契约

- paper_profiles 保存作者、期刊、年份、DOI、arXiv ID、语言、摘要、来源 URL、访问路径、阅读状态、收藏、评分和阅读进度。
- source_map_json 为每个 chunk 生成稳定 source ID，并记录章节与 PDF 页码。
- locator_mode 为 page-grounded 表示页码可核验；缺页码时降级为 structure-grounded，不伪造第 1 页。
- 本地上传的默认访问路径是 local_upload；后续如增加合法 OA/API 下载，必须记录 route、哈希与失败原因。

## 阅读卡质量门

- 固定存在 01–16 节，不增加无关输出。
- 关键判断至少包含一个来源 ID。
- 作者陈述、AI 分析和研究假设分开书写。
- 不可见的数据、实验、页码和新颖性写“材料不足，无法判断”。
- 审计结果随阅读卡返回前端；缺章节或无来源引用时显示“需要复核”。

## 选段到中文 PPTX

1. 用户明确选择整篇、章节、chunk 或阅读器选段；系统不会悄悄扩大证据范围。
2. 本地路由先判定 discovery / methods / resource / clinical / materials / review。
3. 提纲遵循“问题—缺口—方法—证据—可信度—意义—边界”，每个事实页绑定 source ID。
4. 有模型时只发送所选片段；无模型时生成本地证据提纲，不补造研究结果。
5. 输出 16:9 可编辑 PPTX、来源备注和 manifest，并做越界、密度与来源审计。

## 合法全文获取

- 补充材料必须显式确认；未确认时返回 409，不开始获取。
- 直接支持开放获取 PDF、arXiv 和 Unpaywall；图书馆/CARSI 仅生成当前浏览器交接入口。
- 不读取或导出 Cookie、密码、localStorage、会话文件，不绕过付费墙、DRM、验证码或 2FA。
- 文件需通过 HTTPS 公网地址检查、200MB 限制、`%PDF` 签名与 SHA-256 校验。
- Provider registry 与无秘密 manifest 是后续接入出版社授权 API 和更多知识库来源的扩展边界。

## 后续边界

- 公式图片低置信度裁剪、图表首次讨论处自动插入仍待实现。
- 出版商授权 API 尚未接入；不得使用镜像站、导出 Cookie 或绕过认证。
- 复杂多面板自动裁剪、矢量图重绘与最终人工演讲校对仍需后续增强。
- 批量主题订阅与定时推送尚未实现；当前归档模型已预留稳定标识和阅读状态。
