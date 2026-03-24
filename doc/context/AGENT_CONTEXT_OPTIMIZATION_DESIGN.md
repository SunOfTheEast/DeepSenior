# Agent Context Optimization Design

关联文档：
- [`RAG_PLANNING_3.md`](../rag_card/RAG_PLANNING_3.md) — 系统演进总纲（全局依赖图与里程碑）
- [`DATA_MODEL_CONVERGENCE.md`](../rag_card/DATA_MODEL_CONVERGENCE.md) — 数据模型收敛设计
- [`MASTERY_TRACKING_DESIGN.md`](../rag_card/MASTERY_TRACKING_DESIGN.md) — 熟练度追踪设计（Phase E 快照接口供本文档 Phase 4 消费）
- [`RAG_RETRIEVAL_DESIGN.md`](../rag_card/RAG_RETRIEVAL_DESIGN.md) — RAG 检索设计（Phase F 上线后 Review explain_concept 可改用 CardRetriever）

本文档聚焦**上下文优化**：怎么让 prompt 更精简。对应总纲 Phase 0、A 和 per-agent Phase 2-4。

## 背景

当前系统中，多数 Agent 的问题不是“单次历史过长”，而是“多个 Agent 重复消费不分层的公共上下文”。这会带来两类成本：

1. Token 成本上升
2. 注意力被低价值细节稀释，影响分类、规划、评估质量

这份文档给出一套可执行的上下文瘦身设计稿，目标是让 Claude Code 直接按此方案落地。

## 目标

1. 降低 Tutor / Review / Memory / Progress / Recommend 主链路的平均上下文长度
2. 提高决策型 Agent 的注意力集中度
3. 避免“同一事实被多种格式重复喂给模型”
4. 不改变核心业务行为，不做策略层大改

## 非目标

1. 不重写业务状态机
2. 不替换现有 Agent 抽象
3. 不先动模型参数或统一砍 `max_tokens`
4. 不改变用户可见交互文案风格

## 关键原则

### 1. 按任务投影上下文，不再共享“大摘要”

不同 Agent 只拿自己完成当前任务所需的最小信息集。

- 决策型 Agent：拿压缩后的结构化事实
- 生成型 Agent：拿当前任务相关的局部细节
- 分类型 Agent：拿短历史 + 稳定状态快照

### 2. 禁止同层信息重复输入

一个 prompt 中，不要同时出现：

- `profile_summary` + `to_context_string()` + `concept_mastery_summary`
- `full hints` + `full common mistakes` + `method summary`
- `session_context` + 冗长自然语言历史回顾

### 3. 公共聚合函数必须带预算

所有面向 LLM 的聚合 helper，都必须显式支持：

- `max_cards`
- `max_items_per_card`
- `max_total_items`
- `max_chars`
- `include_slice_hints`

### 4. 结构化快照优先于自然语言摘要

供分类器和规划器使用的 session/context snapshot，优先采用稳定的 key-value 文本或 JSON-like 文本，而不是自由叙述。

### 5. 决策与教学分层

用于“判断下一步做什么”的 Agent，不应拿到大量“怎么教”的原材料。

例如：

- Planner 不需要全量 hint pack
- PathEvaluator 不需要所有易错点全文
- Recommend 不需要完整长期画像全文

## 当前热点问题

> 以下 4 项在 Phase 1-4.5 中已全部解决。保留原始问题描述供回溯。

### 1. ~~ProblemContext 聚合函数无预算~~ ✅ Phase 1 已解决

**解决方案**：新增 `get_methods_for_llm()` / `get_hints_for_llm()` / `get_common_mistakes_for_llm()` / `get_card_titles_for_llm()` 四个预算化 helper。slice hint 分离到 `solution_slice_hints_by_card`，不再默认流入所有 Agent。旧 helper 保留兼容，新代码一律使用预算版。

### 2. ~~Planner / PathEvaluator 是最重的决策型 Agent~~ ✅ Phase 2 已解决

**解决方案**：Planner 输入改为 `selected_methods` top-3 + `planning_hints`（无 slice）+ `answer_outline` 截断，走 `assemble(PLANNER_POLICY)`。PathEvaluator 输入改为 `target_skills` + `alignment_constraints`，走 `assemble(PATH_EVALUATOR_POLICY)`。Grader 的 `intended_methods` / `common_mistakes` 改为 top-k + `assemble(GRADER_POLICY)`。

### 3. ~~SemanticMemory 消费存在重复表达~~ ✅ Phase 4 已解决

**解决方案**：新增三个任务快照方法（`to_distill_snapshot` / `to_progress_snapshot` / `to_recommend_snapshot`）替代 `to_context_string()` 全量输出。MemoryDistiller / ProgressSummary / TaskPlanner / Recommend 全部接入 assembler + 任务快照。

### 4. ~~Review handler 侧局部上下文膨胀~~ ✅ Phase 3 已解决

**解决方案**：`compare_methods` 限 2 方法 + `key_insight`/`step_count`/`comparison_note` + `context_signature`。`explain_concept` 限 2 卡 + 1 通法/1 提示/1 易错。MethodEnumerator 改用 `card_titles`，MethodSolver 改用 `method_guidance`。

### 5. 对话历史截取与压缩（未解决）

涉及文件：

- `agent/tutor/tutor_manager.py`
- `agent/tutor/agents/socratic_agent.py`
- `agent/tutor/agents/intent_classifier_agent.py`
- `agent/tutor/context_builder.py`

残留问题（对应 SYSTEM_DOCS 5.12.4）：

| 问题 | 现状 | 优先级 |
|---|---|---|
| history content 无长度限制 | `_format_history` / `_fmt_rich_history` 不截断，6 条可达 2000-3000 字 | P1 |
| 调用方侧截取不统一 | SocraticAgent/ActionClassifier 在 Agent 内部隐式 `[-6:]`，RouterAgent 在 Manager 侧显式截取 | P1 |
| passed_history 线性增长 | `_build_passed_history` 输出全部已通过 checkpoint 完整描述 | P2 |
| 无摘要压缩机制 | 纯硬截断，早期对话直接丢弃 | P2 |
| 请求级上下文缓存 | `_build_recent_context` 同一请求被多次调用 | P1 |

这些是 Phase 0 快赢项的目标。

### 6. 替代解法场景上下文给错（未解决）

详见本文档末尾”替代解法场景的知识卡检索”章节。这是 Phase 5 CardRetriever 的目标。

## 目标架构

引入一层“Agent 专用 Context Projection”，替代当前的公共大摘要模式。

### 新增概念

建议新增以下 helper 或 dataclass，不要求一开始全部 dataclass 化，但输出协议必须稳定。

- `PlanningContextProjection`
- `PathEvalContextProjection`
- `GradingContextProjection`
- `ReviewExplainContextProjection`
- `MemoryDistillContextProjection`
- `ProgressPlanningProjection`
- `RecommendationProjection`

每个 projection 只服务一个 Agent 或一类近似任务。

### 新增模块：Context Governance Layer

为了避免上下文逻辑继续散落在各个 Agent、helper 和 prompt 调用点中，建议新增一层**轻量级上下文治理模块**，统一负责：

1. projection 契约
2. 整 prompt 预算仲裁
3. degraded mode / coverage 状态暴露
4. 上下文签名与缓存 key
5. 上下文长度与降级行为的观测埋点

这层的目标不是引入复杂框架，而是把“上下文怎么组装”从隐式细节升级为显式协议。

#### 设计原则

