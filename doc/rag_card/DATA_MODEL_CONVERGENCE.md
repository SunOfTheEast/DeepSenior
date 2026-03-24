# 数据模型收敛设计

更新时间：2026-03-23

关联文档：
- `RAG_PLANNING_3.md` — 总纲（全局依赖图与里程碑）
- `RAG_SCHEMA_V3.md` — 最终 schema / DDL / 内容源契约
- `KNOWLEDGE_API_CONTRACT.md` — `CardStore` 与运行时读取接口
- `RAG_DATA_MODEL_DESIGN.md` — 原始数据建模草案（归档）
- `MASTERY_TRACKING_DESIGN.md` — 熟练度追踪设计
- `AUDIT_OPERATIONS_DESIGN.md` — 审计与运营设计

---

## 本文档回答的问题

**数据该长什么样、从哪来。**

覆盖范围：内容仓库（CardStore）、正式 solution 索引、concept 壳模型、concept_id 稳定性规则。

对应总纲 Phase B、C、D（数据层部分）。

如与 `RAG_SCHEMA_V3.md` 冲突，以 `RAG_SCHEMA_V3.md` 为准；本文档负责解释语义边界与实施顺序。

---

## 设计原则

### 原则 1：训练目标与方法参考必须显式分区

- `question_card_link` 负责"本题训练目标 / 背景 / 前置"
- `solution_card_link` 负责"某一解法的参考卡片"
- 下游模块（Planner / Review / Recommend）一律显式区分 `target_cards` 和 `supplementary_cards`

### 原则 2：卡片不等于 concept

- `KnowledgeCard` 是教学内容载体
- `Concept` 是 mastery / 推荐 / 迁移追踪的归因单位
- 允许一张卡关联多个 concept，也允许多个卡共享一个 concept
- 每张卡在每个 layer 上最多只有一个 `primary concept`

### 原则 3：在线链路只读已发布索引

在线 Tutor / Review / Recommend 只消费已发布的正式数据，不能写正式索引，不能消费未审批的 proposal，不能用 LLM 猜测出的标签直接改正式数据。

---

## 1. 运行时 ProblemContext

`ProblemContext` 必须从"扁平卡片列表"升级为"带语义分区的运行时结构"。

建议最小字段：

- `question_id`
- `chapter`
- `difficulty`
- `stem`
- `answer_schema`
- `standard_solution_id`
- `active_solution_id`
- `question_card_links`（保留 relation / weight）
- `target_cards`
- `background_cards`
- `prereq_cards`
- `solution_slice_hints_by_card`
- `knowledge_cards_by_id`

关键约束：

1. `from_unified_question()` 默认优先选择 `is_standard=true`，不依赖数组顺序
2. `KnowledgeCard.hints` 只保留概念级提示
3. slice hint 单独保留在 `solution_slice_hints_by_card`，不污染 card hint
4. `question_cards.relation` 在运行时必须可见

---

## 2. 正式索引层

### 2.1 `solution_card_link`

目的：让解法级卡片关联成为正式索引，替代运行时方法名模糊匹配。

建议字段：

- `solution_id`
- `card_id`
- `relation`：`primary | support | prereq | contrast`
- `weight`
- `source`：`author | audit`
- `status`：`active | deprecated`

### 2.2 最小 `concept` 壳模型

Phase D 先上线最小结构，Phase G 再扩展为完整注册表。

最小字段：

- `concept_id`
- `layer`：`problem | method | thinking`
- `name`
- `status`：`shadow | active | deprecated`
- `source`：`seed | author | audit`

说明：

- Phase D 先不要求完整层级树
- `thinking` 层可以存在空壳，但默认不参与在线更新
- Phase G 再补 `subject / chapter / canonical_name / tree_path` 等完整字段

### 2.3 concept_id 稳定性规则

`concept_id` 一旦在 Phase D 分配并被 mastery / alias / 审计记录引用，**在后续 Phase 中不允许更换**。Phase G 对 concept 表的扩展只能是**原地加字段**，不能重编码。

如果业务上确实需要合并或拆分 concept，必须走正式的 merge/split 流程：

**Merge（多合一）**：

1. 选择存活 `concept_id`（通常是使用量更大的一方）
2. 将被合并 concept 的 `student_concept_mastery` 按保守规则迁入存活 concept（取较高 level，不叠加次数统计）
3. 将被合并 concept 的 alias 全部重指向存活 concept
4. 被合并 concept 标记为 `deprecated`，保留 `merged_into: <存活 concept_id>`
5. 全量写 `concept_lineage_log`

**Split（一拆多）**：

1. 原 `concept_id` 保留，新拆出的 concept 分配新 ID
2. 已有 mastery 只留在原 concept，新 concept 从零开始
3. alias 按语义手工分配
4. 写 `concept_lineage_log`

**禁止的做法**：

- 不允许删除后重建同名 concept（会丢失所有历史关联）
- 不允许批量重编码 concept_id
- 不允许在 merge 时叠加 practice_count / error_count

### 2.4 `concept_alias`

目的：支持 `knowledge_point_tags` 标准化、教师词表维护、冷启动 alias 扩充。

建议字段：

