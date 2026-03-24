# RAG Eval And Gating

更新时间：2026-03-23

状态：**旧方案 / 仅供历史参考**

> 说明：本文档描述的是旧版 `knowledge_point_tags` 检索方案的门禁口径。自 2026-03-23 起，新实现请优先参考 `RAG_EVAL_AND_GATING_V2.md`。

关联文档：
- `RAG_PLANNING_3.md` — 里程碑与 phase 顺序
- `RAG_RETRIEVAL_DESIGN.md` — 检索方案与上线范围
- `MASTERY_TRACKING_DESIGN.md` — 覆盖率门槛与误更新边界
- `AUDIT_OPERATIONS_DESIGN.md` — 审计积压、proposal 采纳率与回滚指标

---

## 本文档回答的问题

**什么时候算“可以开始灰度”、什么时候必须回退、指标到底怎么算。**

---

## 1. 评测资产

建议统一放在：

```text
data/evals/rag/
  alias_cases.jsonl
  retrieval_goldens.jsonl
  shadow_sessions.jsonl
  coverage_snapshot.json
```

### 1.1 `alias_cases.jsonl`

用于验证 `knowledge_point_tags -> concept_alias -> concept_id`。

每行最小字段：

- `raw_tag`
- `chapter`
- `preferred_layer`
- `expected_concept_ids`

### 1.2 `retrieval_goldens.jsonl`

用于验证 `CardRetriever` 是否把正确参考卡排进 top-k。

每行最小字段：

- `question_id`
- `active_solution_id`
- `chapter`
- `tags`
- `fallback_query`
- `expected_card_ids`
- `acceptable_card_ids`

### 1.3 `shadow_sessions.jsonl`

用于 replay 线上样本，比较“启发式上下文 vs RAG 上下文”的差异。

每行最小字段：

- `session_id`
- `question_id`
- `solution_id`
- `student_approach`
- `grader_tags`
- `baseline_prompt_chars`
- `baseline_result`

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

### 2.2 alias 标准化

- `alias_top1_match_rate`
  - `normalize_tags()` 的第一候选命中 `expected_concept_ids` 的比例
- `alias_top3_recall`
  - top-3 候选中包含 `expected_concept_ids` 的比例

### 2.3 检索质量

- `stage1_candidate_recall`
  - gold 卡是否进入 Stage 1 候选集
- `retrieval_recall_at_3`
  - gold/acceptable 卡是否进入最终 top-3
- `retrieval_recall_at_5`
  - gold/acceptable 卡是否进入最终 top-5
- `planner_card_adoption_rate`
  - 检索卡被 Planner prompt 实际保留并使用的比例

### 2.4 稳定性

- `planner_prompt_regression_p95`
  - 引入 supplementary cards 后，Planner prompt chars 的 p95 增幅
- `planner_json_parse_error_delta`
  - 相比 baseline，Planner JSON 解析失败率变化
- `audit_backlog_growth_7d`
  - 最近 7 天 `pending + proposed` 净增长
- `misupdate_rate`
  - 审计后确认的错误 mastery 更新 / 总更新

---

## 3. 上线门禁

### 3.1 Phase E 门禁（两层 mastery）

直接沿用 `MASTERY_TRACKING_DESIGN.md`：

- `problem_primary_link_coverage >= 95%`
- `method_primary_link_coverage >= 90%`

未达标：

- 禁止开启 concept 两层在线更新
- 只允许保留 `solution_mastery` 与审计任务入队

### 3.2 Phase F Shadow Mode 门禁

开始影子检索前必须满足：

- Phase E 已通过
- `alias_top1_match_rate >= 0.85`
- `alias_top3_recall >= 0.95`
- `stage1_candidate_recall >= 0.95`
- `retrieval_recall_at_3 >= 0.80`
- `retrieval_recall_at_5 >= 0.90`

### 3.3 Phase F Gray Mode 门禁

从 shadow 转灰度前必须满足：

- `retrieval_recall_at_3 >= 0.85`
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

## 4. 脚本与输出

建议新增：

- `scripts/rag/report_concept_coverage.py`
  - 输出 `coverage_snapshot.json`
- `scripts/rag/eval_alias_normalization.py`
  - 输出 `alias_top1_match_rate` / `alias_top3_recall`
- `scripts/rag/eval_retrieval.py`
  - 输出 `stage1_candidate_recall` / `retrieval_recall_at_k`
- `scripts/rag/replay_shadow_sessions.py`
  - 输出 prompt 增幅、解析失败率、Planner 行为差异
- `scripts/rag/check_rollout_gate.py`
  - 汇总门禁并给出 `pass/fail`

所有脚本输出必须支持：

- 机器可读 JSON
- 人类可读 Markdown 摘要

---

## 5. 回退规则

以下任一条件触发，必须停止扩大流量并回退到启发式方案：

- `planner_json_parse_error_delta > +2pp`
- `misupdate_rate >= 5%`
- `audit_backlog_growth_7d > 0` 且连续 2 周未收敛
- 检索结果导致 Planner 明显偏离 target cards 的训练目标

回退后保留：

- 影子检索日志
- 审计任务
- 指标快照

禁止清空证据后“重新开始”。

---

## 6. 文档与实现边界

- 本文档定义门禁与指标，不重复定义 schema；schema 见 `RAG_SCHEMA_V3.md`
- 本文档定义评测口径，不重复定义检索逻辑；检索逻辑见 `RAG_RETRIEVAL_DESIGN.md`
- 本文档定义何时允许开启功能，不替代灰度开关实现本身
