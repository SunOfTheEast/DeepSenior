# Context Optimization — TODO List

基于 [AGENT_CONTEXT_OPTIMIZATION_DESIGN.md](./AGENT_CONTEXT_OPTIMIZATION_DESIGN.md) 梳理，截至 2026-03-23（Phase 0 + Phase 5 未完成项补充）。

---

## 已完成

### Context Governance Layer（基础设施）

| 文件 | 内容 | 状态 |
|------|------|------|
| `agent/context_governance/budget_policy.py` | `FieldBudget`、`BudgetPolicy`、7 套预定义策略（Planner/PathEval/Grader/ReviewExplain/MemoryDistill/Progress/Recommend） | ✅ |
| `agent/context_governance/assembler.py` | `ContextAssemblyResult`、`assemble()` 整 prompt 预算仲裁 | ✅ |
| `agent/context_governance/signature.py` | `context_signature()` SHA256 签名 | ✅ |
| `agent/context_governance/telemetry.py` | `log_assembly()` 组装观测 | ✅ |

### Phase 1: ProblemContext 预算化

| 事项 | 文件 | 状态 |
|------|------|------|
| `get_methods_for_llm()` — top-k 方法，带 max_methods/max_chars | `data_structures.py` | ✅ |
| `get_hints_for_llm()` — 按卡优先级取 top-k 提示，带 include_slice_hints 开关 | `data_structures.py` | ✅ |
| `get_common_mistakes_for_llm()` — 按卡优先级取 top-k 易错点 | `data_structures.py` | ✅ |
| `get_card_titles_for_llm()` — top-k 卡片标题 | `data_structures.py` | ✅ |
| slice hint 分离 — 不再合入 `KnowledgeCard.hints`，改存 `ProblemContext.solution_slice_hints_by_card` | `data_structures.py` | ✅ |
| 预算默认值集中维护 — `METHODS_BUDGET`/`HINTS_BUDGET`/`MISTAKES_BUDGET`/`CARD_TITLES_BUDGET` | `budget_policy.py` | ✅ |

### Phase 2: Tutor Agent 分治

| 事项 | 文件 | 状态 |
|------|------|------|
| PlannerAgent — 输入改为 `selected_methods` + `planning_hints` + `answer_outline`，走 `assemble(PLANNER_POLICY)` | `planner_agent.py` | ✅ |
| PathEvaluatorAgent — 输入改为 `target_skills` + `target_methods` + `alignment_constraints`，走 `assemble(PATH_EVALUATOR_POLICY)` | `path_evaluator_agent.py` | ✅ |
| GraderAgent — `intended_methods` / `common_mistakes` 改为 top-k，走 `assemble(GRADER_POLICY)` | `grader_agent.py` | ✅ |
| SocraticAgent — 已健康，保持"只看最近 6 条历史"模式 | `socratic_agent.py` | ✅ 无需改动 |
| TutorContextBuilder — 输出改为稳定 key-value 格式 | `context_builder.py` | ✅ |

### Phase 3: Review Agent 分治

| 事项 | 文件 | 状态 |
|------|------|------|
| MethodEnumeratorAgent — 删除 `knowledge_hints`，改用 `card_titles` | `method_enumerator_agent.py` + prompt | ✅ |
| MethodSolverAgent — 删除 `knowledge_hints` + `common_mistakes`，改用 `method_guidance`（方法匹配 1-2 条提示 + 1 条易错） | `method_solver_agent.py` + prompt | ✅ |
| ReviewContextBuilder — 改为稳定 key-value 格式，新增 `last_demo_method`/`last_action_type`/`verification.stage`/`verification.method`，`known_methods` 最多 4 个 | `review/context_builder.py` | ✅ |
| compare_methods handler — 最多比较 2 个方法，每个只保留 `key_insight`/`step_count`/`comparison_note`，走 `context_signature` | `review_chat_manager.py` | ✅ |
| explain_concept handler — 最多 2 张卡，每张只保留标题 + 1 条通性通法 + 1 条提示 + 1 条易错，走 `context_signature` | `review_chat_manager.py` | ✅ |

