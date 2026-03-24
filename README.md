# DeepSenior 仓库说明

这份 README 的目标不是介绍“怎么用一句话运行项目”，而是帮助你快速理解当前仓库里每个目录、每个核心文件分别负责什么，方便后续做审计、接手开发、补测试和排查问题。

## 项目概览

当前仓库围绕一个分层教学系统展开，核心由 5 个业务模块组成：

- `tutor`：做题时的实时辅导，负责判题、路由、苏格拉底式引导、深问和回退。
- `review`：做完题后的复盘，负责错误回放、方法枚举、方法演示和理解验证。
- `memory`：把单次会话沉淀成长期语义记忆，形成学生画像。
- `progress`：基于长期记忆和遗忘曲线生成学习计划与阶段总结。
- `recommend`：根据刚完成的 Tutor / Review 会话推荐下一步练习或复习动作。

这些模块共享一套基础设施：

- `agent/infra/` 提供自包含的配置、日志、LLM 调用和 Prompt 加载能力。
- `agent/base_agent.py` 负责统一的 LLM 调用基类。
- `agent/skills_common.py` 和各模块 `skills/registry.py` 负责把“Agent 能力”暴露成可编排的 skill。
- `tools/debug_cli.py` 提供了一个最小可交互调试终端，用来调状态机。

## 阅读约定

- 这份说明重点覆盖源码、Prompt、工具脚本和项目文档。
- `__pycache__/`、`.pytest_cache/`、以及 `data/sessions/**/*.pkl` 这类缓存/快照文件属于运行产物，不逐个展开讲业务逻辑。
- 当前仓库已经把原先残留的 `src.*` 依赖收敛到 `agent/infra/` 这层，整体上可以作为一个自包含的 `agent` 包理解。
- `tools/debug_cli.py` 不再需要伪造 `src.*` 模块才能工作；它现在直接配置 `agent.infra.llm`，支持 Mock 和基于环境变量的 Live 模式。

## 根目录文件

- `README.md`：当前这份仓库导读，解释目录结构和文件职责。
- [`RAG_DATA_MODEL_DESIGN.md`](./doc/rag_card/RAG_DATA_MODEL_DESIGN.md)：围绕数据模型、记忆/RAG 结构的设计文档。
- `SYSTEM_DOCS.md`：系统说明文档的 Markdown 版本。
- `SYSTEM_DOCS.pdf`：系统说明文档的 PDF 导出版。
- `SYSTEM_TEX.tex`：系统说明文档的 LaTeX 源文件。
- `TODO_LOG.md`：迭代过程中的待办和变更记录。
- `puppeteer-config.json`：浏览器自动化相关配置。
- `test_log.md`：`tools/auto_test.py` 运行后写出的调试/测试日志。

## `.claude/`

- `.claude/settings.local.json`：本地 AI 助手/终端协作工具的个人化配置，不属于核心业务代码。

## `.pytest_cache/`

- `.pytest_cache/README.md`：pytest 自动生成的缓存说明文件。
- `.pytest_cache/v/`：pytest 缓存目录，非手写源码。

## `agent/`

这是业务系统的核心代码目录，下面按模块拆分。

- `agent/__init__.py`：顶层包导出文件，稳定暴露 `BaseAgent`，并兼容性地尝试导入可选的 `chat` 子模块。
- `agent/base_agent.py`：所有 LLM Agent 的统一基类，负责调用 `agent.infra` 中的配置、Prompt、日志和 LLM 服务。
- `agent/infra/`：仓库内置的基础设施层，用来替代历史上的 `src.config`、`src.logging`、`src.services.*`。
- `agent/skills_common.py`：定义 `SkillMeta` 元数据和 `wrap_sync_as_async` 工具，供各模块 skill registry 复用。
- `agent/utils.py`：通用工具函数，目前主要提供文本压缩匹配和宽容 JSON 解析。

## `agent/infra/`

这是现在的运行时基础设施层，让整个 `agent` 包不再依赖外部 `src.*` 宿主工程。

