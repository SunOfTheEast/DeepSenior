# RAG Eval And Gating v2

更新时间：2026-03-23

状态：**执行基线（v2）**

关联文档：
- `KNOWLEDGE_API_CONTRACT_V2.md` — `MethodRouter` / `CardSelector` / `CardRetriever` 契约
- `METHOD_CATALOG_CONTRACT_V2.md` — 方法目录、slot、`question -> topic` 解析
- `RAG_SCHEMA_V3.md` — 覆盖率与发布态索引口径
- `MASTERY_TRACKING_DESIGN.md` — 覆盖率门槛与误更新边界
- `AUDIT_OPERATIONS_DESIGN.md` — 审计积压、proposal 采纳率与回滚指标

> 说明：自 2026-03-23 起，**新实现统一以本文档为准**。旧 `RAG_EVAL_AND_GATING.md` 仅保留为旧版 `knowledge_point_tags` 检索方案的历史门禁参考。

---

## 本文档回答的问题

**v2 的 MethodRouter / CardSelector / fallback 什么时候可以影子上线、灰度上线、全量上线。**

---

## 1. 评测资产

建议统一放在：

```text
data/evals/rag_v2/
  method_router_cases.jsonl
  selector_goldens.jsonl
  shadow_sessions_v2.jsonl
  coverage_snapshot.json
```

### 1.1 `method_router_cases.jsonl`

用于验证 `MethodRouter` 是否把学生方法路由到正确 slot。

每行最小字段：

- `question_id`
- `chapter`
- `topic`
- `problem_text`
- `student_work`
- `student_approach`
- `expected_primary_slot`
- `acceptable_cross_slots`

### 1.2 `selector_goldens.jsonl`

用于验证 `CardSelector` 是否从候选卡中挑出合理结果。

每行最小字段：

- `question_id`
- `chapter`
- `topic`
- `router_primary_slot`
- `candidate_card_ids`
- `expected_selected_card_ids`
- `acceptable_selected_card_ids`
- `expected_additional_need`

### 1.3 `shadow_sessions_v2.jsonl`

用于 replay 线上样本，比较“旧启发式上下文 vs v2 路由检索上下文”。

每行最小字段：

- `session_id`
- `question_id`
- `active_solution_id`
- `chapter`
- `topic`
- `problem_text`
- `student_work`
- `student_approach`
- `baseline_prompt_chars`
- `baseline_result`
- `router_result`
- `selector_result`

### 1.4 `coverage_snapshot.json`

记录每次上线前的正式索引覆盖率快照，作为门禁输入。

---

## 2. 指标定义

### 2.1 覆盖率

- `problem_primary_link_coverage`
  - 分子：`question_card_link.relation=target` 且存在 active primary `problem` concept link 的卡片数
  - 分母：所有 `question_card_link.relation=target` 卡片数
- `method_primary_link_coverage`
  - 分子：`solution_card_link.relation=primary` 且存在 active primary `method` concept link 的卡片数
  - 分母：所有 `solution_card_link.relation=primary` 卡片数

### 2.2 路由质量

- `method_router_top1_accuracy`
  - `primary_slot == expected_primary_slot` 的比例
- `method_router_slot_recall_at_3`
  - `slot_candidates[:3]` 中包含 `expected_primary_slot` 的比例

### 2.3 候选与精选质量

- `candidate_card_recall`
  - gold/acceptable 卡是否进入 `CardSelector` 的 `candidate_cards`
- `selected_card_recall_at_3`
  - gold/acceptable 卡是否进入 `selected_card_ids[:3]`
- `fallback_rate`
  - `CardRetrieveResult.fallback_used=true` 的比例
- `planner_card_adoption_rate`
  - 检索卡被 Planner prompt 实际保留并使用的比例

### 2.4 稳定性

- `planner_prompt_regression_p95`
  - 引入 `supplementary_cards` 后，Planner prompt chars 的 p95 增幅
- `planner_json_parse_error_delta`
  - 相比 baseline，Planner JSON 解析失败率变化
