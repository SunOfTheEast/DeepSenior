# 熟练度追踪设计

更新时间：2026-03-23

关联文档：
- `RAG_PLANNING_3.md` — 总纲（全局依赖图与里程碑）
- `DATA_MODEL_CONVERGENCE.md` — 数据模型收敛设计（concept 表结构、concept_id 规则）
- `RAG_EVAL_AND_GATING.md` — 覆盖率统计与上线门禁
- [`AGENT_CONTEXT_OPTIMIZATION_DESIGN.md`](../context/AGENT_CONTEXT_OPTIMIZATION_DESIGN.md) — 上下文优化设计（三层标签体系原始设计）

---

## 本文档回答的问题

**怎么记录学生学了什么。**

覆盖范围：三层 concept 模型、两层兼容模式、分层更新规则、bridge 系数、覆盖率门槛、历史迁移规则。

对应总纲 Phase E、G（mastery 部分）。

---

## 核心思想

### 练了什么就记什么

学生用替代方法完成题目时，不同层的 concept 应有不同的更新策略：

- **思想层**（thinking）：方法无关，做了就更新 — 但 Phase G 前禁用
- **题型层**（problem）：方法无关，做了就更新
- **方法层**（method，标准）：学生没用到，冻结
- **方法层**（method，替代）：审计通过后才更新

### 宁可少记，不可错记

所有不确定的场景宁可跳过更新，也不写入可能错误的 mastery。

---

## 1. 记忆层契约

### EpisodicMemory 扩展字段

- `question_target_card_ids`
- `solution_card_ids`
- `problem_concepts`
- `method_concepts`
- `thinking_concepts`
- `needs_solution_card_audit`
- `needs_concept_audit`

### 兼容模式规则（Phase G 前）

- `problem_concepts`：来自题目目标卡对应的 primary problem concept
- `method_concepts`：来自正式 `solution_card_link` 对应的 primary method concept
- `thinking_concepts`：默认空，不做自动填充

---

## 2. 在线更新规则

### Phase G 前（两层兼容模式）

- `problem` 层：正常更新
- `method` 层：仅在正式 solution 索引 ready 时更新
- `thinking` 层：默认不更新

### Phase G 后（完整三层模式）

- `problem` 层：正常更新
- `method` 层：按正式索引更新
- `thinking` 层：仅在已发布 thinking concept 时更新

---

## 3. Bridge 规则

solution-level 掌握变化同步到 concept-level 时的衰减系数：

| 层级 | 系数 | 说明 |
|---|---|---|
| `problem` | 50% | 始终生效 |
| `method` | 40% | 始终生效 |
| `thinking` | 60% | Phase G 前禁用；Phase G 后启用，初始 60% |

说明：

- 在 `thinking` 层未正式上线前，不允许提前使用 60% 系数
- 所有系数在完整 concept 独立化后再做数据校准

---

## 4. 覆盖率门槛

Phase E 上线前，必须满足以下映射覆盖率：

- `question_card_link` 中 `relation=target` 的卡片，≥95% 已有 `problem` 层 active primary concept link
- `solution_card_link` 中 `relation=primary` 的卡片，≥90% 已有 `method` 层 active primary concept link

未达标前不允许灰度上线两层更新。

覆盖率的离线统计口径与上线门禁执行，统一见 `RAG_EVAL_AND_GATING.md`。

---

## 5. 运行时 Fallback

即使覆盖率达标，运行时仍可能遇到缺失 primary link 的卡片。处理规则：

1. `problem_concepts` 填充时，若某 target card 缺少 `problem` 层 primary concept → 该 card 对应的 concept 更新**静默跳过**（不更新，也不报错），同时写一条 `concept_link_missing` 警告日志
2. `method_concepts` 填充时，若某 solution card 缺少 `method` 层 primary concept → 该 solution 的 concept 更新标记为 `pending`，并自动入队 `rag_audit_task`（类型 `concept_link_missing`）
3. 禁止在缺失 primary link 时 fallback 到旧的 `card_id` 作为 `concept_id` —— 这会让兼容模式的边界变得模糊

---

## 6. 快照接口

`SemanticMemory` 新增面向不同消费者的投影接口，替代通用的 `to_context_string()`：