- `agent/infra/__init__.py`：基础设施层说明文件，标注这一层用于取代旧 `src.*` 依赖。
- `agent/infra/config.py`：配置工具和 `settings` 单例；同时负责读取各模块可选的 `agents.yaml` 参数文件，不存在时回退默认值。
- `agent/infra/llm.py`：统一的 LLM 服务层，封装 OpenAI 兼容接口调用、流式输出、模型配置和程序内覆盖。
- `agent/infra/logging.py`：项目内置日志工具和轻量级 `LLMStats` 统计器。
- `agent/infra/prompt.py`：Prompt 加载器，从 `agent/{module}/prompts/{language}/` 读取 YAML Prompt。

## `agent/tutor/`

`tutor` 模块负责“做题过程中的辅导”，是状态机最复杂的一层。

- `agent/tutor/__init__.py`：聚合导出 `TutorManager`、`SkillRegistry` 和 Tutor 相关核心数据结构。
- `agent/tutor/action_classifier.py`：对 `classify_action` skill 的业务封装；负责构建上下文、调用分类器并做 fallback。
- `agent/tutor/context_builder.py`：把 `TutorSession` 压缩成可供 LLM 路由判断的结构化上下文文本。
- `agent/tutor/data_structures.py`：Tutor 模块的核心数据模型，包括题目上下文、知识卡、错误类型、会话状态、Checkpoint、Plan、Router/Grader 结果等。
- `agent/tutor/deep_dive_handler.py`：管理“深问”子流程，处理深问的开启、轮次预算、收束和回主线。
- `agent/tutor/regression_handler.py`：管理追问、软回顾和硬回退，帮助学生回到前置 checkpoint。
- `agent/tutor/tutor_manager.py`：Tutor 的主协调器/状态机入口，负责 submission 处理、消息推进、会话持久化、替代解法处理和会话导出。

### `agent/tutor/agents/`

这一层是具体的 LLM Agent 实现，供 `SkillRegistry` 统一注册。

- `agent/tutor/agents/__init__.py`：聚合导出 Tutor 相关 agent 类。
- `agent/tutor/agents/grader_agent.py`：批改学生解题过程，判断错误类型、正确性以及是否使用了替代解法。
- `agent/tutor/agents/intent_classifier_agent.py`：把学生消息分类为 Tutor 动作，例如继续引导、请求答案、深问、回退等。
- `agent/tutor/agents/path_evaluator_agent.py`：当学生走了标准知识卡以外的方法时，评估其数学有效性和教学对齐程度。
- `agent/tutor/agents/planner_agent.py`：根据题目和当前错误状态生成结果导向、方法中立的 checkpoint 引导计划。
- `agent/tutor/agents/router_agent.py`：负责两类决策，一类是批改后规则路由，一类是 checkpoint 通过判定。
- `agent/tutor/agents/socratic_agent.py`：按 checkpoint 和 hint level 生成苏格拉底式引导语，支持流式输出。

### `agent/tutor/prompts/zh/`

这一层存放中文 Prompt 模板，由 `BaseAgent` 按模块和 agent 名自动加载。

- `agent/tutor/prompts/zh/grader_agent.yaml`：`GraderAgent` 的系统 Prompt 和用户模板。
- `agent/tutor/prompts/zh/intent_classifier_agent.yaml`：动作分类器使用的 Prompt。
- `agent/tutor/prompts/zh/path_evaluator_agent.yaml`：替代解法评估 Prompt。
- `agent/tutor/prompts/zh/planner_agent.yaml`：Checkpoint 规划 Prompt。
- `agent/tutor/prompts/zh/router_agent.yaml`：Checkpoint 评估/路由相关 Prompt。
- `agent/tutor/prompts/zh/socratic_agent.yaml`：苏格拉底引导 Prompt。

### `agent/tutor/skills/`

这一层把 Tutor 能力封装成无状态可调用 skill。

- `agent/tutor/skills/__init__.py`：导出 `SkillRegistry` 和 `SkillMeta`。
- `agent/tutor/skills/registry.py`：初始化 Tutor 相关 agent，并统一注册 `grade_work`、`plan_guidance`、`generate_hint`、`stream_hint`、`evaluate_approach`、`evaluate_checkpoint`、`route_decision`、`classify_action` 等 skill。

## `agent/review/`

`review` 模块负责“做完题后的复盘”，重点是方法比较、错误回放和理解验证。

