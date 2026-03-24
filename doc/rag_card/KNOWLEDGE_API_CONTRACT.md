# Knowledge API Contract

更新时间：2026-03-23

状态：**旧方案 / 仅供历史参考**

> 说明：本文档描述的是 `knowledge_point_tags + embedding 主召回` 的旧接口口径。自 2026-03-23 起，新实现请优先参考 `KNOWLEDGE_API_CONTRACT_V2.md`、`METHOD_CATALOG_CONTRACT_V2.md` 与 `RAG_EVAL_AND_GATING_V2.md`。

关联文档：
- `RAG_SCHEMA_V3.md` — 最终 schema / 内容源结构
- `RAG_RETRIEVAL_DESIGN.md` — 检索策略与业务意图
- `DATA_MODEL_CONVERGENCE.md` — 训练目标 / 参考素材 / concept 的语义边界
- `AUDIT_OPERATIONS_DESIGN.md` — Audit Worker proposal 与发布流

---

## 本文档回答的问题

**`agent/knowledge/` 里到底要提供哪些接口、输入输出长什么样、出错时怎么退化。**

如果实现与本文档冲突，优先以本文档为准；若本文档与 schema 冲突，以 `RAG_SCHEMA_V3.md` 为准。

---

## 1. 共享数据形状

```python
@dataclass
class PublishedKnowledgeCard:
    card_id: str
    chapter: str
    title: str
    summary: str
    general_methods: list[str]
    hints: dict[int, str]                 # {1: "...", 2: "...", 3: "..."}
    common_mistakes: list[str]
    prerequisite_card_ids: list[str]
    problem_tags: list[str]
    method_tags: list[str]
    thinking_tags: list[str]


@dataclass
class QuestionCardLink:
    card_id: str
    relation: str                        # target | background | prereq
    weight: float


@dataclass
class SolutionCardLink:
    card_id: str
    relation: str                        # primary | support | prereq | contrast
    weight: float


@dataclass
class PublishedSolution:
    solution_id: str
    question_id: str
    method_name: str
    is_standard: bool
    reference_cards: list[SolutionCardLink]


@dataclass
class PublishedQuestion:
    question_id: str
    chapter: str
    difficulty: int
    stem: str
    answer_schema: dict
    question_cards: list[QuestionCardLink]
    solutions: list[PublishedSolution]


@dataclass
class NormalizedTagMatch:
    raw_tag: str
    concept_id: str
    layer: str
    chapter: str | None
    confidence: float
    matched_alias: str
    ambiguous: bool = False


@dataclass
class RetrievedCard:
    card: PublishedKnowledgeCard
    score: float
    source: str                          # alias | neighbor | embedding
    matched_concepts: list[str]
```

约束：

- 所有 runtime 接口默认只返回 `status=active` 的已发布记录
- 返回集合必须稳定排序；相同分数按 `weight DESC -> updated_at DESC -> id` 兜底
- 未命中返回空列表或 `None`，**不抛业务异常**

---

## 2. CardStore 契约

`CardStore` 是**在线只读权威入口**。Tutor / Review / Recommend / Audit Worker 都只能通过它读取已发布数据。

```python
class CardStore(Protocol):
    def get_question(self, question_id: str) -> PublishedQuestion | None: ...
    def get_solution(self, solution_id: str) -> PublishedSolution | None: ...
    def get_card(self, card_id: str) -> PublishedKnowledgeCard | None: ...

    def list_question_cards(
        self,
        question_id: str,
        *,
        relation: str | None = None,
    ) -> list[QuestionCardLink]: ...

    def list_solution_cards(
        self,
        solution_id: str,
        *,
        relation: str | None = None,
    ) -> list[SolutionCardLink]: ...

    def list_card_primary_concepts(
        self,
        card_id: str,
        *,
        layer: str,
    ) -> list[str]: ...

    def normalize_tags(
        self,
        tags: list[str],
        *,
        chapter: str | None = None,
        preferred_layer: str = "method",
        max_candidates_per_tag: int = 3,
    ) -> list[NormalizedTagMatch]: ...

    def build_problem_context(
        self,
        question_id: str,
        *,
        active_solution_id: str | None = None,
    ) -> PublishedQuestion | None: ...
```

### 2.1 语义要求

- `get_question()` 必须把 `question_cards` 和 `solutions.reference_cards` 一起带出，避免上层再次拼接多表
- `build_problem_context()` 必须保留 `question_card_link.relation/weight`，不能退化回扁平 `knowledge_cards`
- `list_card_primary_concepts()` 默认只返回 `is_primary=true` 且 `status=active` 的 concept
- `normalize_tags()` 必须遵循 `DATA_MODEL_CONVERGENCE.md` 的消歧规则

