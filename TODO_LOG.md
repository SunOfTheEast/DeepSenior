# DeepTutor 待办日志

更新时间：2026-03-20

## P0（高优先）

- [x] `ProblemContext.from_dict`：补齐统一题目结构（`question_id/stem/answer_schema/solutions`）到 Tutor 运行时结构的兼容适配。（2026-03-20 已改代码）
  - 已实现：
    - 自动识别 unified schema 并转换为 legacy `ProblemContext`
    - 将 `slice.hint_pack` 合并进关联知识卡提示，保留现有 Hint 工作流
    - answer_schema -> `answer` 兼容提取（text/reference_answer/correct/accepted）
  - 位置：`agent/tutor/data_structures.py`

- [x] `TutorManager.handle_submission`：修复"`is_correct=True` 时提前返回，导致替代方法未进入 PathEvaluator"的业务缺陷。（2026-03-20 已改代码，待端到端联调）
  - 影响：可能把"绕过目标知识点"的正确答案当作完全掌握，造成记忆画像偏乐观。
  - 位置：`agent/tutor/tutor_manager.py`

- [x] `MemoryManager._apply_update`：升级为"question -> solution 分叉记忆"逻辑（2026-03-20 已落地代码，并补齐 solution/progress 桥接）。
  - 已实现：
    - `Tutor export` 增加 `solution_id/solution_method/solution_tags` 索引
    - `alternative_flagged=true` 时，掌握度只更新 solution-level 知识点
    - 首次替代方法 / 缺失 solution 索引时，自动写入 `pending_audit_tasks`
    - `solution_mastery` 实体更新（level/use_count/linked_concepts/last_outcome）
    - `solution -> concept` bridge：当 distiller 未覆盖相关 concept 时，回写 `concept_mastery`，保证 Progress/Recommend 可直接消费
  - 位置：`agent/tutor/tutor_manager.py`、`agent/memory/memory_manager.py`、`agent/memory/data_structures.py`

- [ ] 后台 RAG Worker：消费 `pending_audit_tasks`，自动补齐 solution 知识卡片索引并生成审计单（学生/教师）。
  - 影响：当前仅完成"任务落盘"，尚未实现自动检索与审批流编排。
  - 位置：`agent/memory/*`（新增 worker 入口待实现）

## P1（中优先）

- [x] `TutorManager.stream_student_message`：与非流式分支对齐替代解法评估策略。（2026-03-20 已修复）
  - 修复：流式路径在 checkpoint 通过且检测到替代方法时，现在会完整调用 `_eval_approach` + `_handle_alternative_path`，与非流式路径行为一致。
  - 位置：`agent/tutor/tutor_manager.py`

- [x] `ReviewChatManager._handle_replay_errors`：修复 `target_method` 默认选择逻辑。（2026-03-20 已修复）
  - 修复：当所有枚举方法都等于学生方法时，回退到第一个可用方法而非 None。
  - 位置：`agent/review/review_chat_manager.py`

- [x] `RecommendAgent._extract_outcome`：优先读取规范化 `outcome`，避免把 `gave_up` 误判为其他状态。（2026-03-20 已修复）
  - 修复：同时检查 `outcome` 和 `status` 字段，显式处理 `gave_up`/`abandoned` 映射。
  - 位置：`agent/recommend/agents/recommend_agent.py`

- [x] `ProgressManager.get_summary`：使 `period=week/month` 真正按时间窗过滤。（2026-03-20 已修复）
  - 修复：新增 `_filter_episodes_by_period`，week→7天、month→30天 cutoff，all_time 不过滤。
  - 位置：`agent/progress/progress_manager.py`

- [ ] `MemoryManager`：实现 `pending -> ready` 的 deferred concept delta replay（延迟回放增量）。
  - 影响：当前 `index_status=pending` 时仅更新 solution mastery，不会把历史增量补写回 concept mastery。
  - 位置：`agent/memory/memory_manager.py`（新增 deferred_delta 队列与回放逻辑）

## P2（中低优先）

- [x] `MemoryManager.commit_session`：增加 `student_id` 与 `episode.student_id` 一致性校验。（2026-03-20 已修复）
  - 修复：参数不一致时抛出 `ValueError`，防止跨学生数据污染。
  - 位置：`agent/memory/memory_manager.py`

- [x] `MemoryStore.episodic_count`：排除索引文件 `_index.json` 的计数。（2026-03-20 已修复）
  - 修复：glob 结果中过滤掉 `_EPISODIC_INDEX_FILENAME`，消除多计 1。
  - 位置：`agent/memory/memory_store.py`

- [x] `TutorManager._build_passed_history`：修复 checkpoint 过滤条件。（2026-03-20 已修复）
  - 修复：改用列表枚举索引 `enumerate` 而非 `cp.index` 与 `current_checkpoint` 比较，避免 Planner 生成的 index 语义不一致导致过滤错误。
  - 位置：`agent/tutor/tutor_manager.py`

## 待观察（教学边缘情况，暂不改代码）

- [ ] 学生连续提交空白内容时无限循环 LLM 调用。建议加连续空白上限后给出不同引导。
  - 位置：`agent/tutor/tutor_manager.py` `_handle_no_attempt` / `handle_submission`

- [ ] 深问（deep dive）子流程与 checkpoint 进度竞态：plan 在深问期间被重建时 `deep_dive_return_checkpoint` 可能越界。
  - 位置：`agent/tutor/tutor_manager.py`

- [ ] Review 验证中断时学生有价值的回答内容被完全丢弃。建议缓存后作为下一轮路由输入。
  - 位置：`agent/review/review_chat_manager.py` `_wants_to_interrupt_verification`

- [ ] `knowledge_cards` 为空时 Grader/Planner prompt 中出现空白占位，LLM 推理质量下降。
  - 位置：`agent/tutor/data_structures.py` `from_unified_question`

- [ ] Ebbinghaus `level=0` 死角：知识点永远卡在 urgent 状态。建议设 level 下限或特殊处理。
  - 位置：`agent/progress/ebbinghaus.py`

- [ ] Pickle 序列化版本兼容性：数据结构变更后旧 snapshot 反序列化失败，用户丢失进行中会话。
  - 位置：`agent/tutor/tutor_manager.py`、`agent/review/review_chat_manager.py`

- [ ] 多问小题（sub_plans / active_sub_problem）功能空挂，TutorManager 中无使用逻辑。
  - 位置：`agent/tutor/data_structures.py`、`agent/tutor/tutor_manager.py`
