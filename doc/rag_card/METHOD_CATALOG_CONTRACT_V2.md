# Method Catalog Contract v2

更新时间：2026-03-23

状态：**执行基线（v2）**

关联文档：
- `KNOWLEDGE_API_CONTRACT_V2.md` — 在线运行时接口与 `MethodCatalog` 读路径
- `RAG_SCHEMA_V3.md` — 正式发布态 schema；本文件不强行升 schema 版本
- `AUDIT_OPERATIONS_DESIGN.md` — `rag_audit_task` proposal / 审批 / 回滚
- `plan.md` — 二阶段检索入口摘要

> 说明：本文件只定义 **Method Catalog 的目录结构、字段契约与发布规则**。它是 `RAG_SCHEMA_V3.md` 之外的 authoring / ingest 扩展，不替代正式 DB schema。

---

## 本文档回答的问题

**`content/method_catalog/` 应该长什么样、运行时如何用它定位 topic 和 slot、audit 怎样补目录。**

---

## 1. 目录结构

```text
content/
  method_catalog/
    _question_topics.yaml
    _cross_topic.yaml
    <chapter>/
      <topic>.yaml
```

目录职责：

- `_question_topics.yaml`
  - 负责 `question_id -> primary_topic` 解析
- `_cross_topic.yaml`
  - 维护跨章节公共方法池
- `<chapter>/<topic>.yaml`
  - 维护该 topic 下的本章方法菜单

运行时不直接读 authoring 文件，而是读对应的 **published snapshot**。authoring / snapshot 的发布流程由 ingest 决定，但在线契约始终视其为只读。

---

## 2. `question -> topic` 映射契约

推荐格式：

```yaml
version: "2026-03-23"
questions:
  - question_id: analytic_ellipse_001
    chapter: 解析几何
    primary_topic: 椭圆
    fallback_topics: [圆锥曲线通法]

  - question_id: analytic_hyperbola_003
    chapter: 解析几何
    primary_topic: 双曲线
    fallback_topics: [圆锥曲线通法]
```

约束：

- `question_id` 唯一
- `chapter` 必须与题目发布态元信息一致
- `primary_topic` 是 v2 主路径必填
- `fallback_topics` 可为空，最多 3 个

运行时语义：

- `MethodCatalog.resolve_topic()` 优先读取 `primary_topic`
- 若映射缺失，允许 `topic=None` 退化运行，但必须产生 `missing_topic_mapping` 告警，并具备 audit 入队能力

---

## 3. Topic 文件契约

推荐格式：

```yaml
version: "2026-03-23"
chapter: 解析几何
topic: 椭圆
status: active
methods:
  - slot_id: ellipse_parametric
    name: 椭圆参数方程法
    trigger: 学生用 cos/sin 参数化设点，绕开联立方程组
    card_ids: [card_0047, card_0048]
    cross_ref: [trig_substitution]
    status: active

  - slot_id: ellipse_set_and_solve
    name: 设点联立法
    trigger: 学生设点代入曲线方程，联立方程组求解
    card_ids: [card_0052, card_0053, card_0054]
    cross_ref: []
    status: active
```

字段语义：

- `version`
  - 内容版本，用于发布与回滚
- `chapter`
  - 章节名，必须与目录层级一致
- `topic`
  - 子主题名，必须与文件名一致
- `status`
  - `active | shadow | deprecated`
- `methods`
  - 方法 slot 列表

---

## 4. Method Slot 契约

每个 `MethodSlot` 固定字段：

- `slot_id`
  - 全局唯一、稳定、`snake_case`
- `name`
  - 教师可读的显示名
- `trigger`
  - 写给 LLM 的一行判断提示
- `card_ids`
  - 与该方法直接关联的已发布知识卡
- `cross_ref`
  - 0-3 个相关 slot，用于中低置信度扩圈
- `status`
  - `active | shadow | deprecated`
- `notes`
  - 可选，供教研或 audit 备注；默认不直接展示给 LLM

硬约束：

- 每个 topic 的 `active` slot 通常保持在 `5-15` 个
- `_cross_topic.yaml` 的 `active` slot 通常保持在 `10-15` 个
- 单个 slot 的 `card_ids` 建议 `1-8` 张
- 单个 slot 的 `cross_ref` 建议 `0-3` 个
- `trigger` 应优先描述**学生作答时可观察到的表现**，而不是教科书定义

好的 `trigger` 示例：

- `学生用 cos/sin 参数化设点，绕开联立方程组`
- `学生先取斜率 k=y/x，再把几何关系转成一元代数`
- `学生直接改用向量点积/模长关系处理几何量`

不好的 `trigger` 示例：

- `一种重要的数学思想`
- `请回忆本节内容`
- `本题可用多种方法处理`

---

## 5. `_cross_topic.yaml` 契约

推荐格式：

```yaml
version: "2026-03-23"
chapter: _cross_topic
topic: 公共方法
status: active
methods:
  - slot_id: trig_substitution
    name: 三角代换
    trigger: 学生用 sin/cos/tan 替换变量简化表达式
    card_ids: [card_0201]
    cross_ref: []
    status: active

  - slot_id: vector_method
    name: 向量法
    trigger: 学生建系后用向量运算处理几何关系
    card_ids: [card_0202, card_0203]
    cross_ref: []
    status: active
```

用途：

- 处理跨章节方法
- 处理 topic 文件还未覆盖的高频共性方法
- 给 `MethodRouter` 提供统一的后备菜单

---

## 6. 运行时展示规则

给 `MethodRouter` 的菜单展示格式应稳定为：

```text
slot_id | name | trigger
```

说明：

- 不向 `MethodRouter` 暴露 `notes`
- 默认不向 `MethodRouter` 直接暴露整张卡片全文
- `shadow` / `deprecated` slot 不进入在线菜单

---

## 7. 发布态与兼容边界

- `MethodCatalog` 是 authoring / ingest 扩展，不要求把 slot 本身写进 `RAG_SCHEMA_V3.md` 的 SQL schema
- 在线只读取 published snapshot，不读取未发布 authoring 目录
- `question_id -> topic` 映射缺失时，允许在线退化，但不得静默吞掉该问题
- 若 topic 文件存在但 slot 覆盖不足，允许 fallback 与 audit；不得在线直接新增正式 slot

---

## 8. Audit 如何补 slot

`rag_audit_task.proposal_payload` 中允许携带：

```json
{
  "proposed_slot": {
    "chapter": "解析几何",
    "topic": "椭圆",
    "slot_id": "ellipse_parametric",
    "name": "椭圆参数方程法",
    "trigger": "学生用 cos/sin 参数化设点，绕开联立方程组",
    "card_ids": ["card_0047", "card_0048"],
    "cross_ref": ["trig_substitution"],
    "status": "shadow"
  }
}
```

规则：

- audit 只能产 proposal，不能直接发布 `active` slot
- 新 slot 先以 `shadow` 进入审阅
- 人工审批通过后，才能进入 published snapshot

---

## 9. 最小验收

- `MethodCatalog.resolve_topic()` 能通过 `_question_topics.yaml` 稳定解析 topic
- `MethodRouter` 输入菜单时，每个 topic 的可见 slot 数量受控
- 同一个 `slot_id` 在全目录内全局唯一
- `trigger` 以学生可观察行为为中心，不退化成抽象教学口号
- audit proposal 能补 `slot_id / trigger / card_ids / cross_ref`