### Phase 4: Memory / Progress / Recommend 分治

| 事项 | 文件 | 状态 |
|------|------|------|
| SemanticMemory 新增 `to_distill_snapshot()` — 画像摘要 + 方法偏好 + 高频错误（截断） | `agent/memory/data_structures.py` | ✅ |
| SemanticMemory 新增 `to_progress_snapshot()` — 弱点 + 错误模式 + 学习统计 | `agent/memory/data_structures.py` | ✅ |
| SemanticMemory 新增 `to_recommend_snapshot()` — 当前题相关弱点优先 + 偏好 | `agent/memory/data_structures.py` | ✅ |
| MemoryDistillerAgent — `to_context_string()` → `to_distill_snapshot(target_tags)` + `assemble(MEMORY_DISTILL_POLICY)` | `memory_distiller_agent.py` | ✅ |
| ProgressSummaryAgent — `to_context_string()` → `to_progress_snapshot()` + mastery 限 top5弱+top3进步 + episodes 限 5 + `assemble(PROGRESS_POLICY)` | `progress_summary_agent.py` | ✅ |
| TaskPlannerAgent — `to_context_string()` → `to_progress_snapshot()` + mastery 限 top5弱+top3高错 + decay 限 top5 + episodes 限 5 + `assemble(PROGRESS_POLICY)` | `task_planner_agent.py` | ✅ |
| RecommendAgent — `to_context_string()` → `to_recommend_snapshot(current_tags)` + weak_concepts 限 4 + `assemble(RECOMMEND_POLICY)` | `recommend_agent.py` | ✅ |

### Phase 5: Projection Registry + Prompt 精简

| 事项 | 文件 | 状态 |
|------|------|------|
| `projection_registry.py` — `ProjectionSpec`/`FieldSpec`/`DegradationStrategy`，7 类 Agent projection 协议 + `validate()` 校验 | `agent/context_governance/projection_registry.py` | ✅ |
| `__init__.py` 导出 `ProjectionSpec`/`FieldSpec`/`DegradationStrategy`/`get_projection`/`list_projections` | `agent/context_governance/__init__.py` | ✅ |
| `task_planner_agent.yaml` — system prompt 压缩为 4 条规则 + JSON-only；plan template task_type 改为单行枚举 | `agent/progress/prompts/zh/task_planner_agent.yaml` | ✅ |
| `recommend_agent.yaml` — system prompt 压缩为 3 条原则 + JSON-only；decide 决策参考改为单行紧凑格式 | `agent/recommend/prompts/zh/recommend_agent.yaml` | ✅ |
| `planner_agent.yaml` / `path_evaluator_agent.yaml` — 已满足精简标准 | — | ✅ 无需改动 |

---

## 未完成

### Phase 0: 快赢项（对话历史截取与压缩）

| 事项 | 文件 | 状态 |
|------|------|------|
| history content 截断 | `socratic_agent.py`, `intent_classifier_agent.py` | ✅ `_format_history` / `_fmt_rich_history` 中 content 截取前 150 字 |
| 调用方侧统一截取 + `_get_recent_history` | `tutor_manager.py`, `action_classifier.py` | ✅ TutorManager `_get_recent_history()` 统一截取 `[-6:]` + 150 字；ActionClassifier 同步 |
| passed_history 精简 | `tutor_manager.py` | ✅ `_build_passed_history` 改为紧凑格式 `已通过: 1.xxx 2.xxx` |
| 请求级上下文缓存 | `tutor_manager.py` | ✅ `_req_recent_history` / `_req_passed_history` / `_req_recent_context` 同一请求只构建一次 |
| PathEvaluator 结果缓存 | `tutor_manager.py` | ✅ `_eval_approach_cached` + `session._path_eval_cache`，同一方法不重复评估 |