### 2.2 `normalize_tags()` 返回规则

- 全部精确命中：返回 1..N 个 `NormalizedTagMatch`
- 同名冲突但可排序：返回多个候选，`ambiguous=true`
- 完全无命中：返回空列表，由调用方决定是否进入 embedding 兜底
- 不允许在无 active alias 时 fallback 到 `card_id`

---

## 3. CardIndex 契约

`CardIndex` 负责**候选集内语义精排**，不负责结构化过滤，不直接碰数据库。

```python
class CardIndex(Protocol):
    def build(self, cards: list[PublishedKnowledgeCard]) -> None: ...
    def upsert(self, cards: list[PublishedKnowledgeCard]) -> None: ...
    def remove(self, card_ids: list[str]) -> None: ...

    def search(
        self,
        query_text: str,
        *,
        candidate_card_ids: list[str],
        top_k: int = 5,
    ) -> list[tuple[str, float]]: ...
```

约束：

- 只接受 Stage 1 已缩圈的 `candidate_card_ids`
- `search()` 返回 `(card_id, score)`，按 `score DESC` 排序
- 若 `candidate_card_ids=[]`，返回空列表
- 索引构建与更新必须可离线完成；在线路径不做大规模重建

---

## 4. CardRetriever 契约

`CardRetriever` 只负责：**把学生当前方法相关的参考卡找出来**。

```python
@dataclass
class CardRetrieveRequest:
    question_id: str
    active_solution_id: str | None
    chapter: str
    target_card_ids: list[str]
    tags: list[str]
    fallback_query: str
    top_k: int = 3
    session_id: str | None = None


@dataclass
class CardRetrieveResult:
    supplementary_cards: list[RetrievedCard]
    normalized_tags: list[NormalizedTagMatch]
    stage1_candidate_card_ids: list[str]
    cache_hit: bool
    warnings: list[str]
    retrieval_signature: str
```

```python
class CardRetriever(Protocol):
    def retrieve(self, request: CardRetrieveRequest) -> CardRetrieveResult: ...
```

### 4.1 行为语义

Stage 1 必须做这些事：

1. `tags -> concept_alias -> concept_id`
2. 根据 `chapter`、`target_card_ids`、`prerequisite_card_ids` 缩圈
3. 过滤掉 `target_card_ids` 已覆盖的卡片，避免把训练目标和参考素材混在一起

Stage 2 必须做这些事：

1. 用 `fallback_query` 对 Stage 1 候选做 embedding 精排
2. 保留 top-k
3. 结果写明 `source=alias/neighbor/embedding`

### 4.2 退化规则

- 没有 alias 命中：允许只走章节/邻域过滤 + embedding
- 没有 Stage 1 候选：返回空结果，`warnings += ["empty_stage1_candidate"]`
- Stage 2 精排为空：返回空结果，`warnings += ["empty_retrieval"]`
- 不允许抛异常中断主链路；Planner/Review 必须能在空结果下退化到仅使用 target cards

### 4.3 Session 缓存

缓存 key 必须至少包含：

- `question_id`
- `active_solution_id`
- 标准化后的 `concept_id` 集合
- `top_k`

同一 session 同一 key 命中时可复用；`active_solution_id` 变化时必须失效。

---

## 5. Audit Worker 契约

```python
@dataclass
class AuditProposal:
    proposed_card_ids: list[str]
    proposed_concepts: list[dict]
    proposed_aliases: list[dict]
    decision_meta: dict


class AuditWorker(Protocol):
    def build_proposal(self, task: dict) -> AuditProposal: ...
    def persist_proposal(self, task_id: str, proposal: AuditProposal) -> None: ...
```

约束：

- Worker 只写 `rag_audit_task.proposal_payload`，不直接写正式索引
- `proposed_card_ids` 必须来自 `CardRetriever`
- `proposed_aliases` 只允许作为建议，不允许绕过审批直接发布

---

## 6. 在线链路禁止事项

- 在线 Tutor / Review / Recommend 不允许直接查询原始散落 JSON
- 在线链路不允许直接写 `solution_card_link` / `concept_alias` / `knowledge_card_concept_link`
- 在线链路不允许因为 alias 未命中就把 `raw_tag` 直接当作 `concept_id`

---

## 7. 最小落地顺序

1. `CardStore`
2. `normalize_tags()`
3. `CardIndex`
4. `CardRetriever.retrieve()`
5. `AuditWorker.build_proposal()`

---

## 8. 非目标

- 本文档不定义具体 embedding 模型选择；由 `RAG_RETRIEVAL_DESIGN.md` 的 D2 决策控制
- 本文档不定义灰度门禁阈值；见 `RAG_EVAL_AND_GATING.md`
