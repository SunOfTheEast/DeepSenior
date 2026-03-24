# Knowledge API Contract v2

更新时间：2026-03-23

状态：**执行基线（v2）**

关联文档：
- `RAG_SCHEMA_V3.md` — 正式 schema / 内容源 / 发布态约束
- `METHOD_CATALOG_CONTRACT_V2.md` — `MethodCatalog` 目录结构、`question -> topic` 解析与 slot 规范
- `RAG_EVAL_AND_GATING_V2.md` — `MethodRouter` / `CardSelector` / fallback 的评测与门禁
- `AUDIT_OPERATIONS_DESIGN.md` — `rag_audit_task` proposal / 审批 / 回滚边界
- `plan.md` — v2 入口摘要与在线链路说明

> 说明：自 2026-03-23 起，**新实现统一以本文档为准**。旧 `KNOWLEDGE_API_CONTRACT.md` 仅保留为 `knowledge_point_tags + embedding 主召回` 方案的历史参考。

---

## 本文档回答的问题

**`agent/knowledge/` 在 v2 方案里到底提供哪些接口、各链路怎么共用、失败时如何退化。**

如果实现与本文档冲突，以本文档为准；如果本文档与 `RAG_SCHEMA_V3.md` 冲突，以 `RAG_SCHEMA_V3.md` 为准。

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
    hints: dict[int, str]
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
```

```python
@dataclass
class MethodSlot:
    slot_id: str
    name: str
    trigger: str
    card_ids: list[str]
    cross_ref: list[str] = field(default_factory=list)
    status: str = "active"               # active | shadow | deprecated
    notes: str | None = None


@dataclass
class MethodCatalogTopic:
    chapter: str
    topic: str
    version: str
    status: str                          # active | shadow | deprecated
    methods: list[MethodSlot]


@dataclass
class TopicResolveResult:
    question_id: str | None
    chapter: str
    topic: str | None
    fallback_topics: list[str]
    source: str                          # request | question_map | default | missing
```

```python
@dataclass
class CandidateCardSummary:
    card_id: str
    title: str
    summary: str
    key_insight: str
    formula_cues: list[str]
    source_slot_ids: list[str]


@dataclass
class RetrievedCard:
    card: PublishedKnowledgeCard
    score: float
    source: str                          # slot | cross_ref | selector | embedding_fallback
    selected_reason: str | None = None
```

```python
@dataclass
class MethodRouterRequest:
    question_id: str | None
    chapter: str
    topic: str
    problem_text: str
    student_work: str
    student_approach: str | None
    topic_methods: list[MethodSlot]
    cross_topic_methods: list[MethodSlot]
    target_card_ids: list[str]


@dataclass
class MethodRouterResult:
    primary_slot: str | None
    cross_slots: list[str]
    confidence: float
    reasoning: str
    slot_candidates: list[str] = field(default_factory=list)
```

```python
@dataclass
class CardSelectorRequest:
    question_id: str | None
    chapter: str
    topic: str
    problem_text: str | None
    student_work: str | None
    student_approach: str | None
    retrieval_goal: str                  # method_reference | concept_explain | reinforcement | memory_repair
    focus_terms: list[str]
    target_card_ids: list[str]
    router_result: MethodRouterResult | None
    candidate_cards: list[CandidateCardSummary]
    top_k: int = 3


@dataclass
class CardSelectorResult:
    selected_card_ids: list[str]
    additional_need: str | None
    additional_reason: str | None
    confidence: float
```

```python
@dataclass
class CardRetrieveRequest:
    consumer: str                        # planner | review | recommend | memory
    question_id: str | None
    active_solution_id: str | None
    chapter: str
    topic: str | None
    problem_text: str | None
    student_work: str | None
    student_approach: str | None
    target_card_ids: list[str]
    focus_terms: list[str] = field(default_factory=list)
    retrieval_goal: str = "method_reference"
    session_id: str | None = None
    top_k: int = 3


