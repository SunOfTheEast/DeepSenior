# 系统演进总纲（v3）

更新时间：2026-03-23

---

## 子文档索引

| 文档 | 回答什么 | 覆盖 Phase |
|---|---|---|
| [`AGENT_CONTEXT_OPTIMIZATION_DESIGN.md`](../context/AGENT_CONTEXT_OPTIMIZATION_DESIGN.md) | 怎么让 prompt 更精简 | Phase 0, A, per-agent Phase 2-4 |
| `plan.md` | v2 入口摘要、路线图与在线链路 | Phase B-H |
| `DATA_MODEL_CONVERGENCE.md` | 数据该长什么样、从哪来 | Phase B, C, D(数据层), G(数据层) |
| `MASTERY_TRACKING_DESIGN.md` | 怎么记录学生学了什么 | Phase E, G(mastery) |
| `KNOWLEDGE_API_CONTRACT_V2.md` | `MethodRouter` / `CardSelector` / `CardRetriever` v2 接口 | Phase F 执行基线 |
| `METHOD_CATALOG_CONTRACT_V2.md` | 方法目录、`question -> topic` 映射与 slot 规则 | Phase B, F 执行基线 |
| `RAG_EVAL_AND_GATING_V2.md` | v2 评测、shadow/gray 门禁 | Phase F-H 执行基线 |
| `AUDIT_OPERATIONS_DESIGN.md` | 怎么保证数据质量并持续改进 | Phase D(审计), H |
| `RAG_SCHEMA_V3.md` | 最终内容源 / DDL / 约束 | Phase B-H 执行基线 |
| `RAG_RETRIEVAL_DESIGN.md` | 旧版运行时检索设计（历史参考） | Historical reference |
| `KNOWLEDGE_API_CONTRACT.md` | 旧版接口基线（历史参考） | Historical reference |
| `RAG_EVAL_AND_GATING.md` | 旧版门禁口径（历史参考） | Historical reference |
| `RAG_DATA_MODEL_DESIGN.md` | v1 历史草案（归档） | Archive only |

---

## 全局实施顺序

```text
Phase 0 快赢项（token 硬截断）
  ↓
Phase A 语义收敛（relation 可见、slice hint 隔离、预算化 helper）
  ↓
Phase B 内容仓库 / CardStore
  ↓
Phase C 正式 solution 索引
  ↓
Phase D 最小 concept 壳模型 + 全局审计权威数据源
  ↓
Phase E 两层 mastery 兼容模式
  ↓
Phase F MethodRouter + CardSelector RAG
  ↓
Phase G 完整 concept 注册表 + 三层独立化 + 历史迁移
  ↓
Phase H 飞轮闭环与运营化
```

## 文档优先级

1. **执行基线（v2）**：`RAG_SCHEMA_V3.md`、`KNOWLEDGE_API_CONTRACT_V2.md`、`METHOD_CATALOG_CONTRACT_V2.md`、`RAG_EVAL_AND_GATING_V2.md`
2. **设计边界**：`plan.md`、`DATA_MODEL_CONVERGENCE.md`、`MASTERY_TRACKING_DESIGN.md`、`AUDIT_OPERATIONS_DESIGN.md`、`confused.md`
3. **旧方案参考**：`RAG_RETRIEVAL_DESIGN.md`、`KNOWLEDGE_API_CONTRACT.md`、`RAG_EVAL_AND_GATING.md`
4. **历史归档**：`RAG_DATA_MODEL_DESIGN.md`、`RAG_PLANNING.md`、`RAG_PLANNING_2.md`

如果文档之间出现冲突，按以上优先级裁决。

### 可并行部分

- Phase 0 与 Phase A 并行
- Phase B 的 CardStore 与 Phase A 的 ContextBuilder 改造并行
- Phase D 的审计后台壳子，可在 Phase C 后半段并行
- Phase H 的看板壳子，可在 Phase G 后半段并行
- Per-agent prompt 优化（Phase 2-3）在 Phase A 完成后随时启动，与 Phase B-H 互不阻塞
- Per-agent prompt 优化（Phase 4）需等 Phase E 快照接口就绪