1. **轻量实现优先**
   - 不要求一开始就为每个 Agent 建立重量级 dataclass 树
   - 第一阶段可以用少量 dataclass + 函数完成
   - 先统一协议，再决定是否抽象成更强类型系统

2. **集中定义预算，不在各处写死**
   - `max_hints=4`、`max_cards=2`、`recent_episodes=5` 这类值应集中维护
   - 单个 helper 可以裁剪，但最终是否保留应由整 prompt 预算仲裁器决定

3. **上下文不完整时必须显式暴露**
   - 不能默默跳过映射缺失、alias 冲突、concept coverage 不足
   - projection 输出应带 `coverage_status` / `degraded_mode` / `projection_warnings`

4. **可观测优先于“看起来精简”**
   - 每次组装上下文时，至少要知道：
     - 原始候选项数量
     - 裁剪后数量
     - 被截断的原因
     - 当前 prompt 是否 degraded

#### 建议输出协议

建议新增一个统一返回结构，供各 Agent 的 projection builder 复用：

```python
@dataclass
class ContextAssemblyResult:
    payload: dict[str, Any]              # 最终喂给 prompt 的字段
    token_estimate: int                  # 粗略估计即可
    coverage_status: str                 # full | partial | degraded
    degraded_mode: bool
    warnings: list[str]                  # 例如 concept_link_missing / alias_ambiguous
    dropped_fields: list[str]            # 预算淘汰掉的字段
    context_signature: str               # 缓存 / 诊断使用
```

#### 建议模块职责拆分

不要求一步到位，但建议至少有以下职责边界：

- `projection_registry.py`
  - 定义每类 Agent 的 projection 协议
  - 明确字段名、可空语义、默认降级策略

- `budget_policy.py`
  - 统一维护预算常量和优先级规则
  - 决定当整 prompt 超预算时谁先被裁掉

- `assembler.py`
  - 负责把 ProblemContext / SemanticMemory / RAG 检索结果组装成最终 payload
  - 输出 `ContextAssemblyResult`

- `telemetry.py`
  - 记录长度、裁剪、degraded mode、覆盖率告警

- `signature.py`
  - 基于任务类型、question_id、active_solution_id、target_cards、focus concept 等生成上下文签名

#### 不要求现在就做的事

- 不要求一开始建立 7 个独立 projection 类并到处透传
- 不要求引入独立 DSL
- 不要求做严格 token 精算器

先把**协议、预算、观测、降级**收口到一个模块层，就已经能显著降低后续维护成本。

## Phase 1: 先收口公共聚合函数

### 1.1 改造 ProblemContext

在 `agent/tutor/data_structures.py` 中新增预算化 helper，保留旧 helper 一段时间做兼容，但新代码一律改用预算版。

建议新增方法：

```python
def get_methods_for_llm(
    self,
    max_methods: int = 4,
    max_chars: int = 120,
) -> list[str]: ...

def get_hints_for_llm(
    self,
    *,
    max_cards: int = 2,
    max_items_per_card: int = 2,
    max_total_items: int = 4,
    max_chars: int = 240,
    include_slice_hints: bool = False,
) -> list[str]: ...

def get_common_mistakes_for_llm(
    self,
    *,
    max_cards: int = 2,
    max_items_per_card: int = 2,
    max_total_items: int = 4,
    max_chars: int = 240,
) -> list[str]: ...

def get_card_titles_for_llm(self, max_cards: int = 3) -> list[str]: ...
```

### 1.2 slice hint 不再默认流入所有 Agent

当前 `_collect_slice_hints_by_card()` 的结果会合入 `KnowledgeCard.hints`。这会污染下游所有场景。

建议改法：

1. `KnowledgeCard.hints` 保持“概念层提示”
2. slice hint 单独保留为运行时字段，例如：
   - `ProblemContext.solution_slice_hints_by_card`
3. 只有真正需要教学细节的 Agent 才显式请求 slice hint

### 1.3 预算策略默认值

建议作为第一版默认值：

- `methods`: 最多 4 条
- `hints`: 最多 4 条，总长不超过 240 字
- `common_mistakes`: 最多 4 条，总长不超过 240 字
- `card_titles`: 最多 3 条

### 1.4 局部预算升级为整 prompt 预算

当前的预算设计主要作用在单个 helper 上，例如：

- `get_hints_for_llm()`
- `get_common_mistakes_for_llm()`
- `get_card_titles_for_llm()`

但在真实调用中，一个 Agent prompt 往往会同时包含：

- `target_cards`
- `supplementary_cards`
- `answer_outline`
- `error_description`
- `recent_history`
- `student_work_excerpt`

如果只在局部 helper 层做裁剪，RAG 上线后仍可能出现“每个字段都不过量，但加在一起超预算”的问题。

因此建议在治理层中新增**整 prompt 预算仲裁**：

1. 先按字段组装完整候选 payload
2. 再按统一优先级做裁剪，而不是各字段各自决定

建议优先级：

1. 题目与当前任务锚点
   - `problem`
   - `error_description`
   - `student_approach`
   - `target_cards`
2. 当前决策必须依赖的信息
   - `target_skills`
   - `selected_methods`
   - `answer_outline`
3. 补充参考信息
   - `supplementary_cards`
   - `alignment_constraints`
   - `common_mistakes`
4. 可牺牲信息
   - 长历史
   - 低相关 recent episodes
   - 低权重 supplementary cards

如果进入预算压缩，应在 `ContextAssemblyResult` 中显式写出：

- `dropped_fields`
- `warnings`
- `coverage_status=partial|degraded`

## Phase 2: Tutor Agent 分治

### 2.1 PlannerAgent

涉及文件：

- `agent/tutor/agents/planner_agent.py`
- `agent/tutor/prompts/zh/planner_agent.yaml`

#### 当前输入

- `problem`
- `answer`
- `intended_methods`
- `hints`
- `error_description`
- `start_from`
- `granularity`
- `alternative_method`

#### 问题

- 输入层级过多
- `hints` 含大量教学细节
- prompt system 说明偏长

#### 目标输入

Planner 只保留：

- `problem`
- `answer` 或更推荐 `answer_outline`
- `selected_methods`
- `planning_hints`
- `error_description`
- `start_from`
- `granularity`
- `alternative_method`

#### 具体要求

1. `selected_methods`
   - 最多 2 到 3 条
   - 优先保留标准主方法
   - 如果存在替代方法，则只保留与替代路径相关的方法

2. `planning_hints`
   - 最多 2 到 3 条
   - 不包含 slice `l2/l3` 级教学提示
   - 只保留 outcome 级提示

3. `answer`
   - 若标准答案很长，新增 `answer_outline`
   - 只保留关键中间目标和最终结果，不保留完整推导

4. prompt 改短
   - system prompt 保留 4 到 6 条硬规则
   - 去掉长篇“为什么这样设计”的解释

5. 输出最小化
   - 如果 `approach_summary`、`reset_reason`、`prerequisite_tags` 不被核心链路依赖，改为可选
   - checkpoint 只保留执行所需字段

#### 目标效果

Planner 应该成为“高信号决策器”，而不是“拿着完整教师资料再写一份教学设计书”。

#### 新增约束：RAG 上线后的整 prompt 预算

当 Phase F 上线后，Planner 会同时拿：

- `target_cards`
- `supplementary_cards`
- `selected_methods`
- `planning_hints`
- `answer_outline`
- `error_description`

因此必须在治理层定义 Planner 的整 prompt 优先级：

1. `problem`
2. `error_description`
3. `target_cards`
4. `selected_methods`
5. `planning_hints`
6. `supplementary_cards`
7. `answer_outline`