@dataclass
class CardRetrieveResult:
    supplementary_cards: list[RetrievedCard]
    router_result: MethodRouterResult | None
    selector_result: CardSelectorResult | None
    fallback_used: bool
    warnings: list[str]
    retrieval_signature: str


@dataclass
class RetrievalBundle:
    request: CardRetrieveRequest
    result: CardRetrieveResult
    selected_card_ids: list[str]
    router_primary_slot: str | None
    router_confidence: float | None
    selector_confidence: float | None
```

约束：

- 运行时默认只返回 `status=active` 的已发布记录。
- `MethodRouterResult.slot_candidates` 必须稳定排序；若 `primary_slot` 非空，则第一项必须等于 `primary_slot`。
- `CandidateCardSummary.formula_cues` 是**投影字段**，可由 card 摘要、slot trigger 或作者补充说明合成；它不是新的强制 DB 字段。
- `RetrievalBundle` 是跨模块共享载体；Tutor / Review / Recommend / Memory 不允许各自重新拼一份“检索结果摘要”。

---

## 2. MethodCatalog 契约

`MethodCatalog` 负责：

- 解析 `question_id -> topic`
- 加载 `topic` 菜单与 `_cross_topic` 公共菜单
- 提供 `slot_id -> MethodSlot` 的稳定查找

```python
class MethodCatalog(Protocol):
    def resolve_topic(
        self,
        *,
        question_id: str | None,
        chapter: str,
        requested_topic: str | None = None,
    ) -> TopicResolveResult: ...

    def get_topic_catalog(
        self,
        *,
        chapter: str,
        topic: str,
    ) -> MethodCatalogTopic | None: ...

    def get_cross_topic_catalog(self) -> MethodCatalogTopic: ...

    def get_slot(self, slot_id: str) -> MethodSlot | None: ...
```

行为语义：

- 若 `CardRetrieveRequest.topic` 非空，优先使用该值。
- 若 `topic` 为空，必须通过 `resolve_topic()` 读取 `question -> topic` 映射。
- 若映射缺失，允许只返回 `fallback_topics=[]` 与 `topic=None`；此时主链路可继续，但必须写 `warnings += ["missing_topic_mapping"]`，并在满足入队条件时写入 `rag_audit_task`。
- `get_cross_topic_catalog()` 必须始终可用，哪怕其中 `methods=[]`。

---

## 3. CardStore 契约

`CardStore` 是**在线只读权威入口**。在线 Tutor / Review / Recommend / Memory / Audit Worker 都只能通过它读取已发布数据。

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

    def get_card_summaries(
        self,
        card_ids: list[str],
        *,
        source_slot_id: str | None = None,
    ) -> list[CandidateCardSummary]: ...
```

行为语义：

- `get_card_summaries()` 必须返回稳定排序，且自动过滤不存在或非 `active` 的卡。
- `get_card_summaries()` 允许把 slot 的 `trigger` / `notes` 投影进 `key_insight` 或 `formula_cues`，但**不得改变卡片原始事实内容**。
- `list_card_primary_concepts()` 默认只返回 `is_primary=true` 且 `status=active` 的 concept。

---

## 4. CardIndex 契约

`CardIndex` 在 v2 里只负责 **embedding fallback**，不承担主召回职责。

```python
class CardIndex(Protocol):
    def build(self, cards: list[PublishedKnowledgeCard]) -> None: ...
    def upsert(self, cards: list[PublishedKnowledgeCard]) -> None: ...
    def remove(self, card_ids: list[str]) -> None: ...

    def search(
        self,
        query_text: str,
        *,
        candidate_card_ids: list[str] | None = None,
        chapter: str | None = None,
        topic: str | None = None,
        exclude_card_ids: list[str] | None = None,
        top_k: int = 5,
    ) -> list[tuple[str, float]]: ...
```

行为语义：

