# RAG 检索设计

更新时间：2026-03-23

状态：**旧方案 / 仅供历史参考**

> 说明：本文档记录的是 `Grader -> knowledge_point_tags -> CardRetriever` 的旧方案。自 2026-03-23 起，新实现请优先参考 `plan.md`、`KNOWLEDGE_API_CONTRACT_V2.md`、`METHOD_CATALOG_CONTRACT_V2.md` 与 `RAG_EVAL_AND_GATING_V2.md`。

关联文档：
- `RAG_PLANNING_3.md` — 总纲（全局依赖图与里程碑）
- `DATA_MODEL_CONVERGENCE.md` — 数据模型收敛设计（concept / alias / card_link 定义）
- `KNOWLEDGE_API_CONTRACT.md` — `CardStore` / `CardRetriever` / Worker 接口基线
- `RAG_EVAL_AND_GATING.md` — 离线评测、shadow/gray 门禁
- [`AGENT_CONTEXT_OPTIMIZATION_DESIGN.md`](../context/AGENT_CONTEXT_OPTIMIZATION_DESIGN.md) — 上下文优化设计（知识卡三层标签体系、Planner 卡片分区原始设计）

---

## 本文档回答的问题

**怎么在运行时找到对的知识卡。**

覆盖范围：CardRetriever 两阶段检索、Grader 输出扩展、alias 标准化流程、embedding 索引、Planner 卡片分区、Review/Recommend 接入。

对应总纲 Phase F。

---

## 问题背景

当学生使用替代解法时，Planner 拿到的知识卡与规划目标错位 —— 不是上下文太长，是**上下文给错了**。预算化截断无法解决此问题。

示例：一道圆锥曲线定值问题，标准方法是联立+韦达，学生用配极理论秒杀。Planner 只有"联立+韦达"的知识卡，无法为配极理论生成合理的教学路径。

---

## 设计方案：两阶段知识卡检索

引入 `CardRetriever` 组件，在 Planner 调用前按需检索相关知识卡：

```
题目挂载的卡片  = 本题的训练目标（约束，始终存在）
RAG 检索的卡片  = 当前方法的参考素材（补充，按需加载）
```

### 语义路由：Grader 输出扩展

不新增 LLM 调用。Grader 已经在分析学生的解题方法，让它在输出中多加一个标准化标签字段：

```python
@dataclass
class GraderResult:
    ...
    student_approach: str              # 已有：自然语言方法描述
    knowledge_point_tags: list[str]    # 新增：标准化知识点标签
```

LLM 做方法分析时顺带输出标签，成本几乎为零，但给 RAG 提供高质量的结构化 query。

### Stage 1：结构化缩范围（1000+ → 30-50）

利用数学知识的天然层级结构做硬过滤：

| 过滤维度 | 来源 | 效果 |
|---|---|---|
| 学科/章节 | `ProblemContext.chapter` + `tags` | 砍掉 80%+ 无关分支 |
| 知识图谱邻域 | 题目挂载卡的 `prerequisite_ids` + 同层/子节点 | 保留知识上相关的卡 |
| concept alias 精确匹配 | `knowledge_point_tags` → `concept_alias` → `concept_id` | 精准命中已知 concept |
| question target cards | `question_card_link.relation` | 锚定目标卡邻域 |

### Stage 2：语义精排（30-50 → 3-5）

在候选集内做 embedding 相似度排序：

- **Card embedding**：`"{title} | 方法: {general_methods} | 场景: {hints[0]}"`
- **Query embedding**：Grader 输出的 `knowledge_point_tags` + `student_approach`

~1000 张卡的规模无需向量数据库，numpy 数组 + cosine similarity 即可。

### 标签标准化流程

Grader 输出的 `knowledge_point_tags` 是自由文本，需经过 `concept_alias` 标准化后再进入检索。

标准化规则（详见 `DATA_MODEL_CONVERGENCE.md` 2.4 节）：

1. 只使用 `status=active` 的 alias 关联 `status=active` 的 concept
2. 同名冲突按 layer → chapter → confidence 消歧
3. 无法消歧的保留所有候选，交由 Stage 2 精排

### 完整检索流程

```
学生提交解题过程
    ↓
Grader 分析
    ├─ error_type, is_correct, ...（已有）
    └─ knowledge_point_tags: ["配方法", "完全平方式"]（新增）
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

---

## Planner 卡片分区

Planner 需要区分训练目标和参考素材：

```
【本题训练目标（必须覆盖）】
- 因式分解: hints...

【学生方法相关参考（可参考，非必须覆盖）】
- 求根公式: hints...
```

挂载卡告诉 Planner 终点在哪，检索卡告诉 Planner 学生此刻站在哪。

---

## CardRetriever 组件定位

建议放在 `agent/knowledge/` 下：

- `agent/knowledge/card_retriever.py` — 检索逻辑
- `agent/knowledge/card_index.py` — embedding 索引构建与维护
- `agent/knowledge/card_store.py` — 卡片仓库读取（与 `DATA_MODEL_CONVERGENCE.md` Phase B 共建）

具体输入输出契约以 `KNOWLEDGE_API_CONTRACT.md` 为准。

该组件不仅服务 Tutor（Planner 补充卡片），也可服务：

- Review：`explain_concept` 时检索相关卡片，替代全量注入
- Recommend：基于学生掌握度检索待强化的卡片
- Memory：`commit_session` 时正确记录检索卡对应的 concept

---

## Session 缓存策略

同一 session 内首次检索结果缓存，方法切换时刷新。

---

## 实施任务

### Phase F：CardRetriever RAG 上线

| 任务 | 改动位置 | 做什么 |
|---|---|---|
| F1 Grader 输出扩展 | `grader_agent.py` / prompt | 增加 `knowledge_point_tags` |
| F2 tag 标准化 | `concept_alias` / CardStore | 标签映射到 canonical concept |
| F3 CardIndex | `agent/knowledge/card_index.py` | embedding 构建与增量更新 |
| F4 CardRetriever | `agent/knowledge/card_retriever.py` | Stage 1 结构化过滤 + Stage 2 精排 |
| F5 Planner 卡片分区 | `planner_agent.py` / prompt | 区分 target cards 与 supplementary cards |
| F6 Review 接入 | `review_chat_manager.py` | `explain_concept` 只取最相关卡片 |
| F7 Recommend 接入 | `recommend_agent.py` | 基于薄弱 concept 检索强化卡 |
| F8 session 缓存 | CardRetriever | 首次检索缓存，方法切换时刷新 |

前置依赖：

- Phase D 最小 concept 壳模型已就绪（`DATA_MODEL_CONVERGENCE.md`）
- Phase E 两层 mastery 已生效（`MASTERY_TRACKING_DESIGN.md`）
- Phase D 审计权威数据源已可用（`AUDIT_OPERATIONS_DESIGN.md`）

验收：

- 替代方法场景下 Planner 拿到正确参考卡
- RAG 检索到正确卡片后，mastery 更新也不会一刀切
- Review 的概念解释不再全量注入所有知识卡
- Recommend 可以基于薄弱 concept 检索强化卡

离线评测指标、shadow mode 与灰度门禁统一见 `RAG_EVAL_AND_GATING.md`。

---

## 待决定项

| 编号 | 问题 | 建议 |
|---|---|---|
| D2 | embedding 放 PostgreSQL 还是本地索引文件？ | 1000-5000 卡阶段优先本地索引文件 |
| D3 | `knowledge_point_tags` 是否允许自由标签？ | 允许，但必须经 `concept_alias` 标准化后再进入检索 |