裁剪规则建议：

- 先缩 `supplementary_cards`
- 再缩 `planning_hints`
- 最后才缩 `target_cards`

目标是保证：RAG 上线后 Planner 拿到的是“正确且预算内”的上下文，而不是“正确但再次过长”的上下文。

### 2.2 PathEvaluatorAgent

涉及文件：

- `agent/tutor/agents/path_evaluator_agent.py`
- `agent/tutor/prompts/zh/path_evaluator_agent.yaml`

#### 当前输入

- `problem`
- `answer`
- `knowledge_card_titles`
- `intended_methods`
- `hints`
- `common_mistakes`
- `student_approach`
- `student_work_excerpt`

#### 问题

- 对“替代解法能不能教”这个判断来说，`hints` 和 `common_mistakes` 经常过量
- 它真正需要的是目标技能，而不是全部教学素材

#### 目标输入

- `problem`
- `answer_outline`
- `target_skills`
- `target_methods`
- `alignment_constraints`
- `student_approach`
- `student_work_excerpt`

#### 具体要求

1. 用 `target_skills` 代替冗长 hints
   - 来源：知识卡标题 + 每张卡 1 条核心技能描述

2. 用 `alignment_constraints` 代替全量易错点
   - 只保留“哪些核心训练不能被绕过”

3. `student_work_excerpt`
   - 上限截断
   - 只保留与方法判断相关的片段

4. prompt 改写
   - 让模型先判断“数学可行性”
   - 再判断“是否绕过目标技能”
   - 不要把示例和解释写太多

### 2.3 GraderAgent

涉及文件：

- `agent/tutor/agents/grader_agent.py`

#### 判断

Grader 目前不算最差，但仍有改进空间。

#### 可做优化

1. `common_mistakes` 改为 top-k
2. `intended_methods` 改为 top-k
3. 对很长 `student_work` 做裁剪
   - 保留开头
   - 保留最后答案
   - 保留明显计算/推导段

#### 优先级

低于 Planner 和 PathEvaluator。

### 2.4 SocraticAgent

涉及文件：

- `agent/tutor/agents/socratic_agent.py`

#### 判断

这是当前上下文消费最健康的 Tutor Agent。

#### 处理建议

1. 保持“只看最近 6 条历史”的模式
2. 不要额外接入长期画像或全计划摘要
3. 如要再优化，只做历史内容裁剪，不改整体结构

### 2.5 TutorActionClassifierAgent / TutorContextBuilder

涉及文件：

- `agent/tutor/action_classifier.py`
- `agent/tutor/agents/intent_classifier_agent.py`
- `agent/tutor/context_builder.py`

#### 判断

这部分不是 token 最大头，但建议顺手结构化。

#### 改造要求

1. `TutorContextBuilder.build()` 输出改成稳定 key-value 结构，例如：

```text
mode=socratic
deep_dive=inactive
checkpoint.current=2
checkpoint.total=5
checkpoint.attempts=1
checkpoint.hint_level=2
checkpoint.passed=1,2
last_error_type=wrong_path_minor
alternative_method=导数法
alternative_flagged=false
```

2. 不要混入长描述性自然语言
3. 已通过步骤只保留编号，不带 description

#### 治理层要求

`TutorContextBuilder` 的输出建议纳入统一 projection 契约：

- 字段名固定
- 字段顺序固定
- 缺失字段有明确默认值
- 结构变化时提升协议版本

这样可以避免 ActionClassifier 在多次重构后因为字段漂移而出现“行为没坏，但分类边界慢慢漂移”的隐性回归。

## Phase 3: Review Agent 分治

### 3.1 Review classify_intent

涉及文件：

- `agent/review/agents/review_chat_agent.py`
- `agent/review/context_builder.py`

#### 判断

它本身已经较轻，保持轻量化即可。

#### 改造要求

1. `ReviewContextBuilder` 改为结构化快照
2. 增加以下字段：
   - `active_method`
   - `last_demo_method`
   - `last_action_type`
   - `pending_verification_stage`
3. `known_methods` 最多保留 4 个
4. 继续只传最近 4 条历史

### 3.2 MethodEnumeratorAgent

涉及文件：

- `agent/review/agents/method_enumerator_agent.py`

#### 问题

当前把 `knowledge_hints` 全量塞进去，容易把“枚举有哪些方法”变成“参考教师提示推测还有什么方法”。

#### 目标输入

- `problem`
- `answer_outline`
- `standard_methods`
- `card_titles`

#### 改造要求

1. 删除 `knowledge_hints`
2. 只保留知识卡标题和标准方法
3. 如果需要提示范围，增加简短 `method_constraints`

### 3.3 MethodSolverAgent

涉及文件：

- `agent/review/agents/method_solver_agent.py`

#### 问题

当前吃 `knowledge_hints + common_mistakes`，很容易过量。

#### 目标输入

- `problem`
- `answer_outline`
- `method_name`
- `method_specific_guidance`

#### 改造要求

1. 默认不传全量 common mistakes
2. `method_specific_guidance` 只取与该方法相关的 1 到 2 条提示
3. 如果 method 不属于标准方法，可不传知识卡 hints

### 3.4 Review handler 的临时上下文

涉及文件：

- `agent/review/review_chat_manager.py`

#### compare_methods

当前会把所有 solved demo 拼进去。

改造要求：

1. 最多比较 2 个方法
2. 每个方法只保留：
   - `key_insight`
   - `step_count`
   - `comparison_note`
3. 不注入完整步骤

#### explain_concept

当前会把所有知识卡完整 hints + mistakes 塞进去。

改造要求：

1. 做概念检索
   - 只取最相关 1 到 2 张卡
2. 每张卡只取：
   - 标题
   - 1 条通性通法
   - 1 条核心提示
   - 1 条典型错误

#### 治理层要求

`compare_methods` 和 `explain_concept` 这类 handler 分支，建议统一走治理层的 `context_signature`：

- `compare_methods` 缓存 key 建议包含：
  - `task=compare_methods`
  - `question_id`
  - `method_pair`
  - `student_method_used`

- `explain_concept` 缓存 key 建议包含：
  - `task=explain_concept`
  - `question_id`
  - `active_solution_id`
  - `focus_concept`
  - `target_cards_hash`

不要只用“同一 session”做缓存边界，否则很容易把旧的 supplementary cards 复用到新的问题焦点上。

## Phase 4: Memory / Progress / Recommend 分治

### 4.1 SemanticMemory 不再提供单一“大而全”上下文入口

涉及文件：

- `agent/memory/data_structures.py`

#### 当前问题

`to_context_string()` 已经混合了：

- 画像摘要
- 近期重点
- 擅长方法
- 回避方法
- 薄弱知识点
- 待巩固解法
- 待审计任务
- 常见错误

这对不同 Agent 来说太杂。

#### 改造要求

保留 `to_context_string()` 兼容旧代码，但新增任务定制接口：

```python
def to_distill_snapshot(self, tags: list[str], max_methods: int = 3) -> str: ...
def to_progress_snapshot(self, max_weak: int = 5, max_errors: int = 3) -> str: ...
def to_recommend_snapshot(self, current_tags: list[str], max_weak: int = 4) -> str: ...
```

建议同步增加一个轻量状态头，显式暴露当前画像是否完整：

```python
@dataclass
class SnapshotMeta:
    coverage_status: str        # full | partial | degraded
    degraded_mode: bool
    warnings: list[str]
```

原因：

- concept link 缺失
- alias 消歧失败
- RAG 结果为空
- 某层 mastery 未启用