- 若 `candidate_card_ids` 非空，则只在候选集内 rerank。
- 若 `candidate_card_ids` 为空，则只允许在 `chapter` 或 `topic` 限定范围内做 fallback 搜索。
- `exclude_card_ids` 默认至少排除 `target_card_ids` 与已经选中的 `selected_card_ids`。
- 在线路径不做大规模重建。

---

## 5. MethodRouter 契约

`MethodRouter` 的职责是：**从闭合菜单里选择 method slot**，而不是自由生成方法名或 `card_id`。

```python
class MethodRouter(Protocol):
    def route(self, request: MethodRouterRequest) -> MethodRouterResult: ...
```

行为语义：

- `primary_slot` 只能是 `topic_methods` 或 `cross_topic_methods` 中已有的 `slot_id`，或 `null`。
- `cross_slots` 最多 2 个，且必须来自已提供菜单。
- `slot_candidates` 最多 3 个，按相关性排序。
- `confidence` ∈ `[0.0, 1.0]`。

默认阈值：

- `confidence >= 0.80`：高置信快路径
- `0.50 <= confidence < 0.80`：中置信路径，允许扩展 `cross_ref`
- `confidence < 0.50` 或 `primary_slot is null`：低置信路径，允许 embedding fallback，并触发 audit 入队判断

---

## 6. CardSelector 契约

`CardSelector` 的职责是：**在候选卡摘要范围内精选最终卡片，并指出缺口。**

```python
class CardSelector(Protocol):
    def select(self, request: CardSelectorRequest) -> CardSelectorResult: ...
```

行为语义：

- `selected_card_ids` 最多 `top_k` 张，默认不超过 3 张。
- `selected_card_ids` 不能包含 `target_card_ids` 中已存在的卡，避免把训练目标和参考素材混在一起。
- `additional_need` 允许为空；非空时表示当前候选卡不足，系统应使用它触发一次 chapter/topic 受限的 embedding 补充召回。
- `confidence` ∈ `[0.0, 1.0]`。

默认阈值：

- `confidence >= 0.60`：可直接把结果送下游
- `confidence < 0.60`：结果可送下游但必须带 `warnings += ["low_selector_confidence"]`，并触发 audit 入队判断

---

## 7. CardRetriever 契约

`CardRetriever` 是 v2 在线检索编排器。

```python
class CardRetriever(Protocol):
    def retrieve(self, request: CardRetrieveRequest) -> RetrievalBundle: ...
```

标准编排顺序：

1. 解析 `topic`
2. 按 `retrieval_goal` 选择主路径
3. 如需方法识别，调用 `MethodRouter`
4. 系统按 `slot_id -> card_ids` 确定性拉候选卡摘要
5. 调用 `CardSelector`
6. 按需做 embedding fallback
7. 组装 `CardRetrieveResult` 与 `RetrievalBundle`

### 7.1 不同 `retrieval_goal` 的默认路径

- `method_reference`
  - 用于 Planner 主链路
  - 走 `MethodRouter -> 查表 -> CardSelector -> 按需 fallback`
- `concept_explain`
  - 用于 Review `explain_concept`
  - 若存在最近方法上下文，可走 `MethodRouter`
  - 若 `focus_terms` 已足够明确，可直接构造候选卡再交给 `CardSelector`
- `reinforcement`
  - 用于 Recommend 强化卡检索
  - 默认以 `focus_terms + target_card_ids` 先构造候选，再交 `CardSelector`
  - 若当前 session 明显是方法性缺口，可补跑 `MethodRouter`
- `memory_repair`
  - 用于 Memory / Audit 的补录或修复
  - 优先复用已有 `RetrievalBundle`；只有缺失时才重新调用检索

### 7.2 Fallback 规则