- `agent/review/__init__.py`：聚合导出 `ReviewChatManager`、`ReviewSkillRegistry` 和复盘相关核心数据结构。
- `agent/review/context_builder.py`：把 `ReviewSession` 压缩为适合 LLM 使用的结构化上下文。
- `agent/review/data_structures.py`：Review 模块的数据结构定义，包括 `ReviewAction`、错误快照、挣扎点、方法信息、理解检验和复盘会话。
- `agent/review/review_chat_manager.py`：Review 会话主调度器，负责创建复盘会话、继承 Tutor export、路由学生意图、触发错误回放与理解验证。

### `agent/review/agents/`

- `agent/review/agents/__init__.py`：聚合导出 Review 相关 agent 类。
- `agent/review/agents/method_enumerator_agent.py`：列举一道题的可行解法，并区分标准方法和替代方法。
- `agent/review/agents/method_solver_agent.py`：按指定方法生成完整解题演示。
- `agent/review/agents/review_chat_agent.py`：复盘通用对话 Agent，同时承载意图识别、错误回放、理解提问和理解评估等子能力。

### `agent/review/prompts/zh/`

- `agent/review/prompts/zh/method_enumerator_agent.yaml`：解法枚举 Prompt。
- `agent/review/prompts/zh/method_solver_agent.yaml`：按指定方法演示解法的 Prompt。
- `agent/review/prompts/zh/review_chat_agent.yaml`：复盘聊天、意图分类和理解验证相关 Prompt。

### `agent/review/skills/`

- `agent/review/skills/__init__.py`：导出 `ReviewSkillRegistry` 和 `SkillMeta`。
- `agent/review/skills/registry.py`：注册 `enumerate_methods`、`solve_method`、`classify_intent`、`respond_review`、`replay_errors`、`ask_understanding`、`ask_transfer`、`evaluate_understanding` 等复盘 skill，并可选复用 Tutor 的 `evaluate_approach`。

## `agent/memory/`

`memory` 模块负责把单次会话结果沉淀为长期语义画像，是个体化学习的基础。

- `agent/memory/__init__.py`：聚合导出 `MemoryManager`、`MemoryStore`、`MemorySkillRegistry` 以及记忆相关数据结构。
- `agent/memory/data_structures.py`：定义情节记忆、语义记忆、概念更新、方法观察、掌握度记录等数据模型。
- `agent/memory/memory_manager.py`：记忆模块主入口，对外提供“提交一次会话记忆”和“读取学生上下文”两类能力。
- `agent/memory/memory_store.py`：纯持久化层，负责把 episodic/semantic memory 落盘和读取。

### `agent/memory/agents/`

- `agent/memory/agents/__init__.py`：导出 `MemoryDistillerAgent`。
- `agent/memory/agents/memory_distiller_agent.py`：把一条情节记忆和当前语义画像蒸馏成增量更新指令。

### `agent/memory/prompts/zh/`

- `agent/memory/prompts/zh/memory_distiller_agent.yaml`：记忆蒸馏 Prompt。

### `agent/memory/skills/`

- `agent/memory/skills/__init__.py`：导出 `MemorySkillRegistry` 和 `SkillMeta`。
- `agent/memory/skills/registry.py`：注册 `distill_memory` skill，供 `MemoryManager` 调用。

## `agent/progress/`

`progress` 模块负责把长期记忆转换成“今天做什么”和“最近学得怎么样”。

- `agent/progress/__init__.py`：聚合导出 `ProgressManager`、`ProgressSkillRegistry` 和进度模块数据结构。
- `agent/progress/data_structures.py`：定义每日任务、任务计划、进展总结、遗忘衰减记录等数据结构。
- `agent/progress/ebbinghaus.py`：纯数学层，实现遗忘曲线、保持率、复习优先级和待复习概念排序。
- `agent/progress/progress_manager.py`：进度模块主调度器，负责生成今日计划、阶段总结，以及在会话结束后刷新衰减记录。

### `agent/progress/agents/`

- `agent/progress/agents/__init__.py`：导出 `ProgressSummaryAgent` 和 `TaskPlannerAgent`。
- `agent/progress/agents/progress_summary_agent.py`：结合长期画像和近期会话，生成“有洞见”的学习进展叙述。
- `agent/progress/agents/task_planner_agent.py`：把语义记忆、遗忘曲线结果和近期学习行为综合成今日任务列表。