这些都不应只停留在日志里，还应让下游 Agent 知道“这份画像是部分可信”。

### 4.2 MemoryDistillerAgent

涉及文件：

- `agent/memory/agents/memory_distiller_agent.py`

#### 当前问题

它同时拿：

- 一整条 episode
- `current_semantic.to_context_string()`
- `current_mastery_summary`

这已经出现“全局画像 + 局部相关画像”的重复。

#### 改造要求

1. `current_profile` 改为 `to_distill_snapshot(target_tags)`
2. `current_mastery_summary` 保留，但不要再附加全量自由文本画像
3. `episode` 侧只保留与目标 tags / methods 相关的字段

### 4.3 ProgressSummaryAgent

涉及文件：

- `agent/progress/agents/progress_summary_agent.py`

#### 当前问题

同时拿：

- `semantic.to_context_string()`
- `concept_mastery_summary`
- `recent_episodes`
- 统计值

#### 改造要求

1. `long_term_profile` 改成 `to_progress_snapshot()`
2. `concept_mastery_summary` 只保留 top 5 弱项和 top 3 进步项
3. `recent_episodes` 最多 5 条
4. 不要重复表达相同弱点

### 4.4 TaskPlannerAgent

涉及文件：

- `agent/progress/agents/task_planner_agent.py`
- `agent/progress/prompts/zh/task_planner_agent.yaml`

#### 当前问题

这是 Progress 侧最重的 Agent：

- 长期画像
- 掌握情况
- 遗忘列表
- 近期会话
- 近期负荷
- 再加一个比较长的 prompt

#### 改造要求

1. `long_term_profile` 改为结构化 snapshot
2. `concept_mastery` 只保留：
   - top 5 弱项
   - top 3 高频错误关联 concept
3. `decay_due` 只保留 top 5
4. `recent_episodes` 只保留最近 3 到 5 条
5. system prompt 压缩为规则式表达

### 4.5 RecommendAgent

涉及文件：

- `agent/recommend/agents/recommend_agent.py`

#### 当前问题

推荐决策不需要完整长期画像全文，但现在会吃：

- `student_profile`
- `weak_concepts`
- `recent_problems`
- 当前会话统计

#### 改造要求

1. `student_profile` 改为 `to_recommend_snapshot(current_tags)`
2. `weak_concepts` 不再单独传整份列表，如果 snapshot 已覆盖则不重复传
3. `recent_problems` 最多 3 到 5 条
4. 优先突出：
   - 当前题相关弱点
   - 最近重复失败模式
   - retry 是否触发

#### 新增要求：RecommendationProjection 需要带不完整性标记

如果当前画像来自部分 coverage 或降级模式，Recommend 不应像平时一样强依赖长期画像做强结论。

建议：

- `student_profile` snapshot 中带 `coverage_status`
- degraded 时减少长期结论，更多参考当前题和最近 episode
- 避免在画像不完整时给出“你一直在某 concept 上薄弱”这类过强表述

## Prompt 改造规则

### 1. 决策型 Agent 的 system prompt 必须缩短

适用对象：

- `planner_agent`
- `path_evaluator_agent`
- `task_planner_agent`
- `recommend_agent`

要求：

1. 保留核心枚举和规则
2. 删除长篇解释性文字
3. 示例不超过 1 组
4. 避免把“为什么这样设计”每次重发

### 2. prompt 中尽量不要求模型复述背景

例如：

- 不要让模型先解释整体思路再给结构化输出
- 能输出 JSON 就不要再生成多余叙述

## Context Governance Layer（新增实施层）

上文的改造项仍以“按 Agent 分治”为主。为了保证这些改造后续可维护、可观测、可复用，建议补一层统一实现层。

### 目标

1. 不让 projection 逻辑继续散落在 Agent 文件里
2. 不让预算值继续在多个文件里各写一份
3. 不让覆盖率缺失、alias 冲突、RAG 缺卡只体现在日志里
4. 给缓存、观测、回归测试一个统一落点

### 推荐落点

建议新增目录：

```text
agent/context_governance/
  __init__.py
  projection_registry.py
  budget_policy.py
  assembler.py
  telemetry.py
  signature.py
```

### 最小职责

#### 1. `projection_registry.py`

定义每类 projection 的协议：

- 允许哪些字段
- 字段是否可空
- 缺失时怎么降级
- 输出协议版本号

#### 2. `budget_policy.py`

集中维护：

- 默认预算
- 各字段优先级
- 不同 Agent 的整 prompt 裁剪顺序

#### 3. `assembler.py`

负责把：

- `ProblemContext`
- `SemanticMemory`
- `recent_history`
- `RAG supplementary cards`
- `coverage/degraded state`

组装成最终 payload，并输出 `ContextAssemblyResult`。

#### 4. `telemetry.py`

至少记录：

- projection 名称
- 组装前字段大小
- 组装后字段大小
- 是否 degraded
- `dropped_fields`
- `warnings`
- 估算 token

#### 5. `signature.py`

负责生成上下文签名，供：

- 检索缓存
- explain_concept 缓存
- prompt 回归对比
- 线上问题定位

### 这层不应该做什么

- 不直接依赖 LLM
- 不改变业务状态机
- 不在这里做正式索引写回
- 不引入复杂框架式抽象

它更像是“上下文基础设施层”，而不是新业务模块。

## 实施顺序

建议按下面顺序执行，降低回归风险。

### Step 1 ✅

先补 `Context Governance Layer` 的最小骨架：

- `budget_policy.py`
- `assembler.py`
- `signature.py`

目标：

- 先把预算、签名、降级状态收口
- 给后续各 Agent 的 projection 改造一个统一落点

### Step 2 ✅

改 `ProblemContext` 预算化 helper。

已完成：

- `get_methods_for_llm()` / `get_hints_for_llm()` / `get_common_mistakes_for_llm()` / `get_card_titles_for_llm()` 已实现
- slice hint 分离：`_collect_slice_hints_by_card()` 结果不再合入 `KnowledgeCard.hints`，改存 `ProblemContext.solution_slice_hints_by_card`
- `get_hints_for_llm(include_slice_hints=True)` 按需从分离字段拉取

### Step 3 ✅

改 Tutor / Review Agent 的输入投影。

已完成：

- `PlannerAgent`: 使用 `get_methods_for_llm()` + `get_hints_for_llm()` + `assemble(PLANNER_POLICY)`
- `PathEvaluatorAgent`: 使用 `_build_target_skills()` + `get_methods_for_llm()` + `assemble(PATH_EVALUATOR_POLICY)`
- `GraderAgent`: 使用 `get_methods_for_llm()` + `get_common_mistakes_for_llm()` + `assemble(GRADER_POLICY)`
- `MethodEnumeratorAgent`: 删除 `knowledge_hints`，改用 `card_titles`（`get_card_titles_for_llm()`）
- `MethodSolverAgent`: 删除 `knowledge_hints` + `common_mistakes`，改用 `method_guidance`（方法匹配后取 1-2 条提示 + 1 条易错点）
- `TutorContextBuilder`: 改为稳定 key-value 输出格式

### Step 4 ✅

改 Review 的 `compare/explain` handler。

已完成：

- `ReviewContextBuilder` 改为稳定 key-value 格式（mode/verification.stage/known_methods 等），与 TutorContextBuilder 对齐
- `compare_methods` handler：最多比较 2 个方法，每个只保留 `key_insight`/`step_count`/`comparison_note`，走 `context_signature`
- `explain_concept` handler：最多 2 张卡，每张只保留标题 + 1 条通性通法 + 1 条提示 + 1 条易错，走 `context_signature`