- `topic` 缺失：允许只使用 `_cross_topic` + `chapter` 受限 fallback 搜索，并写 `warnings += ["missing_topic_mapping"]`
- `primary_slot is null`：允许直接走 fallback 搜索，并写 `warnings += ["empty_primary_slot"]`
- 候选卡为空：写 `warnings += ["empty_candidate_cards"]`
- `additional_need` 非空：允许再做一次 fallback 搜索，最多补 2 张
- 全流程空结果：返回空 `supplementary_cards`，**不抛业务异常**

### 7.3 Session 缓存

缓存 key 必须至少包含：

- `consumer`
- `question_id`
- `active_solution_id`
- `chapter`
- `topic`
- `retrieval_goal`
- `student_approach` 的标准化摘要或 `focus_terms`
- `top_k`

同一 session 同一 key 可复用；`active_solution_id`、`topic` 或 `retrieval_goal` 变化时必须失效。

---

## 8. 共享消费约定

### 8.1 Planner

- 输入：`target_cards + RetrievalBundle.result.supplementary_cards + retrieval_signature`
- 只关心 `supplementary_cards` 是否可用、`retrieval_signature` 是什么，不直接消费 router/selector 的推理全文
- RAG 为空时必须退化到只使用 `target_cards`

### 8.2 Review

- `explain_concept` 必须走同一个 `CardRetriever`
- 输入来源：当前题目、最近一次方法上下文、学生追问文本生成的 `focus_terms`
- 只取 `1-2` 张最相关卡，替代全量知识卡注入

### 8.3 Recommend

- 推荐类型仍由 `RecommendAgent` 决策
- 若类型是 `REVIEW_CONCEPT`，RAG 负责把“该补什么”落成强化卡
- 若类型是 `EASIER_PROBLEM` / `SIMILAR_PROBLEM` / `HARDER_PROBLEM`，RAG 负责提供更贴近缺口的卡片或题目目标提示，不替代最终选题器

### 8.4 Memory

- Memory 优先复用上游 `RetrievalBundle`
- 至少记录：
  - `selected_card_ids`
  - `router_primary_slot`
  - `router_confidence`
  - `retrieval_signature`
- 若 `router_confidence < 0.50`、`selector_confidence < 0.60` 或正式 `solution_card_link` 缺失，必须具备入 `rag_audit_task` 的能力

---

## 9. 四条完整示例链路

以下示例统一使用同一场景：

- 题型：解析几何 / 椭圆
- 标准法：设点联立
- 学生法：`x = 2cosα, y = sinα`

### 9.1 Planner 链路

```python
request = CardRetrieveRequest(
    consumer="planner",
    question_id="analytic_ellipse_001",
    active_solution_id=None,
    chapter="解析几何",
    topic=None,
    problem_text="已知椭圆 ... 求 ...",
    student_work="设点 P(2cosα, sinα)，再由 ...",
    student_approach="学生使用参数化设点绕开联立方程",
    target_card_ids=["card_set_point", "card_conic_relation"],
    session_id="sess_001",
)
```

预期：

- `MethodCatalog.resolve_topic()` 解析出 `topic="椭圆"`
- `MethodRouter.primary_slot="ellipse_parametric"`
- `CardSelector.selected_card_ids=["card_ellipse_parametric", "card_trig_substitution"]`
- Planner 最终拿到 `target_cards + supplementary_cards + retrieval_signature`

### 9.2 Review 链路

```python
request = CardRetrieveRequest(
    consumer="review",
    question_id="analytic_ellipse_001",
    active_solution_id=None,
    chapter="解析几何",
    topic="椭圆",
    problem_text="已知椭圆 ... 求 ...",
    student_work="为什么这里能直接写 x=2cosα, y=sinα？",
    student_approach="围绕椭圆参数化追问其合法性",
    target_card_ids=["card_set_point", "card_conic_relation"],
    focus_terms=["参数方程", "这样设点是否合法"],
    retrieval_goal="concept_explain",
    session_id="sess_001",
    top_k=2,
)
```