```python
def to_distill_snapshot(self, tags: list[str], max_methods: int = 3) -> str: ...
def to_progress_snapshot(self, max_weak: int = 5, max_errors: int = 3) -> str: ...
def to_recommend_snapshot(self, current_tags: list[str], max_weak: int = 4) -> str: ...
```

这些接口在 Phase E 上线，供 `AGENT_CONTEXT_OPTIMIZATION_DESIGN.md` Phase 4（Memory/Progress/Recommend prompt 瘦身）消费。

---

## 7. 历史迁移规则

### 迁移原则

- 不追求语义上的"天然无损"
- 追求可解释、可回滚、低误伤

### 回填规则

1. 每条 legacy card mastery 只迁移到**一个** concept，不允许跨层复制
2. 默认迁移目标层为 `problem`（题型层），理由：card mastery 反映的是"学生在这个知识点上的整体表现"，与题型层语义最接近
3. `method` 层从新数据开始累计，不做历史回填（避免"做过一道联立方程的题"就被记为"掌握了联立方程这个方法"）
4. `thinking` 层从新数据开始累计（Phase G 后才启用）
5. 若目标 card 在 `problem` 层没有 active primary concept，该条 mastery 暂不迁移，标记为 `deferred` 并入审计
6. 迁移时只迁移 `level` 和 `last_practiced`，**不迁移** `practice_count` / `error_count` / `consecutive_correct`（这些计数在 card 粒度和 concept 粒度语义不等价，迁移会放大失真）
7. 原始 `student_card_mastery` 全量保存在 legacy 快照表
8. 迁移过程写 `mastery_migration_log`，含原 card_id、目标 concept_id、迁移层、迁移的字段

### 不允许的做法

- 不允许按权重把旧 mastery 平均拆给多个 concept
- 不允许把一条 legacy mastery 复制给多个 concept（即使它们在不同层）
- 不允许把无 primary 的 card 强行迁移
- 不允许迁移 practice_count / error_count 等计数统计

---

## 8. 实施任务

### Phase E：两层 Mastery 兼容模式

| 任务 | 改动位置 | 做什么 |
|---|---|---|
| E1 EpisodicMemory 扩展 | `agent/memory/data_structures.py` | 增加 `problem_concepts/method_concepts/thinking_concepts` |
| E2 TutorManager export 适配 | `tutor_manager.py` | 从 `question_card_link` + `solution_card_link` + `knowledge_card_concept_link` 填充两层 concept |
| E3 `_apply_update` 两层逻辑 | `agent/memory/memory_manager.py` | `problem` 正常更新，`method` 走安全阀 |
| E4 bridge 系数两层化 | `memory_manager.py` | `problem=50%`、`method=40%` |
| E5 快照接口 | `SemanticMemory` | 增加 `to_distill_snapshot()` / `to_progress_snapshot()` / `to_recommend_snapshot()` |
| E6 显式禁用 thinking | Memory / export 层 | `thinking_concepts` 默认空，未发布前不更新 |

前置依赖：Phase D（`DATA_MODEL_CONVERGENCE.md`）

验收：

- 替代解法场景下，`problem` 层正常更新，标准 `method` 层冻结
- 文档、代码和指标都不会误报"thinking 层已上线"
- 缺失 primary link 的卡片不会静默写入错误 mastery

### Phase G（mastery 部分）：三层切换与历史迁移

| 任务 | 改动位置 | 做什么 |
|---|---|---|
| G2 thinking concept 发布 | 内容层 / 教师后台 | 允许正式发布 `thinking` concept |
| G4 Memory 三层切换 | `memory_manager.py` / `data_structures.py` | 从两层切换到三层在线更新 |
| G5 历史 mastery 保守迁移 | 数据层 / 迁移脚本 | legacy `student_card_mastery` -> `student_concept_mastery` |
| G6 桥接系数校准 | `memory_manager.py` | thinking 60%、problem 50%、method 40% 基于数据验证后调整 |
| G7 legacy 下线策略 | 数据层 | `student_card_mastery` 降级为归档镜像 |

前置依赖：Phase F（`RAG_RETRIEVAL_DESIGN.md`）

验收：

- mastery 主路径完全基于 `concept_id`
- `thinking` 层只在有正式 concept 时参与更新
- 兼容模式可以安全下线
- 历史 mastery 数据迁移无重复记账