### Step 5 ✅

改 `SemanticMemory` 的投影接口，以及 `MemoryDistillerAgent` / `ProgressSummaryAgent` / `TaskPlannerAgent` / `RecommendAgent`。

已完成：

- `SemanticMemory` 新增 `to_distill_snapshot()` / `to_progress_snapshot()` / `to_recommend_snapshot()`，保留 `to_context_string()` 兼容
- `MemoryDistillerAgent`: `to_context_string()` → `to_distill_snapshot(target_tags)` + `assemble(MEMORY_DISTILL_POLICY)`
- `ProgressSummaryAgent`: `to_context_string()` → `to_progress_snapshot()` + mastery 限 top5弱+top3进步 + episodes 限 5 + `assemble(PROGRESS_POLICY)`
- `TaskPlannerAgent`: `to_context_string()` → `to_progress_snapshot()` + mastery 限 top5弱+top3高错 + decay 限 top5 + episodes 限 5 + `assemble(PROGRESS_POLICY)`
- `RecommendAgent`: `to_context_string()` → `to_recommend_snapshot(current_tags)` + weak_concepts 限 4 + `assemble(RECOMMEND_POLICY)`

### Step 6 ✅

`projection_registry` 统一定义 + prompt 精简。

完成内容：

- `agent/context_governance/projection_registry.py`: 定义 `ProjectionSpec` / `FieldSpec` / `DegradationStrategy`，为 7 类 Agent 注册 projection 协议（允许字段、可空语义、降级策略、协议版本号），提供 `validate()` 校验接口
- `__init__.py` 导出 `ProjectionSpec` / `FieldSpec` / `DegradationStrategy` / `get_projection` / `list_projections`
- `task_planner_agent.yaml`: system prompt 从 10 行压缩为 4 条核心规则 + JSON-only 指令；plan template 从 56 行压缩为 ~40 行，task_type 说明从 5 段改为单行枚举
- `recommend_agent.yaml`: system prompt 从 9 行压缩为 3 条核心原则 + JSON-only 指令；decide template 从 48 行压缩为 ~30 行，决策参考从 8 行改为单行紧凑格式
- `planner_agent.yaml` / `path_evaluator_agent.yaml`: 已满足精简标准，无需改动

## 验收标准

### 功能正确性

1. Tutor 主链路行为不变
2. Review 主路由行为不变
3. Memory / Progress / Recommend 的输出字段不变
4. 不引入新状态字段耦合

### 上下文质量

1. Planner / PathEvaluator prompt 的动态输入长度明显下降
2. Progress / Recommend prompt 中不再出现长期画像重复表达
3. Review 的 `compare` / `explain_concept` 上下文长度有上限
4. coverage 缺失 / degraded mode 能显式进入 projection 结果，而不只是写日志

### 工程约束

1. 旧 helper 可保留一段时间，但新代码不得继续调用无预算版本
2. 新增 helper 都需要明确预算参数
3. 预算值不要写死在多个文件里，最好集中定义
4. projection/snapshot 输出协议要有版本号或稳定 contract
5. cache key 应来自 context signature，而不是零散字符串拼接

## 建议测试

至少补以下回归测试或 smoke test。

### Tutor

1. `PlannerAgent` 输入不再包含全量 hints
2. `PathEvaluatorAgent` 输入不再包含全量 common mistakes
3. `SocraticAgent` 仍只消费短历史

### Review

1. `MethodEnumeratorAgent` 不再使用 full hints
2. `compare_methods` 只比较两个方法
3. `explain_concept` 只注入相关卡片

### Memory / Progress / Recommend

1. `to_distill_snapshot()` 只输出相关 tags
2. `TaskPlannerAgent` 不再同时拿长画像全文和长 mastery 明细
3. `RecommendAgent` 不再重复接收弱点列表

### Governance

1. 同一输入生成的 `context_signature` 稳定一致
2. 超预算时 `dropped_fields` 符合优先级策略
3. 缺失 concept link / alias 冲突时，`coverage_status` 与 `warnings` 正确暴露
4. projection 协议变更时，contract test 能发现字段漂移

## 建议代码落点

### 优先新增或修改的文件

- `agent/context_governance/budget_policy.py`
- `agent/context_governance/assembler.py`
- `agent/context_governance/signature.py`
- `agent/context_governance/telemetry.py`
- `agent/tutor/data_structures.py`
- `agent/tutor/agents/planner_agent.py`
- `agent/tutor/agents/path_evaluator_agent.py`
- `agent/review/agents/method_enumerator_agent.py`
- `agent/review/agents/method_solver_agent.py`
- `agent/review/review_chat_manager.py`
- `agent/memory/data_structures.py`
- `agent/memory/agents/memory_distiller_agent.py`
- `agent/progress/agents/progress_summary_agent.py`
- `agent/progress/agents/task_planner_agent.py`
- `agent/recommend/agents/recommend_agent.py`
- `agent/tutor/context_builder.py`
- `agent/review/context_builder.py`

## 给 Claude Code 的执行提示

1. 先做 Phase 1 和 Phase 2，不要一口气改全仓库
2. 每完成一个 Phase，先做最小 smoke test，再继续
3. 如果发现某些输出字段根本没被消费，可以在保留兼容的前提下改成可选
4. 不要先从“砍 `max_tokens`”下手，先做上下文投影
5. 对每个 Agent 的最终 prompt，建议记录一版优化前后的字符长度，便于验证收益

## 设计评审备注（2026-03-23）

### 评审结论

原设计的 Phase 分层和优先级排序方向正确，但存在以下问题需要修正：

#### 1. 缺少快赢项（新增 Phase 0）

SYSTEM_DOCS 5.12.4 已识别的 5 个问题（A-E）中，有若干 10 行代码即可修复的快赢项未纳入本设计文档：

| 问题 | 修改量 | 收益 |
|---|---|---|
| history content 字段无截断（问题 A） | `_format_history` / `_fmt_rich_history` 加 `[:150]` | 减少 30-50% history token |
| `_build_passed_history` 无上限（问题 C） | 只传编号+关键词，不传完整 description | 减少 100-300 字 |
| 隐式截断不统一（问题 B） | TutorManager 侧统一截取后传入 | 消除 Agent 内部隐藏行为 |

建议在 Phase 1 之前新增 Phase 0 专做这些零风险修改。

#### 2. Projection 类设计过重

7 个 Projection 类（`PlanningContextProjection` 等）对当前阶段过度抽象。建议修正为：

1. 先建立轻量 `Context Governance Layer`
2. 用函数 + 少量 dataclass 管理 projection 契约、预算和降级状态
3. 确认多个 Agent 共享同一 projection 协议后，再决定是否抽成更强类型的 Projection 类

也就是说，**需要统一 module，但不需要一开始就类爆炸**。

#### 3. 预算默认值缺乏依据

`max_hints=4, max_chars=240` 等默认值缺少数据支撑。建议实施前先跑基线测量：统计各 Agent prompt 的实际 token 分布（P50/P95），再据此定预算。

#### 4. Phase 5（ContextBuilder）应提前

`ActionClassifier` 是对话阶段每轮都调用的 LLM，频次远高于 Planner/PathEvaluator。`TutorContextBuilder` 的结构化改造虽单次收益小，但总 token 节省量可观（SYSTEM_DOCS 5.12.4 问题 E）。建议提前到 Phase 2 之后。

#### 5. 与 SYSTEM_DOCS 需交叉引用

