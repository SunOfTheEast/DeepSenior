# RAG 检索疑点记录（公式主导场景）

更新时间：2026-03-24（已定案，选择路线 B，详见末尾"定案结论"）

关联文档：
- `plan.md` — 当前总实施计划与混合式 RAG 摘要
- `RAG_RETRIEVAL_DESIGN.md` — 现有 RAG 检索主方案
- `KNOWLEDGE_API_CONTRACT.md` — `CardStore` / `CardRetriever` 契约
- `RAG_SCHEMA_V3.md` — 正式内容源与发布态约束

---

## 本文档记录什么

本文档不是定案，而是记录当前最关键的一处“实现路线分歧”：

**在数学公式主导的学生作答中，系统到底该如何稳定地产生 RAG query signal，并据此找到真正相关的知识卡片。**

这个问题会直接影响系统最终实现度，因为它决定了：

- RAG 是不是只适用于“文字描述清楚的方法”
- 替代解法场景是否真的能被正确识别
- 解析几何、函数、数列等“公式密度高”的题目能否被稳定支持
- 知识库到底该做成“向量检索库”，还是“目录化知识系统”

---

## 典型问题场景

我们重新审视了一个更真实的例子：

- 题型：解析几何
- 标准解：设点、联立、爆算
- 学生解法：不用标准设点联立，而是直接参数化，例如写出  
  `x = 2cosα, y = sinα`
- 学生文本特点：几乎没有自然语言解释，主要是公式变形

这类场景里，人类老师通常一眼能看出：

- 学生在用参数方程/参数化思路
- 这是对标准方法的替代
- 这条路线的关键知识点不是“设点联立”，而是“圆锥曲线参数化”“利用参数挖掘隐含关系”

但对系统来说，问题恰恰在这里出现：

- 学生作答文本未必包含“参数方程法”这几个字
- 信息主要埋在公式结构里，而不是自然语言标签里
- 如果系统只是看一句 `x = 2cosα, y = sinα`，它到底能不能稳定知道“这是一种什么方法”并据此检索卡片，存在不小不确定性

---

## 当前方案里最不稳的假设

现有 `RAG_RETRIEVAL_DESIGN.md` 的核心假设之一是：

- Grader 在分析学生方法时，顺带输出 `student_approach`
- 再新增 `knowledge_point_tags`
- 检索侧用 `knowledge_point_tags + student_approach` 作为 query signal

这对“文字主导”的方法描述是合理的，但对“公式主导”的数学作答可能偏乐观。

更关键的是，**当前代码实际上还没走到这一步**：

- `agent/tutor/agents/grader_agent.py` 当前只返回：
  - `student_approach`
  - `uses_alternative_method`
  - `alternative_method_name`
- `agent/tutor/prompts/zh/grader_agent.yaml` 当前也没有要求输出 `knowledge_point_tags`

也就是说，现阶段系统连“自由标签版 query signal”都还没有真正落地，更不用说“公式结构归因版 query signal”。

---

## 为什么公式主导场景特别难

### 1. 公式结构不等于自然语言标签

`x = 2cosα, y = sinα` 对人类老师而言，已经足够说明“在做椭圆参数化”；  
对纯文本 LLM 或 embedding 而言，它首先只是一个符号串。

### 2. 公式本身高度依赖题目上下文

同一个表达式，在不同题目里可能意味着不同策略：

- 在椭圆题里，是参数方程
- 在三角代换题里，可能只是代换技巧
- 在某些教师整理的讲义里，它还可能被归到“构造辅助参数”

因此，不能孤立地从单行公式推方法名。

### 3. 数学方法往往体现在“结构”而不是“词汇”

很多学生不会写：

- “我这里使用参数方程法”
- “我接下来通过参数化处理圆锥曲线”

他们只会直接写：

- `设点 P(2cosα, sinα)`
- `令 x = at, y = bt`
- `设 k = y/x`

方法信息埋在写法模式里，而不是名词里。

### 4. 纯 embedding 对公式表达未必稳定

如果知识卡片正文和学生作答公式的表达风格不一致，embedding 很容易出现：

- 检到大量“同章节但不相干”的卡
- 检到“会讲参数”但并不是这类方法的卡
- 对真正关键的方法卡排序不稳定