- `audit_backlog_growth_7d`
  - 最近 7 天 `pending + proposed` 净增长
- `misupdate_rate`
  - 审计后确认的错误 mastery 更新 / 总更新

### 2.5 辅助诊断指标

以下指标可保留，但**不再作为 v2 主门禁**：

- 旧 `alias_top1_match_rate`
- 旧 `alias_top3_recall`

它们只用于 concept / alias 数据质量诊断，不作为 v2 主链路放量依据。

---

## 3. 上线门禁

### 3.1 Phase E 门禁（沿用）

- `problem_primary_link_coverage >= 95%`
- `method_primary_link_coverage >= 90%`

未达标：

- 禁止开启 concept 两层在线更新
- 允许继续做 shadow / audit，但不放量更新 mastery

### 3.2 Phase F Shadow Mode 门禁

开始影子检索前必须满足：

- Phase E 已通过
- `method_router_top1_accuracy >= 0.80`
- `method_router_slot_recall_at_3 >= 0.95`
- `candidate_card_recall >= 0.90`
- `selected_card_recall_at_3 >= 0.80`
- `fallback_rate <= 0.25`

### 3.3 Phase F Gray Mode 门禁

从 shadow 转灰度前必须满足：

- `method_router_top1_accuracy >= 0.85`
- `method_router_slot_recall_at_3 >= 0.97`
- `candidate_card_recall >= 0.93`
- `selected_card_recall_at_3 >= 0.85`
- `fallback_rate <= 0.15`
- `planner_prompt_regression_p95 <= 15%`
- `planner_json_parse_error_delta <= +1pp`
- `audit_backlog_growth_7d <= 0`
- `misupdate_rate < 5%`

### 3.4 全量门禁

从灰度转全量前必须满足：

- 连续 7 天满足灰度门禁
- `planner_card_adoption_rate >= 0.60`
- proposal 采纳率 `> 70%`
- 无 P0/P1 教学回归

---

## 4. 回退规则

以下任一条件触发，必须停止扩大流量并回退到旧启发式上下文：

- `planner_json_parse_error_delta > +2pp`
- `misupdate_rate >= 5%`
- `audit_backlog_growth_7d > 0` 且连续 2 周未收敛
- `fallback_rate > 0.35` 且连续 3 天无下降
- 检索结果导致 Planner 明显偏离 `target_cards` 的训练目标

回退后保留：

- shadow / gray 日志
- `MethodRouter` / `CardSelector` 输出
- 审计任务
- 指标快照

禁止清空证据后“重新开始”。

---

## 5. 脚本与输出

建议新增：

- `scripts/rag/report_concept_coverage.py`
  - 输出 `coverage_snapshot.json`
- `scripts/rag/eval_method_router.py`
  - 输出 `method_router_top1_accuracy` / `method_router_slot_recall_at_3`
- `scripts/rag/eval_card_selector.py`
  - 输出 `candidate_card_recall` / `selected_card_recall_at_3` / `fallback_rate`
- `scripts/rag/replay_shadow_sessions_v2.py`
  - 输出 prompt 增幅、解析失败率、Planner 行为差异
- `scripts/rag/check_rollout_gate_v2.py`
  - 汇总门禁并给出 `pass/fail`

所有脚本输出必须支持：

- 机器可读 JSON
- 人类可读 Markdown 摘要

---

## 6. 最小验证场景

在第一轮评测资产中，至少要覆盖：

- 解析几何：椭圆参数方程替代设点联立
- 圆锥曲线：焦点弦/配极理论等与标准法错位的方法
- 跨章节：解析几何题使用三角代换
- 低置信：MethodRouter 无法命中 slot
- 候选不足：CardSelector 提出 `additional_need`

---

## 7. 文档与实现边界

- 本文档定义 **v2** 门禁与指标，不重复定义 schema；schema 见 `RAG_SCHEMA_V3.md`
- 本文档定义评测口径，不重复定义运行时接口；接口见 `KNOWLEDGE_API_CONTRACT_V2.md`
- 本文档定义何时允许开启功能，不替代灰度开关实现本身