预期：

- Review 只拿 `1-2` 张解释卡
- 若已有最近方法上下文，允许复用 `ellipse_parametric` 相关候选
- 不再把整套解析几何知识卡注入 prompt

### 9.3 Recommend 链路

```python
request = CardRetrieveRequest(
    consumer="recommend",
    question_id="analytic_ellipse_001",
    active_solution_id=None,
    chapter="解析几何",
    topic="椭圆",
    problem_text="已知椭圆 ... 求 ...",
    student_work=None,
    student_approach="学生会做参数化，但对参数关系理解不稳",
    target_card_ids=["card_set_point", "card_conic_relation"],
    focus_terms=["椭圆参数方程", "参数关系"],
    retrieval_goal="reinforcement",
    session_id="sess_001",
    top_k=2,
)
```

预期：

- Recommend 类型仍由 `RecommendAgent` 给出
- RAG 返回 1-2 张强化卡，作为“先复习什么”的具体材料
- 若后续还要选题，可把这些卡对应的 concept / solution 作为题目筛选提示

### 9.4 Memory 链路

```python
bundle = RetrievalBundle(
    request=request,
    result=result,
    selected_card_ids=["card_ellipse_parametric", "card_trig_substitution"],
    router_primary_slot="ellipse_parametric",
    router_confidence=0.88,
    selector_confidence=0.84,
)
```

预期：

- Memory 优先直接消费这个 `bundle`
- `EpisodicMemory` 记录 `selected_card_ids`、`router_primary_slot`、`router_confidence`、`retrieval_signature`
- 若正式 `solution_card_link` 尚未存在，则同时触发 `rag_audit_task`

---

## 10. 失败场景与退化规则

### 10.1 低置信度

- 条件：`MethodRouter.confidence < 0.50`
- 处理：直接走 fallback 搜索，`warnings += ["low_router_confidence"]`
- 审计：满足入队条件时写 `rag_audit_task`

### 10.2 无 slot 命中

- 条件：`primary_slot is null`
- 处理：允许仅用 `_cross_topic` + `chapter` 受限 fallback 搜索
- 审计：必须具备入队能力，任务类型优先 `new_method_rag`

### 10.3 候选卡不足

- 条件：`CardSelector.additional_need` 非空或 `selected_card_ids=[]`
- 处理：最多补 1 次 embedding fallback，补 1-2 张
- 审计：若补完仍不足，写 `warnings += ["insufficient_candidate_cards"]`

### 10.4 Audit 入队

默认入队条件至少包括：

- `missing_topic_mapping`
- `empty_primary_slot`
- `low_router_confidence`
- `low_selector_confidence`
- `missing_solution_card_link`

---

## 11. Audit Worker 契约

```python
@dataclass
class AuditProposal:
    proposed_slot: dict | None
    proposed_card_ids: list[str]
    proposed_aliases: list[dict]
    decision_meta: dict


class AuditWorker(Protocol):
    def build_proposal(self, task: dict, bundle: RetrievalBundle | None = None) -> AuditProposal: ...
    def persist_proposal(self, task_id: str, proposal: AuditProposal) -> None: ...
```

约束：

- Worker 只写 `rag_audit_task.proposal_payload`，不直接写正式索引
- `proposed_card_ids` 必须来自 `CardSelector` 或 `CardIndex` 的实际结果
- `proposed_slot` 只允许作为建议，不允许在线直接发布

---

## 12. 在线链路禁止事项

- 在线链路不允许让 LLM 自由生成 `card_id`
- 在线链路不允许让 `MethodRouter` 生成菜单外的 `slot_id`
- 在线 Tutor / Review / Recommend / Memory 不允许直接读原始散落 JSON
- 在线链路不允许直接写 `solution_card_link` / `concept_alias` / `knowledge_card_concept_link`
- 在线链路不允许把旧 `knowledge_point_tags` 当作 v2 主路径输入