### 5. 纯 LLM 直接选卡也不稳

如果让 LLM 自己在知识库里“理解完再直接给 `card_id`”，又容易出现：

- 幻觉卡片
- 目录理解偏差
- 命名不一致时误判
- 难做稳定评测与门禁

---

## 目前讨论形成的判断

### 判断 1：单靠 Grader 顺手吐标签，不足以覆盖数学公式主导场景

原先那种：

- Grader 输出 `knowledge_point_tags`
- RAG 直接拿这些 tags 去检索

对于解析几何替代解法场景并不稳，至少不能作为唯一方案。

### 判断 2：问题不在“LLM 能不能理解”，而在“信号如何稳定落地”

LLM 往往能大致看懂：

- `x = 2cosα, y = sinα`

像是在做参数化。

真正难的是：

- 这种理解怎么变成稳定、可复用、可评测、可审核的检索信号

### 判断 3：纯 embedding 不是最合适的一号方案

对于数学公式密集场景，embedding 更适合做：

- 候选集内精排
- 兜底排序

而不适合独自承担“方法识别 + 全库主召回”。

### 判断 4：纯 LLM 检索也不应该直接上

如果改成“让 LLM 自己理解学生上传上下文，然后分级翻目录 pull 出知识卡片”，方向是有价值的，但不能变成“LLM 直接自由选卡”。

更稳的理解应是：

**LLM 负责语义理解与路由，目录/索引负责确定性落卡。**

---

## 目前浮现出的两条可行路线

### 路线 A：继续做当前混合 RAG，但补上“公式感知层”

思路：

- 保留 `CardRetriever`
- 保留结构化召回 + embedding 精排
- 但 query signal 不再只靠自由文本 tags
- 在 Grader 前或 Grader 旁边，加一层“公式模式抽取 / 方法归因”

这一层可能输出：

- `symbolic_features`
  - 例如：`["x=a cos t", "y=b sin t"]`
- `problem_signals`
  - 例如：`["analytic_geometry", "ellipse"]`
- `method_tags`
  - 例如：`["椭圆参数方程法", "参数化设点"]`
- `student_approach_summary`
  - 例如：`"通过椭圆参数化绕开设点联立，直接利用参数关系挖掘隐含条件"`

然后检索优先级变为：

1. `symbolic_features + problem_signals` 做规则/结构化召回  
2. `method_tags` 做 alias / concept 命中  
3. `student_approach_summary` 做 embedding 兜底

优点：

- 延续当前文档体系
- 对现有 `CardRetriever` 改动相对可控
- 评测口径更清晰

问题：

- 需要额外建“公式模式识别”层
- 实现复杂度高于最初设想
- 仍然需要设计好 method taxonomy 和 alias

### 路线 B：改成“LLM 语义路由 + 目录式知识检索”

这是当前讨论中更接近直觉、也更贴近数学教学资料组织方式的一条路。

思路：

- 不把重点放在向量库“搜相似文本”
- 让 LLM 先理解：
  - 当前题目是什么
  - 学生到底用了什么方法
  - 这个方法属于哪个章节/子主题/方法类目
- 然后系统按目录/索引逐级 pull 对应知识卡

更理想的输出不是 `card_id`，而是一个“检索意图 JSON”：

```json
{
  "chapter": "解析几何",
  "topic": "椭圆",
  "method_candidates": ["参数方程法", "参数化设点", "圆锥曲线参数化"],
  "symbolic_patterns": ["x=a cos t", "y=b sin t"],
  "confidence": 0.82
}
```

然后由目录化检索器做：

- 先按 `chapter`
- 再按 `topic`
- 再按 `method_candidates`
- 再结合 alias / 公式模式 / 卡片摘要

去稳定选出 top `k` 张卡。

优点：

- 更贴近数学知识库的真实组织方式
- 对公式主导场景更友好
- 第一版可以不急着引入复杂向量库

问题：

- 需要知识库有足够清晰的目录和别名体系
- 需要定义 LLM 输出的检索意图 schema
- 如果目录组织不稳，LLM 路由也会漂

---

## 当前更倾向的方向

在当前讨论里，更自然的倾向是：

