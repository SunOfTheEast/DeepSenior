# DeepTutor RAG 数据建模草案（归档）

更新时间：2026-03-23

状态：**历史草案，不再作为实现依据**

---

## 说明

这份文档是 v1 阶段的原始数据建模草案，已被后续文档替代。为避免实现时继续引用旧 DDL / 旧字段，本文件已压缩为归档说明。

若需要当前有效设计，请按以下优先级阅读：

1. `RAG_PLANNING_3.md` — 总纲与 phase 顺序
2. `RAG_SCHEMA_V3.md` — 最终 schema / 内容源 / 约束
3. `DATA_MODEL_CONVERGENCE.md` — 数据语义与边界
4. `KNOWLEDGE_API_CONTRACT.md` — `CardStore` / `CardRetriever` / Worker 接口
5. `RAG_EVAL_AND_GATING.md` — 覆盖率、离线评测、shadow/gray 门禁

---

## 为什么归档

原草案中有多处内容已与 v3 规划冲突，包括但不限于：

- 旧版 `audit_task_type_enum` 缺少 `concept_link_missing`
- 旧版 `knowledge_card.tags` 仍混合题型/方法/思想语义
- 旧版 mastery 仍以 `student_card_mastery` 为主路径
- 旧版 DDL 未体现 `solution_card_link` / `concept_alias` / `knowledge_card_concept_link` 的最终边界

继续把它当作“当前模型”使用，会直接导致实现偏离 `RAG_PLANNING_3.md`。

---

## 保留价值

本文件仅保留一项用途：

- 作为 v1 设计思路的历史归档，供回溯“最早为什么想到 `question -> solution -> slice`”

除此之外，不再承担：

- 最终 DDL
- 当前 JSON schema
- 审计任务设计
- mastery 主路径设计

---

## 当前结论

`question -> solution -> slice` 这一总体方向仍然保留；
但具体字段、表结构、审计任务、concept / alias / mastery / RAG 接口，均以 v3 文档集为准。