本文档与 SYSTEM_DOCS 的 5.12（上下文管理）、8.3（架构审计）在问题描述上大量重叠但互不引用，长期会导致文档分叉。需双向链接。

---

## Phase 0: 对话上下文管理具体实现方案（2026-03-23 补充）

Phase 1-4.5 解决了"Agent 输入投影"层面的上下文优化。Phase 0 解决更底层的问题：**对话历史本身的截取、压缩和缓存**。这些问题跨 Agent 通用，改动集中在 `tutor_manager.py` 和两个 Agent 的 `_format_history` 函数中。

### 0.1 统一上下文截取：Manager 侧截取，Agent 不再接收全量 history

#### 现状问题

```
当前调用链（SocraticAgent 为例）：

TutorManager._ask_socratic(session, student_response)
  └─ self._hint(session.problem, checkpoint, session.interaction_history, ...)
       └─ SocraticAgent.process(problem, checkpoint, interaction_history, ...)  # 全量传入
            └─ self._format_history(interaction_history, last_n=6)              # Agent 内部隐式截取
```

问题：
1. TutorManager 不知道 Agent 会取多少条——截取逻辑"藏"在 Agent 里
2. 不同 Agent 各自截取（Socratic [-6:]、ActionClassifier [-6:]、Router 在 Manager 侧 [-6:]），策略不统一
3. 如果要改截取窗口，需要去每个 Agent 里改

#### 目标状态

```
TutorManager 统一截取：

TutorManager._ask_socratic(session, student_response)
  ├─ recent_history = self._get_recent_history(session, max_entries=6)     # Manager 侧统一截取
  └─ self._hint(session.problem, checkpoint, recent_history, ...)
       └─ SocraticAgent.process(problem, checkpoint, recent_history, ...)   # 只收截取后的 history
            └─ self._format_history(recent_history)                         # 不再做二次截取
```

#### 具体改动

**文件 1：`agent/tutor/tutor_manager.py`**

新增统一截取方法：

```python
_HISTORY_WINDOW = 6   # 默认对话窗口大小
_CONTENT_MAX_CHARS = 150  # 每条消息 content 截断上限

@classmethod
def _get_recent_history(
    cls,
    session: TutorSession,
    max_entries: int = _HISTORY_WINDOW,
    max_content_chars: int = _CONTENT_MAX_CHARS,
) -> list[dict[str, Any]]:
    """统一截取最近 N 条对话，并截断每条 content。

    所有需要对话历史的 Agent 调用点都应使用此方法，
    而非直接传 session.interaction_history。
    """
    recent = session.interaction_history[-max_entries:]
    return [
        {
            **entry,
            "content": entry.get("content", "")[:max_content_chars],
        }
        for entry in recent
    ]
```

修改所有调用点（约 5 处）：

| 调用点 | 当前传入 | 改为 |
|---|---|---|
| `_ask_socratic()` → `SocraticAgent.process()` | `session.interaction_history` | `self._get_recent_history(session)` |
| `stream_student_message()` → `self._stream_hint()` | `session.interaction_history` | `self._get_recent_history(session)` |
| `_action_cls.classify()` | `session`（Agent 内部取 history） | `self._get_recent_history(session)` + 改 classify 签名 |
| `_generate_emotional_response()` | `session.interaction_history` | `self._get_recent_history(session)` |
| `DeepDiveHandler._build_response()` | `session.interaction_history` | 由 TutorManager 传入已截取的 history |

**文件 2：`agent/tutor/agents/socratic_agent.py`**

```python
# 修改前
def _format_history(self, history: list[dict[str, Any]], last_n: int = 6) -> str:
    recent = history[-last_n:]  # 隐式截取
    ...

# 修改后
def _format_history(self, history: list[dict[str, Any]]) -> str:
    """格式化对话历史。输入应已由 Manager 侧截取和截断。"""
    label_map = {"student": "学生", "tutor": "老师", "system": "系统"}
    lines = [
        f"[{label_map.get(m['role'], m['role'])}]: {m['content']}"
        for m in history
    ]
    return "\n".join(lines) if lines else "（对话刚开始）"
```

**文件 3：`agent/tutor/agents/intent_classifier_agent.py`**

```python
# 修改前
def _fmt_rich_history(history: list[dict[str, Any]]) -> str:
    ...
    for h in history:
        content = h.get("content", "")  # 无截断

# 修改后（content 已由 Manager 截断，此处不再需要处理）
def _fmt_rich_history(history: list[dict[str, Any]]) -> str:
    ...
    for h in history:
        content = h.get("content", "")  # 已截断，直接使用

# classify_action 签名变更
async def classify_action(
    self,
    problem_context: ProblemContext,
    session_context: str,
    interaction_history: list[dict[str, Any]],  # 改为接收已截取的 history
    student_message: str,
) -> dict[str, Any]:
    ...
    recent_history=self._fmt_rich_history(interaction_history),  # 不再 [-6:]
```

#### 约束

1. `_get_recent_history` 是唯一的截取入口，Agent 内部**禁止**再做 `[-N:]`
2. `_HISTORY_WINDOW` 和 `_CONTENT_MAX_CHARS` 集中定义在 TutorManager 类顶部
3. 截断后的 history 条目保持原始结构（dict），只有 `content` 字段被缩短

### 0.2 passed_history 精简

#### 现状

```python
# tutor_manager.py:869
def _build_passed_history(self, session: TutorSession) -> str:
    ...
    lines = [
        f"Checkpoint {cp.index + 1}: {cp.description}" for cp in passed
    ]
    return "\n".join(lines)
```

当 8 个 checkpoint 做到第 8 步时，输出约 7 × 30 字 = 210 字的完整 description。对于 `evaluate_checkpoint` 来说，已通过步骤的完整描述价值远低于"学生刚才说了什么"。

#### 目标

```python
def _build_passed_history(self, session: TutorSession) -> str:
    if not session.solution_plan:
        return "（尚无引导计划）"
    passed = [
        cp for i, cp in enumerate(session.solution_plan.checkpoints)
        if i < session.current_checkpoint
    ]
    if not passed:
        return "（尚未通过任何 checkpoint）"
    # 只保留编号+关键词（≤10 字），不传完整 description
    items = [
        f"{cp.index + 1}.{cp.description[:10]}" for cp in passed
    ]
    return f"已通过: {', '.join(items)}"
```

效果：`"已通过: 1.展开二次项, 2.移项合并, 3.开方求根"` — 固定约 30-50 字。

### 0.3 请求级上下文缓存

#### 现状

同一次 `_process_message_core` 调用中：

```
_process_message_core(session, student_message)
  ├─ _action_cls.classify(session, ...)       # 内部取 interaction_history[-6:]
  ├─ _eval_checkpoint(
  │     passed_checkpoints_history=self._build_passed_history(session),  # 构建 1 次
  │     interaction_context=self._build_recent_context(session),         # 构建 1 次
  │  )
  └─ _ask_socratic(session, ...)              # 内部又取 interaction_history
```

`_build_recent_context` 和 `_build_passed_history` 每次调用都重新遍历 `interaction_history`。虽然单次开销小，但在统一截取后，可以进一步复用。

#### 目标

引入请求级上下文快照，一次构建多处使用：

```python
@dataclass
class _RequestContext:
    """单次请求内的上下文快照，避免重复构建。"""
    recent_history: list[dict[str, Any]]
    passed_history: str
    recent_context_str: str  # _build_recent_context 的输出

    @classmethod
    def build(cls, manager: "TutorManager", session: TutorSession) -> "_RequestContext":
        recent = manager._get_recent_history(session)
        return cls(
            recent_history=recent,
            passed_history=manager._build_passed_history(session),
            recent_context_str=manager._build_recent_context(session),
        )
```