### 不建议并行的部分

- 没有 `solution_card_link` 前，不上线 Planner RAG
- 没有 Phase D 的最小 concept 壳模型前，不做 alias 标准化
- 没有 Phase E 的两层 mastery 前，不上线 Phase F
- 没有 Phase G 的正式 thinking concept 前，不启用 thinking 更新

---

## 关键里程碑

| 里程碑 | 包含 | 标志 |
|---|---|---|
| M-1 快赢落地 | Phase 0 | history token 下降，零回归 |
| M0 语义边界收敛 | Phase A | `relation` 可见，slice hint 不再污染 card hint |
| M1 正式索引可用 | Phase B + C | `solution_card_link` 生效，导出不再靠模糊匹配 |
| M1.5 最小 concept 壳模型可用 | Phase D | alias 标准化、审计 proposal、concept link 有权威数据源 |
| M2 两层 mastery 生效 | Phase E | problem/method 层更新正确，thinking 未启用 |
| M3 RAG 检索上线 | Phase F | MethodRouter + CardSelector 上线，Planner 能拿到正确参考卡，且更新链路一致 |
| M4 完整 concept 独立化 | Phase G | `concept_id` 成为 mastery 主键，thinking 层正式上线 |
| M5 飞轮闭环 | Phase H | 新方法发现 -> 审计 -> 发布 -> 推荐完整闭环 |

---

## 全局设计原则

1. **训练目标与方法参考显式分区** — `question_card_link` 管目标，`solution_card_link` 管方法参考
2. **卡片不等于 concept** — 允许多对多，但每层必须有单一 primary
3. **在线只读，离线写回** — 在线链路不写正式索引，不消费未审批 proposal
4. **审计是发布门** — 新方法 / 新 concept / 新 alias 必须可查询、可审批、可回滚
5. **兼容模式显式声明边界** — Phase G 前只做 problem + method 两层，不宣称 thinking 已上线
6. **concept_id 永不更换** — 只原地加字段，merge/split 走正式 lineage

---

## 验收指标

### 正确性

- `background/prereq` 卡不再被误记为训练目标
- 没有正式索引的替代方法不会进入 ready
- RAG 检索命中后，mastery 更新范围与索引层一致
- thinking 层在正式上线前不产生误更新

### 成本

- Planner / PathEvaluator / Review explain_concept 的 prompt 显著下降
- Review "解释概念" 不再全量注入所有卡片

### 可运营性

- 审计任务可按状态查询
- proposal 可审批、可回滚、可追踪
- 历史迁移有 lineage 日志

---

## 待决定项

| 编号 | 问题 | 建议 | 决定阶段 | 关联文档 |
|---|---|---|---|---|
| D1 | `concept` 是否需要层级树？ | 需要，Phase G 再补完整树结构 | Phase G 前 | DATA_MODEL |
| D2 | embedding 放 PostgreSQL 还是本地索引文件？ | 1000-5000 卡阶段优先本地索引 | Phase F 前 | KNOWLEDGE_API_CONTRACT_V2 |
| D3 | `MethodRouter` 是否允许自由生成 slot？ | 不允许，只能从 Method Catalog 闭合选择 | Phase F 前 | KNOWLEDGE_API_CONTRACT_V2 |
| D4 | 历史 mastery 是否允许按权重拆分？ | 不建议，默认只迁 problem 层 primary | Phase G 前 | MASTERY_TRACKING |
| D5 | 半自动审批是否开放？ | 默认关闭，仅低风险范围灰度 | Phase H | AUDIT_OPS |
| D6 | thinking concept 谁维护？ | 教研组主词表，LLM 只提建议 | Phase H | AUDIT_OPS |

---

## 演进历史

- `RAG_PLANNING.md` — v1 初版规划
- `RAG_PLANNING_2.md` — v2 Codex 审计后修订（语义收敛优先、concept 独立化、审计全局化）
- `RAG_PLANNING_3.md`（本文档）— v3 最终版（concept 前移、两层兼容、迁移防重记、alias 消歧、覆盖率门槛），并补齐执行基线文档