### Phase 2 遗留

| 事项 | 文件 | 设计文档章节 | 说明 |
|------|------|------------|------|
| GraderAgent — `student_work` 长文本裁剪（保留开头 + 答案 + 推导段） | `grader_agent.py` | 2.3 | ✅ `_trim_student_work()` 首尾+关键段裁剪，默认 800 字 |

### RAG v2：二阶段知识卡检索（替代原 Phase 5 方案）

> 原 Phase 5 的 `Grader → knowledge_point_tags → embedding` 方案已被二阶段方案取代。详见 `doc/rag_card/plan.md`。

| 事项 | 文件 | 状态 |
|------|------|------|
| `agent/knowledge/` 子系统骨架 | `data_structures / method_catalog / card_store / card_index / card_retriever` | ✅ |
| `MethodRouterAgent` + `CardSelectorAgent` | `agent/knowledge/agents/` | ✅ |
| `JsonParseMixin` 共享解析 | `agent/knowledge/agents/_parsing.py` | ✅ |
| `RagAuditEntry` 审计收集 | `card_retriever.py` + `data_structures.py` | ✅ |
| `build_card_retriever()` 组装工厂 | `agent/knowledge/factory.py` | ✅ |
| SkillRegistry 注册 `retrieve_cards` | `agent/tutor/skills/registry.py` | ✅ |
| TutorManager 接入（ACCEPT/ACCEPT_WITH_FLAG 触发 RAG） | `tutor_manager.py` | ✅ |
| Planner 消费 `supplementary_cards` | `planner_agent.py` + prompt | ✅ |
| 方法目录示例（椭圆 5 slot + 跨章节 4 slot） | `content/method_catalog/` | ✅ |
| Review `explain_concept` 接入 CardRetriever | `review_chat_manager.py` | ✅ |
| Recommend 接入 CardRetriever | `recommend_manager.py` | ✅ |
| Memory 记录 `method_slot` | `memory/data_structures.py` + `tutor_manager.py` + `memory_manager.py` | ✅ |
| 实际知识卡内容填充 + 端到端验证 | `content/knowledge_cards/` + `card_store.py` (FileCardStore) | ✅ 椭圆 7 张卡 |

---

## 实施顺序与进度

| Step | 内容 | 状态 |
|------|------|------|
| Step 1 | Context Governance Layer 骨架（budget_policy / assembler / signature / telemetry） | ✅ |
| Step 2 | ProblemContext 预算化 helper + slice hint 分离 | ✅ |
| Step 3 | Tutor/Review Agent 输入投影（Planner / PathEval / Grader / MethodEnum / MethodSolver / TutorContextBuilder） | ✅ |
| Step 4 | Review compare/explain handler 上下文裁剪 + ReviewContextBuilder 结构化 | ✅ |
| Step 5 | SemanticMemory 投影接口 + MemoryDistiller / ProgressSummary / TaskPlanner / Recommend | ✅ |
| Step 6 | projection_registry 统一定义 + prompt 精简 | ✅ |
| **Phase 0** | **快赢项：content 截断 / 统一截取 / passed_history / 请求缓存** | **✅ 2026-03-24** |
| **RAG B/C** | **knowledge 子系统骨架 + 方法目录 + CardStore** | **✅ 骨架完成** |
| **RAG F** | **MethodRouter + CardSelector + Planner 接入** | **✅ Planner 接入完成** |
| **RAG F 剩余** | **Review / Recommend / Memory 接入 + 知识卡内容 + FileCardStore** | **✅ 2026-03-24** |
| **RAG D/E** | **concept 壳 + rag_audit_task 持久化 + 双层 mastery** | **✅ 2026-03-24** |
| **RAG G/H** | **完整 concept registry + 运营闭环** | **🔲 未开始** |
