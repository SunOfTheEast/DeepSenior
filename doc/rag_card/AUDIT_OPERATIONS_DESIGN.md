# 审计与运营设计

更新时间：2026-03-23

关联文档：
- `RAG_PLANNING_3.md` — 总纲（全局依赖图与里程碑）
- `DATA_MODEL_CONVERGENCE.md` — 数据模型收敛设计（concept_id 稳定性、merge/split 规则）
- `MASTERY_TRACKING_DESIGN.md` — 熟练度追踪设计
- `RAG_SCHEMA_V3.md` — `rag_audit_task` 与正式索引 schema
- `KNOWLEDGE_API_CONTRACT.md` — Worker proposal / 持久化接口

---

## 本文档回答的问题

**怎么保证数据质量并持续改进。**

覆盖范围：`rag_audit_task` 全局权威数据源、审计状态机、审计 proposal 契约、半自动审批、教师看板、思想层渐进填充、质量指标。

对应总纲 Phase D（审计部分）、Phase H。

`rag_audit_task` 的最终字段定义以 `RAG_SCHEMA_V3.md` 为准；本文档负责状态机、权限边界与运营流程。

---

## 设计原则

### 审计是发布门，不是日志附件

- `rag_audit_task` 是全局权威数据源
- `SemanticMemory.pending_audit_tasks` 只允许保留摘要镜像
- 任何新方法 / 新 concept 映射 / 新别名补全，都必须可查询、可审批、可回滚

### 自动发现只能补建议，不能直接写正式索引

- 新方法发现后，先入 `rag_audit_task`
- RAG 只负责"提建议"
- 正式写入 `solution` / `solution_card_link` / `knowledge_card_concept_link` 只能发生在审计通过后

### 在线路径只读，离线路径写回

- 在线 Tutor / Review / Recommend 只消费已发布索引
- Audit Worker / 教师后台负责 proposals、审批、发布
- 禁止在线推理链路直接修改正式内容索引

---

## 1. `rag_audit_task` 设计

必须成为全局权威数据源，支持以下能力：

- 幂等入队（重复事件去重）
- 按状态查询
- proposal 存储
- 审批结果回写
- Worker 可恢复消费

### Proposal 负载

- `proposed_card_ids`
- `proposed_concepts`
- `proposed_aliases`
- `decision_meta`

### 审计状态机

```
pending → proposed → approved / rejected → done
```

- `pending`：新发现的方法 / 缺失的 concept link，等待 Worker 处理
- `proposed`：Worker 已生成建议（检索到的卡片、推荐的 concept 映射），等待人工审批
- `approved`：审批通过，等待正式写入
- `rejected`：审批拒绝，保留记录
- `done`：已正式写入到 `solution` / `solution_card_link` / `knowledge_card_concept_link`

### 任务类型

- `solution_card_index`：替代方法缺少知识卡索引
- `new_method_rag`：首次出现的替代方法
- `concept_link_missing`：卡片缺少某层的 primary concept link（Phase E 运行时 fallback 产生）

---

## 2. Audit Worker 契约

Worker 消费 `pending` 任务，产出 proposal：

1. 调用 CardRetriever 检索候选卡片
2. 根据候选卡片推荐 `proposed_card_ids` 和 `proposed_concepts`
3. 如需新增 alias，生成 `proposed_aliases`
4. 将任务状态更新为 `proposed`
5. 支持重试和断点恢复

### 结果回写

审批通过后：

1. 写入 `solution`（如有新解法）
2. 写入 `solution_card_link`
3. 写入 `knowledge_card_concept_link`（如有新 concept 映射）
4. 写入 `concept_alias`（如有新别名）
5. 更新任务状态为 `done`

---

## 3. 半自动审批

### 默认关闭

即使开启，也只允许低风险范围：

**允许半自动的**：

- alias 补全
- 已有 solution 下的 support/prereq 级补链

**禁止自动批准的**：

- 新 solution 发布
- 新 method 发布
- 新 thinking concept 发布
- primary concept 改写

### 保护机制

- 先跑 shadow mode（记录但不执行）
- 全量写审计日志
- 支持一键回滚
- 每日配额限制
- 抽检失败率超阈值自动熔断
- 教师黑名单与规则白名单

---

## 4. 教师后台

### 审计看板

- 待审任务列表（按状态、类型、时间筛选）
- Proposal 详情（推荐的卡片、concept 映射、理由）
- 审批历史
- 批量操作

### 思想层渐进填充

- 教师 + 高级 LLM + 教研组协作标注界面
- LLM 基于题目 + 标准解法 + 知识图谱建议候选 thinking 标签
- 教研组提案 → 讨论 → 审定 → 批量写入
- thinking concept 发布后，mastery 追踪自动激活（见 `MASTERY_TRACKING_DESIGN.md`）

### 新方法发布流

审批通过后自动生成：

- `solution` 记录
- `solution_card_link` 关联
- `knowledge_card_concept_link` 映射（如需）

---

## 5. 质量指标

| 指标 | 说明 | 目标 |
|---|---|---|
| 检索命中率 | RAG 检索的卡片被 Planner 实际使用的比例 | 持续提升 |
| 误更新率 | mastery 更新被事后判定为错误的比例 | < 5% |
| 审计积压 | `pending` + `proposed` 状态的任务数 | 不持续增长 |
| proposal 采纳率 | Worker 提案被审批通过的比例 | > 70% |
| 发布时延 | 从任务创建到 `done` 的中位时间 | 持续缩短 |
| 回滚率 | 已发布索引被回滚的比例 | < 2% |

---

## 6. 实施任务

### Phase D（审计部分）：全局审计权威数据源

| 任务 | 改动位置 | 做什么 |
|---|---|---|
| D4 `rag_audit_task` 权威数据源化 | 数据层 / Memory | 入队权威数据源从 `SemanticMemory` 迁到全局表 |
| D6 审计 proposal 契约 | 后台任务系统 | 支持 `proposed_card_ids/proposed_concepts/proposed_aliases` |

前置依赖：Phase C（`DATA_MODEL_CONVERGENCE.md`）

验收：

- 全局审计任务可查询、可恢复
- 应用重启后任务状态不丢失

### Phase H：飞轮闭环与运营化

| 任务 | 改动位置 | 做什么 |
|---|---|---|
| H1 教师审计看板 | 后台 | 待审任务、proposal、审批历史 |
| H2 新方法发布流 | 后台 + 数据层 | 审批后生成 solution / link / concept 映射 |
| H3 思想层渐进填充 | 教师后台 | thinking concept 的提案、讨论、发布 |
| H4 质量指标 | 监控 | 命中率、误更新率、积压、回滚率 |
| H5 A/B 验证 | 线上实验 | 启发式上下文 vs RAG 检索上下文 |
| H6 低风险半自动审批 | 审计后台 | 仅开放低风险 proposal 的半自动通道 |

前置依赖：Phase G

验收：

- 新方法从发现到发布形成闭环
- 思想层 concept 可以异步渐进增加，不阻塞主流程
- 审计积压、proposal 采纳率、误更新率可观测