**优先考虑路线 B：LLM 语义路由 + 目录式知识检索。**

但要明确一点：

这并不等于“纯 LLM 检索”。

更准确的说法应该是：

- LLM 负责理解学生上传上下文“在做什么”
- LLM 输出检索意图，而不是直接输出最终卡片 ID
- Catalog / Index Retriever 负责沿目录/索引分层 pull 卡
- 如果 pull 不到或置信度低，再进入 `rag_audit_task`

也就是说，系统仍然需要一个确定性的知识层，只是把“主召回中心”从 embedding 转向了“LLM 路由 + 目录索引”。

---

## 如果走路线 B，需要补齐什么

### 1. 不要把这件事硬塞进现有 Grader

当前 `Grader` 的职责是：

- 正误判断
- 错误类型判断
- 替代方法检测

它更像判题器，不像检索路由器。

如果走路线 B，更合理的做法是：

- 保留 `Grader`
- 新增一个 `RetrievalRouter` / `MethodRouter`

让这个新模块专门负责：

- 读题目
- 读学生作答
- 理解数学公式结构
- 输出检索意图

### 2. 知识库需要目录化/索引化

如果要“分级翻目录 pull 卡”，那目录本身就必须是清晰的：

- 章节
- 子主题
- 方法类目
- alias
- 公式模式
- prerequisite / related cards

否则 LLM 虽然知道“像参数方程法”，但系统没有稳定落点。

### 3. 需要定义检索意图 schema

至少要明确这些字段是否存在：

- `chapter`
- `topic`
- `method_candidates`
- `symbolic_patterns`
- `concept_candidates`
- `confidence`
- `fallback_query`

### 4. 需要定义低置信度和失败处理

不能因为 LLM 看起来“像懂了”，就默认检索成功。

必须有：

- 低置信度阈值
- 空结果 fallback
- `rag_audit_task`
- 人工审计闭环

---

## 当前尚未定案的问题

以下问题目前都没有完全想清楚，因此不应在主方案文档里直接写死：

1. 公式模式识别要不要单独做成一个轻量 parser / recognizer
2. 路线 B 是否还需要 embedding 作为候选集内兜底精排
3. 目录化知识库的组织轴究竟是“章节优先”还是“方法优先”
4. 一张知识卡是更偏“concept”，还是更偏“method template”
5. 对于完全新方法，是补 alias / 补 link 就够，还是需要新建方法卡
6. `Grader`、`PathEvaluator`、`RetrievalRouter` 三者的职责边界怎么切

---

## 定案结论（2026-03-24 更新）

路线选择已定案：**路线 B — LLM 语义路由 + 目录式知识检索**，并进一步发展为**二阶段 LLM 检索**方案。

### 已落地的实现

1. **MethodRouter**（第 1 轮 LLM）：从方法目录闭合菜单中选 slot，不让 LLM 自由生成 tags
2. **确定性查表**：slot_id → card_ids，完全确定性，无 embedding
3. **CardSelector**（第 2 轮 LLM）：从候选卡摘要中精选 1-3 张，可主动输出 `additional_need`
4. **Embedding fallback**：仅在 MethodRouter 低置信或 CardSelector 发现候选不足时触发
5. **审计闭环**：低置信度时自动收集 `RagAuditEntry`，为后续人工补目录提供信号

### 对本文档六个未决问题的回应

1. **公式模式识别是否需要单独 parser** — 暂不需要。MethodRouter 的 trigger 描述已够 LLM 判断 `cos/sin 参数化` 等模式。
2. **路线 B 是否还需要 embedding** — 是的，但仅做 fallback 兜底，不做主召回。
3. **目录组织轴** — 章节 → topic → method slot，章节优先。
4. **知识卡是 concept 还是 method template** — 当前不区分，由 slot 的 card_ids 挂载决定。Phase D/E 引入 concept 层后再分化。
5. **新方法处理** — 通过 `RagAuditEntry` → 人工审批 → 新增 method slot 扩展目录。
6. **Grader/PathEvaluator/MethodRouter 边界** — Grader 只判题，PathEvaluator 只评估方法有效性，MethodRouter 只做检索路由，三者完全独立。

详细设计见 `plan.md`（二阶段检索架构章节）。