使用方式：

```python
async def _process_message_core(self, session, student_message):
    ...
    ctx = _RequestContext.build(self, session)

    decision = await self._action_cls.classify(
        session_context=...,
        interaction_history=ctx.recent_history,    # 复用
        ...
    )
    ...
    evaluation = await self._eval_checkpoint(
        passed_checkpoints_history=ctx.passed_history,       # 复用
        interaction_context=ctx.recent_context_str,          # 复用
        ...
    )
    ...
```

#### 约束

1. `_RequestContext` 是纯数据容器，不持有 session 引用
2. 每次 `_process_message_core` / `handle_submission` 入口构建一次，方法内传递
3. 不做跨请求缓存——session 每轮都在变化

### 0.4 PathEvaluator 结果缓存

#### 现状

同一种替代方法可能被评估两次：

1. **提交流**：Grader 返回 `uses_alternative_method=True` → 触发 PathEvaluator
2. **消息流**：Router 返回 `used_alternative_method=True` → 再次触发 PathEvaluator

每次评估花费一次 LLM 调用（~3-5s）。

#### 目标

在 session 上缓存 PathEvaluator 结果：

```python
# TutorSession 新增字段
@dataclass
class TutorSession:
    ...
    _path_eval_cache: dict[str, PathEvaluationResult] = field(
        default_factory=dict, repr=False
    )
```

```python
# TutorManager 中封装缓存查询
async def _eval_approach_cached(
    self,
    session: TutorSession,
    problem_context: ProblemContext,
    student_approach: str,
    student_work_excerpt: str,
) -> PathEvaluationResult:
    """带缓存的 PathEvaluator 调用。同一 session 内同一方法不重复评估。"""
    cache_key = self._normalize_method_key(student_approach)
    if cache_key in session._path_eval_cache:
        self._logger.debug(f"PathEvaluator cache hit: {cache_key}")
        return session._path_eval_cache[cache_key]

    result = await self._eval_approach(
        problem_context=problem_context,
        student_approach=student_approach,
        student_work_excerpt=student_work_excerpt,
    )
    session._path_eval_cache[cache_key] = result
    return result
```

替换 `handle_submission` 和 `_process_message_core` 中的两处 `_eval_approach` 调用为 `_eval_approach_cached`。

#### 约束

1. 缓存粒度是 session 级（同一会话内同一方法名不重评）
2. 缓存 key 走 `_normalize_method_key`（已有），处理"配方法" / "完全平方法" 等别名
3. session 关闭时缓存随 session 一起销毁，无需额外清理

### 0.5 滑动窗口 + 摘要（P2，远期）

当前阶段不实施。记录设计方向供后续参考。

#### 思路

当对话超过 N 轮（如 12 轮）时，将前半部分压缩为一句摘要，保留最近 6 轮原文：

```
[摘要] 学生最初尝试因式分解但在十字相乘步骤卡住，经过 3 轮引导理解了分组分解的思路。
[学生]: 那分组分解之后是不是直接提公因式？
[老师]: 没错，你试试看...
...（最近 6 轮原文）
```

#### 为什么现在不做

1. 需要额外一次 LLM 调用来生成摘要（增加延迟）
2. 当前 `_MAX_INTERACTIONS=200` 的上限已经足够护栏
3. Phase 0.1 的 content 截断 + 窗口统一已经能将 6 条历史从 ~3000 字压到 ~900 字
4. 等 Phase 5 CardRetriever 上线后，prompt 空间更紧张时再引入

### Phase 0 实施顺序

```
0.1  统一截取 + content 截断     ← 最优先，所有后续改动的基础
0.2  passed_history 精简          ← 独立于 0.1，可并行
0.3  请求级上下文缓存             ← 依赖 0.1（统一截取后才有单一数据源可缓存）
0.4  PathEvaluator 结果缓存       ← 独立于 0.1-0.3，可并行
0.5  滑动窗口+摘要                ← 远期，当前不实施
```

预计总改动量：~120 行代码（不含 0.5）。

### Phase 0 验收标准

1. **SocraticAgent / ActionClassifier 不再接收全量 `interaction_history`** — 入参中的 history 长度 ≤ `_HISTORY_WINDOW`
2. **每条 history content ≤ 150 字** — 6 条历史总 content ≤ 900 字（当前可达 3000 字）
3. **passed_history 固定 ≤ 50 字** — 不再随 checkpoint 数量线性增长
4. **同一请求内 `_build_recent_context` / `_build_passed_history` 只调用一次**
5. **同一 session 内同一替代方法 PathEvaluator 只调用一次**
6. **所有原有测试通过** — 行为不变，只是上下文更精简

---

## 替代解法场景的知识卡检索（2026-03-23 补充）

### 问题发现

当前 ProblemContext 只挂载与**标准方法**关联的知识卡。当学生使用替代解法时：

1. PathEvaluator 判定 ACCEPT，Planner 被调用为替代方法重新规划
2. 但 Planner 通过 `get_hints_summary()` 拿到的仍然是标准方法的 hints
3. **Planner 在替代方法场景下拿到的上下文与规划目标错位**

这不是”上下文太长”的问题，是**上下文给错了**。预算化截断无法解决此问题。

且影响不限于 Planner——Memory 在 `commit_session` 时记录的 concept 与实际教学内容也会脱节，进而影响推荐链路。

### 设计方案：两阶段知识卡检索

引入 `CardRetriever` 组件，在 Planner 调用前按需检索相关知识卡作为补充：

```
题目挂载的卡片  = 本题的训练目标（约束，始终存在）
RAG 检索的卡片  = 当前方法的参考素材（补充，按需加载）
```

#### 语义路由：Grader 输出扩展

不新增 LLM 调用。Grader 已经在分析学生的解题方法，让它在输出中多加一个标准化标签字段：

```python
@dataclass
class GraderResult:
    ...
    student_approach: str              # 已有：自然语言方法描述
    knowledge_point_tags: list[str]    # 新增：标准化知识点标签
```

LLM 做方法分析时顺带输出标签，成本几乎为零，但给 RAG 提供高质量的结构化 query。

#### Stage 1：结构化缩范围（1000+ → 30-50）

利用数学知识的天然层级结构做硬过滤：

| 过滤维度 | 来源 | 效果 |
|---|---|---|
| 学科/章节 | `ProblemContext.chapter` + `tags` | 砍掉 80%+ 无关分支 |
| 知识图谱邻域 | 题目挂载卡的 `prerequisite_ids` + 同层/子节点 | 保留知识上相关的卡 |

数学知识体系是一棵有明确层级的树：

```
代数
├── 整式运算
│   ├── 乘法公式（平方差、完全平方）
│   └── 因式分解
│       ├── 提公因式法
│       ├── 十字相乘法
│       └── 分组分解法
├── 方程
│   ├── 一元二次方程
│   │   ├── 求根公式
│   │   ├── 因式分解法
│   │   ├── 配方法
│   │   └── 韦达定理
│   └── ...
```

`KnowledgeCard.prerequisite_ids` 已提供知识图谱的边，Stage 1 可沿这些边做邻域扩展。

#### Stage 2：语义精排（30-50 → 3-5）

在候选集内做 embedding 相似度排序：

- **Card embedding**：`”{title} | 方法: {general_methods} | 场景: {hints[0]}”`
- **Query embedding**：Grader 输出的 `knowledge_point_tags` + `student_approach`