### `agent/progress/prompts/zh/`

- `agent/progress/prompts/zh/progress_summary_agent.yaml`：进展总结 Prompt。
- `agent/progress/prompts/zh/task_planner_agent.yaml`：今日任务规划 Prompt。

### `agent/progress/skills/`

- `agent/progress/skills/__init__.py`：导出 `ProgressSkillRegistry` 和 `SkillMeta`。
- `agent/progress/skills/registry.py`：注册 `plan_tasks` 和 `summarize_progress` 两类进度 skill。

## `agent/recommend/`

`recommend` 模块负责在一次 Tutor/Review 完成后，推荐学生下一步应该做什么。

- `agent/recommend/__init__.py`：聚合导出 `RecommendManager`、`ProblemBankBase`、`RecommendSkillRegistry` 和推荐相关数据结构。
- `agent/recommend/data_structures.py`：定义推荐类型、推荐来源、问题查询条件、推荐上下文和推荐结果对象。
- `agent/recommend/problem_bank.py`：抽象题库接口，约束“如何查询下一题/前置题”；同时提供空实现 `NullProblemBank`。
- `agent/recommend/recommend_manager.py`：推荐模块调度器，负责拼接推荐上下文、调用推荐决策 skill，并向题库取题。

### `agent/recommend/agents/`

- `agent/recommend/agents/__init__.py`：导出 `RecommendAgent`。
- `agent/recommend/agents/recommend_agent.py`：只做“推荐策略决策”，决定推荐类型、目标知识点、难度和说明文字，不直接查题。

### `agent/recommend/prompts/zh/`

- `agent/recommend/prompts/zh/recommend_agent.yaml`：推荐决策 Prompt。

### `agent/recommend/skills/`

- `agent/recommend/skills/__init__.py`：导出 `RecommendSkillRegistry` 和 `SkillMeta`。
- `agent/recommend/skills/registry.py`：注册 `decide_recommendation` skill。

## `tools/`

这是开发调试和回归验证用的工具目录。

- `tools/debug_cli.py`：微型调试终端，支持 Mock / Live 两种模式；现在会直接配置 `agent.infra.llm`，用来手动跑 Tutor/Review 状态机、查看上下文、调用 skill、配置 mock override。
- `tools/auto_test.py`：自动化调试脚本，会模拟学生与 `debug_cli` 交互，并把完整过程写入 `test_log.md`。

## `data/`

这是运行期产物目录，主要保存会话快照。

### `data/sessions/`

- `data/sessions/_debug/`：Mock 模式 Tutor 调试会话快照。
- `data/sessions/_debug_live/`：Live 模式 Tutor 调试会话快照。
- `data/sessions/_debug_review/`：Mock 模式 Review 调试会话快照。
- `data/sessions/_debug_review_live/`：Live 模式 Review 调试会话快照。

这些目录下的每个 `.pkl` 文件本质上都是一次会话对象的持久化快照，文件名通常就是 `session_id`。它们是运行生成物，不是手写业务源码，因此不逐个解释内容。

## 代码阅读建议

如果你是第一次接手这个仓库，建议按下面顺序阅读：

1. `agent/infra/` 和 `agent/base_agent.py`：先理解整个系统现在如何独立完成配置、日志、Prompt 和 LLM 调用。
2. `agent/tutor/data_structures.py` 和 `agent/tutor/tutor_manager.py`：理解主状态机和会话对象。
3. `agent/review/review_chat_manager.py`：理解做题后的复盘流程。
4. `agent/memory/memory_manager.py`：理解会话如何沉淀为长期画像。
5. `agent/progress/progress_manager.py` 和 `agent/recommend/recommend_manager.py`：理解“做完以后怎么安排下一步”。
6. `tools/debug_cli.py`：最后用调试终端把上述链路串起来验证。

## 一句话总结

可以把这个仓库理解成一套“做题中辅导 + 做题后复盘 + 长期记忆沉淀 + 后续学习规划/推荐”的教学 Agent 组合系统；`tutor` 负责过程，`review` 负责复盘，`memory` 负责沉淀，`progress` 和 `recommend` 负责后续动作。