- `alias`
- `concept_id`
- `layer`（继承自关联 concept 的 layer，用于消歧）
- `chapter`（可选，用于缩小同名 alias 的匹配范围）
- `confidence`
- `source`
- `status`：`active | deprecated`

#### 消歧规则

同一个 alias 文本可能指向不同层的 concept。运行时标签标准化必须遵守以下规则：

1. **只使用 `status=active` 的 alias 关联 `status=active` 的 concept**，shadow / deprecated 一律跳过
2. **同名 alias 冲突时，按以下优先级消歧**：
   - 优先匹配与当前 query 同层的 alias（Grader 输出的 tags 通常对应 method 层）
   - 同层内优先匹配与当前 `chapter` 一致的 alias
   - 仍有冲突时，取 `confidence` 最高的
   - 最终仍无法消歧的，保留所有候选，交由 Stage 2 embedding 精排决定
3. **冷启动生成的 alias 默认 `confidence=0.8`**，教师审定后提升为 `1.0`

### 2.5 `knowledge_card_concept_link`

目的：把 card 与 concept 的关系正式化。

建议字段：

- `card_id`
- `concept_id`
- `layer`
- `weight`
- `is_primary`
- `status`

最小约束：

- 每张卡在每个 `layer` 上最多一个 `is_primary=true`
- 在线过滤和历史迁移只默认信任 `primary`

### 2.6 Mastery 存储

过渡状态：

- 保留 `student_card_mastery` 作为 legacy 快照
- 新增 `student_concept_mastery`

目标状态：

- 在线主路径只读写 `student_concept_mastery`
- `student_card_mastery` 仅作为兼容镜像或历史归档

详细的更新规则和迁移规则见 `MASTERY_TRACKING_DESIGN.md`。

---

## 3. 实施任务

### Phase B：内容仓库与统一读取层

| 任务 | 改动位置 | 做什么 |
|---|---|---|
| B1 内容目录定稿 | `content/` | 建立 `questions/`、`knowledge_cards/`、`concepts/` 目录 |
| B2 CardStore | `agent/knowledge/card_store.py` | 统一读取 question/card/concept/solution |
| B3 ingest 契约 | `scripts/` | JSON -> 正式索引同步 |
| B4 ProblemBank 对齐 | `agent/recommend/problem_bank.py` | 与 CardStore 共用权威数据源 |
| B5 发布版本策略 | 内容发布流程 | 增量更新与版本控制 |

前置依赖：Phase A（语义收敛，见 `AGENT_CONTEXT_OPTIMIZATION_DESIGN.md`）

验收：

- 内容权威数据源明确
- Tutor / Recommend / 审计后台不再各自假设题库结构

### Phase C：正式 solution 索引与导出修正

| 任务 | 改动位置 | 做什么 |
|---|---|---|
| C1 `solution_card_link` DDL | 数据层 | 建立正式解法卡片关联表 |
| C2 导出逻辑去启发式 | `agent/tutor/tutor_manager.py` | 不再靠 `_find_solution_tags_for_method()` 推断正式索引 |
| C3 solution 状态机 | Memory / 审计层 | `pending -> ready -> rejected` 只由正式索引驱动 |
| C4 `slice_edge` 保底兼容 | 数据层 / Tutor 运行时 | 线性默认 + 流程边兼容 |
| C5 切法追踪 | 数据层 | 为中途换法、混合法预留 `student_solution_trace` |

前置依赖：Phase B

验收：

- `solution_tags` 不再来源于方法名模糊匹配
- 没有正式索引的替代方法一律 `pending`

### Phase D（数据层部分）：最小 concept 壳模型

| 任务 | 改动位置 | 做什么 |
|---|---|---|
| D1 `concept` 壳表上线 | 数据层 | 建立最小 `concept` 表，先支持 `problem/method`，`thinking` 可空 |
| D2 `concept_alias` 上线 | 数据层 | 建立 alias 权威数据源 |
| D3 `knowledge_card_concept_link` 上线 | 数据层 | 每层单一 `primary` 约束 |
| D5 alias 冷启动 | CardStore / 脚本 | 从现有 `tags` + `general_methods` 生成初始 alias |

实施顺序备注：D1-D3 的种子数据（`concept` 初始记录 + `knowledge_card_concept_link` 初始映射）与 D5 的 alias 冷启动应在同一个 ingest 批次中完成，确保 alias 生成时有 `concept_id` 可关联。

Phase D 的审计部分（D4、D6）见 `AUDIT_OPERATIONS_DESIGN.md`。

前置依赖：Phase C

验收：

- alias 标准化、concept link 有权威数据源
- 后续 RAG 和 mastery 模块可以基于正式 concept 工作

### Phase G（数据层部分）：完整 concept 注册表

| 任务 | 改动位置 | 做什么 |
|---|---|---|
| G1 扩展 `concept` | 数据层 | 增加 `subject/chapter/canonical_name/tree_path` 等完整字段（原地加字段，不换 ID） |
| G3 `knowledge_card_concept_link` 扩展 | 数据层 | 完整支持三层链接 |

前置依赖：Phase F

验收：

- concept 表具备完整层级树能力
- concept_id 在扩展过程中保持稳定