~1000 张卡的规模无需向量数据库，简单的 numpy 数组 + cosine similarity 即可。

#### 数学表达式 → 知识点的映射难度

| 学生操作 | 对应知识点 | 映射难度 | 依赖方式 |
|---|---|---|---|
| `Δ=b²-4ac` | 判别式 / 求根公式 | 低 | 公式本身是标志，embedding 可解 |
| `(x+2)(x+3)=0` → `x=-2 或 -3` | 零积定理 | 中 | 操作和概念名差异大，需 LLM 标签 |
| `两边同时加 (b/2a)²` | 配方法 | 中 | 需理解操作意图，需 LLM 标签 |
| `令 t=x²` | 换元法 | 高 | 太泛，需 LLM 结合上下文判断 |
| `构造辅助线` | 取决于构造方式 | 很高 | 同一操作可对应不同知识点 |

对于”中”和”高”难度的映射，Grader 输出的 `knowledge_point_tags`（LLM 语义路由）比纯 embedding 更可靠。两者结合：tags 精确匹配优先，embedding 兜底。

#### 完整检索流程

```
学生提交解题过程
    ↓
Grader 分析
    ├─ error_type, is_correct, ...（已有）
    └─ knowledge_point_tags: [“配方法”, “完全平方式”]（新增）
    ↓
PathEvaluator 判定 → ACCEPT
    ↓
CardRetriever.retrieve(
    tags=knowledge_point_tags,        # 精确匹配优先
    chapter=problem_context.chapter,  # Stage 1 结构化过滤
    fallback_query=student_approach,  # Stage 2 语义兜底
    top_k=3,
)
    ↓
Planner(
    problem_context,                  # 含挂载卡（训练目标）
    supplementary_cards=retrieved,    # 检索卡（方法参考）
    ...
)
```

#### 知识卡三层标签体系

当前 `KnowledgeCard.tags` 把题型信息和方法信息混在一起，导致替代解法场景下无法精确控制熟练度更新。需拆分为三层：

```
思想层 (thinking_tags)  — 为什么这么做有效    迁移性最强，跨题型跨方法
题型层 (problem_tags)   — 这是什么问题        中等迁移，同类题通用
方法层 (method_tags)    — 具体怎么算          迁移性最弱，适用范围窄
```

**示例：圆锥曲线定值问题**

```
thinking_tags: ["不变量思想", "射影对偶思想"]
problem_tags:  ["圆锥曲线·定值问题"]
method_tags:   ["联立+韦达定理法", "对称式处理"]
```

**各层标注来源与节奏**

| 层级 | 标注者 | 节奏 | 缺省行为 |
|---|---|---|---|
| 方法层 | 出题人 + RAG 自动补充 | 跟随题目上线 | 替代方法走审计流程 |
| 题型层 | 出题人 | 跟随题目上线 | 必填 |
| 思想层 | 教师 + 高级 LLM + 教研组 | 异步渐进填充（dummy 模式） | 空 = 不参与更新，不影响其他层 |

思想层设计为 **dummy-safe**：`thinking_tags` 为空时系统照常运行，教研组审定后填入即开始参与迁移追踪。

**对熟练度更新的影响**

学生使用替代方法时，各层更新规则：

| 层级 | 更新条件 | 理由 |
|---|---|---|
| 思想层 | 做了就更新（方法无关） | 思想是超越具体方法的元能力 |
| 题型层 | 做了就更新（方法无关） | 学生完成了该题型的识别与求解 |
| 方法层（标准） | 冻结 | 学生没有练到该方法 |
| 方法层（替代） | 审计通过后更新 | 新方法走 pending → ready 安全阀 |

**对 KnowledgeCard 结构的变更**

```python
class KnowledgeCard:
    problem_tags:  list[str]   # 题型层 — 必填
    method_tags:   list[str]   # 方法层 — 必填（标准解法）
    thinking_tags: list[str]   # 思想层 — 可空，渐进填充
```

原有的 `tags` 字段在过渡期保留，等迁移完成后废弃。

#### Planner prompt 中的卡片分区

Planner 需要区分训练目标和参考素材：

```
【本题训练目标（必须覆盖）】
- 因式分解: hints...

【学生方法相关参考（可参考，非必须覆盖）】
- 求根公式: hints...
```

#### CardRetriever 组件定位

建议放在 `agent/knowledge/` 下，与 Memory 平级：

- `agent/knowledge/card_retriever.py` — 检索逻辑
- `agent/knowledge/card_index.py` — embedding 索引构建与维护
- `agent/knowledge/card_store.py` — 卡片仓库读取

该组件不仅服务 Tutor（Planner 补充卡片），也可服务：
- Review：`explain_concept` 时检索相关卡片，替代全量注入
- Recommend：基于学生掌握度检索待强化的卡片
- Memory：`commit_session` 时正确记录检索卡对应的 concept

#### 待决定项

1. **知识图谱层级结构**：是预定义静态树，还是从 `prerequisite_ids` 关系自动推导？
2. **检索结果是否缓存到 session**：同一 session 内多次触发替代解法检测时，复用还是重新检索？
3. **embedding 模型选择**：用通用 embedding（如 text-embedding-3-small）还是数学领域专用模型？

### 修订后的实施顺序

```
Phase 0: 快赢项（content 截断、passed_history 精简、隐式截断统一）         ← 未开始
Phase 1: ProblemContext 预算化 helper                                      ✅ 已完成
Phase 2: PlannerAgent + PathEvaluatorAgent + GraderAgent 输入投影           ✅ 已完成
Phase 2.5: TutorContextBuilder + ReviewContextBuilder 结构化               ✅ 已完成
Phase 3: Review Agent 分治（MethodEnumerator/Solver + compare/explain）     ✅ 已完成
Phase 4: Memory / Progress / Recommend 分治 + SemanticMemory 任务快照       ✅ 已完成
Phase 4.5: Projection Registry + 决策型 Prompt 精简                         ✅ 已完成
Phase 5: CardRetriever 组件（知识卡 RAG 检索）                              ← 未开始
```

#### 当前状态（2026-03-23）

**Phase 1-4.5 全部完成**。Context Governance Layer（budget_policy / assembler / projection_registry / signature / telemetry）已就绪，7 类 Agent 全部接入投影改造和 assembler 预算仲裁。

**剩余两个方向**：

1. **Phase 0（快赢项）** — 尚未开始。content 截断、passed_history 精简、调用方侧统一截取、请求级上下文缓存等低风险改动。这些改动独立于 RAG，可随时做。

2. **Phase 5（CardRetriever）** — 尚未开始。需要：
   - 知识卡仓库的全量数据可用
   - embedding 索引基础设施就绪
   - Grader 输出扩展（`knowledge_point_tags`）已落地
   - 三层标签体系（thinking_tags / problem_tags / method_tags）设计定稿

Phase 0 解决”上下文太长”的残留问题；Phase 5 解决”上下文给错”的问题。两者互不阻塞。

#### 建议下一步

> Phase 0 快赢 → Phase 5 CardRetriever

Phase 0 改动小（每项 10-30 行代码），能进一步降低 token 消耗，且为 Phase 5 的 RAG 检索结果注入打好基础（检索结果也需要走 assembler 裁剪管道，如果现有 history 还在无限膨胀，预算空间会被挤占）。

---

## 一句话总结

这次优化的核心不是”少给模型 token”，而是”让每个 Agent 只看到它此刻真正需要看到的事实”——包括确保替代解法场景下 Planner 能拿到**正确的**知识卡，而不只是更**少的**知识卡。
