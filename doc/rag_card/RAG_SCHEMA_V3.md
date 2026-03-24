# RAG Schema v3

更新时间：2026-03-23

状态：**执行基线**

关联文档：
- `RAG_PLANNING_3.md` — 总纲与实施顺序
- `DATA_MODEL_CONVERGENCE.md` — 数据语义与边界约束
- `MASTERY_TRACKING_DESIGN.md` — mastery 读写与迁移规则
- `AUDIT_OPERATIONS_DESIGN.md` — 审计任务、proposal 与发布流
- `KNOWLEDGE_API_CONTRACT.md` — `CardStore` / `CardRetriever` / Worker 接口契约

---

## 本文档回答的问题

**最终应该建什么表、保留什么内容源结构、哪些约束必须落在 schema 上。**

如果本文件与 `RAG_DATA_MODEL_DESIGN.md` 或旧版 planning 文档冲突，**以本文件为准**。

---

## 1. 内容源目录（authoring / ingest 输入）

```text
content/
  questions/
    <question_id>.json
  knowledge_cards/
    <card_id>.json
  concepts/
    <concept_id>.json
```

### 1.1 Question JSON（最小必填）

| 字段 | 类型 | 说明 |
|---|---|---|
| `question_id` | `str` | 题目主键 |
| `question_type` | `subjective/fill_blank/single_choice/multi_choice` | 题型 |
| `chapter` | `str` | 章节 |
| `difficulty` | `1-5` | 难度 |
| `stem` | `str` | 题干 |
| `answer_schema` | `object` | 标准答案结构 |
| `question_cards` | `list` | 训练目标/背景/前置卡片 |
| `solutions` | `list` | 方法分叉 |
| `version` | `str` | 内容版本 |

约束：

- `question_cards[].relation ∈ {target, background, prereq}`
- `solutions[]` 中至少一条 `is_standard=true`
- `solutions[].reference_cards[]` 为 `solution_card_link` 的内容源
- `solutions[].slices[]` 为运行时步骤与提示的内容源

### 1.2 KnowledgeCard JSON（最小必填）

| 字段 | 类型 | 说明 |
|---|---|---|
| `card_id` | `str` | 卡片主键 |
| `chapter` | `str` | 章节 |
| `title` | `str` | 卡片标题 |
| `summary` | `str` | 摘要 |
| `general_methods` | `list[str]` | 通性通法 |
| `hints_general` | `object` | `l1/l2/l3` 概念级提示 |
| `common_mistakes` | `list[str]` | 常见误区 |
| `prerequisite_card_ids` | `list[str]` | 前置卡 |
| `problem_tags` | `list[str]` | 题型层标签 |
| `method_tags` | `list[str]` | 方法层标签 |
| `thinking_tags` | `list[str]` | 思想层标签 |
| `version` | `str` | 内容版本 |

约束：

- `KnowledgeCard.hints` 只承载概念级提示，不混入 slice hint
- `problem_tags/method_tags/thinking_tags` 仅作为冷启动与教研编辑辅助，不直接替代正式 `concept_link`

### 1.3 Concept JSON（最小必填）

| 字段 | 类型 | 说明 |
|---|---|---|
| `concept_id` | `str` | 稳定主键，不可重编码 |
| `layer` | `problem/method/thinking` | concept 层级 |
| `name` | `str` | 显示名 |
| `status` | `shadow/active/deprecated` | 发布状态 |
| `source` | `seed/author/audit` | 来源 |
| `aliases` | `list[object]` | ingest 后展开为 `concept_alias` |

说明：

- Phase D 只要求最小壳模型；`subject/chapter/canonical_name/tree_path` 在 Phase G 扩展
- `aliases[]` 是内容源便捷写法，正式索引层仍落在独立 `concept_alias` 表

---

## 2. Canonical SQL Schema

> 目标：给迁移脚本、CardStore、Audit Worker 和离线评测提供统一的最终表结构。

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE question_type_enum AS ENUM (
  'subjective',
  'fill_blank',
  'single_choice',
  'multi_choice'
);

CREATE TYPE question_card_relation_enum AS ENUM (
  'target',
  'background',
  'prereq'
);

CREATE TYPE solution_card_relation_enum AS ENUM (
  'primary',
  'support',
  'prereq',
  'contrast'
);

CREATE TYPE slice_edge_type_enum AS ENUM (
  'next',
  'branch',
  'remedial',
  'retry',
  'merge'
);

CREATE TYPE published_source_enum AS ENUM (
  'seed',
  'author',
  'audit'
);

CREATE TYPE record_status_enum AS ENUM (
  'active',
  'deprecated'
);

CREATE TYPE concept_layer_enum AS ENUM (
  'problem',
  'method',
  'thinking'
);

CREATE TYPE concept_status_enum AS ENUM (
  'shadow',
  'active',
  'deprecated'
);

CREATE TYPE alias_status_enum AS ENUM (
  'active',
  'deprecated'
);

CREATE TYPE solution_index_status_enum AS ENUM (
  'pending',
  'ready',
  'rejected'
);

CREATE TYPE audit_task_type_enum AS ENUM (
  'solution_card_index',
  'new_method_rag',
  'concept_link_missing'
);

CREATE TYPE audit_status_enum AS ENUM (
  'pending',
  'proposed',
  'approved',
  'rejected',
  'done'
);
```

### 2.1 发布内容表

```sql
CREATE TABLE knowledge_card (
  card_id TEXT PRIMARY KEY,
  chapter TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  general_methods JSONB NOT NULL DEFAULT '[]'::jsonb,
  common_mistakes JSONB NOT NULL DEFAULT '[]'::jsonb,
  prerequisite_card_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  problem_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  method_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  thinking_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  difficulty_min SMALLINT NOT NULL DEFAULT 1 CHECK (difficulty_min BETWEEN 1 AND 5),
  difficulty_max SMALLINT NOT NULL DEFAULT 5 CHECK (difficulty_max BETWEEN 1 AND 5),
  status record_status_enum NOT NULL DEFAULT 'active',
  source published_source_enum NOT NULL DEFAULT 'author',
  version TEXT NOT NULL DEFAULT 'v1',
  source_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE knowledge_card_hint (
  card_id TEXT NOT NULL REFERENCES knowledge_card(card_id) ON DELETE CASCADE,
  hint_level SMALLINT NOT NULL CHECK (hint_level BETWEEN 1 AND 3),
  hint_text TEXT NOT NULL,
  PRIMARY KEY (card_id, hint_level)
);

CREATE TABLE question (
  question_id TEXT PRIMARY KEY,
  question_type question_type_enum NOT NULL,
  chapter TEXT NOT NULL,
  difficulty SMALLINT NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
  stem TEXT NOT NULL,
  answer_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
  options JSONB NOT NULL DEFAULT '[]'::jsonb,
  status record_status_enum NOT NULL DEFAULT 'active',
  source published_source_enum NOT NULL DEFAULT 'author',
  version TEXT NOT NULL DEFAULT 'v1',
  source_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE question_card_link (
  question_id TEXT NOT NULL REFERENCES question(question_id) ON DELETE CASCADE,
  card_id TEXT NOT NULL REFERENCES knowledge_card(card_id) ON DELETE RESTRICT,
  relation question_card_relation_enum NOT NULL,
  weight NUMERIC(4,3) NOT NULL DEFAULT 1.0 CHECK (weight > 0 AND weight <= 1),
  status record_status_enum NOT NULL DEFAULT 'active',
  PRIMARY KEY (question_id, card_id, relation)
);

CREATE TABLE solution (
  solution_id TEXT PRIMARY KEY,
  question_id TEXT NOT NULL REFERENCES question(question_id) ON DELETE CASCADE,
  method_name TEXT NOT NULL,
  is_standard BOOLEAN NOT NULL DEFAULT FALSE,
  entry_slice_id TEXT NULL,
  status record_status_enum NOT NULL DEFAULT 'active',
  source published_source_enum NOT NULL DEFAULT 'author',
  source_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE solution_card_link (
  solution_id TEXT NOT NULL REFERENCES solution(solution_id) ON DELETE CASCADE,
  card_id TEXT NOT NULL REFERENCES knowledge_card(card_id) ON DELETE RESTRICT,
  relation solution_card_relation_enum NOT NULL,
  weight NUMERIC(4,3) NOT NULL DEFAULT 1.0 CHECK (weight > 0 AND weight <= 1),
  source published_source_enum NOT NULL DEFAULT 'author',
  status record_status_enum NOT NULL DEFAULT 'active',
  PRIMARY KEY (solution_id, card_id, relation)
);

CREATE TABLE slice (
  slice_id TEXT PRIMARY KEY,
  solution_id TEXT NOT NULL REFERENCES solution(solution_id) ON DELETE CASCADE,
  step_order INT NOT NULL CHECK (step_order >= 1),
  title TEXT NOT NULL,
  expected_outcome TEXT NOT NULL DEFAULT '',
  score_weight NUMERIC(5,2) NOT NULL DEFAULT 0 CHECK (score_weight >= 0),
  rubric JSONB NOT NULL DEFAULT '{}'::jsonb,
  common_failures JSONB NOT NULL DEFAULT '[]'::jsonb,
  UNIQUE (solution_id, step_order)
);

CREATE TABLE slice_hint (
  slice_id TEXT NOT NULL REFERENCES slice(slice_id) ON DELETE CASCADE,
  hint_level SMALLINT NOT NULL CHECK (hint_level BETWEEN 1 AND 3),
  hint_text TEXT NOT NULL,
  PRIMARY KEY (slice_id, hint_level)
);

CREATE TABLE slice_edge (
  solution_id TEXT NOT NULL REFERENCES solution(solution_id) ON DELETE CASCADE,
  from_slice_id TEXT NOT NULL REFERENCES slice(slice_id) ON DELETE CASCADE,
  to_slice_id TEXT NOT NULL REFERENCES slice(slice_id) ON DELETE CASCADE,
  edge_type slice_edge_type_enum NOT NULL DEFAULT 'next',
  condition_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  priority SMALLINT NOT NULL DEFAULT 100,
  PRIMARY KEY (solution_id, from_slice_id, to_slice_id, edge_type),
  CHECK (from_slice_id <> to_slice_id)
);

ALTER TABLE solution
  ADD CONSTRAINT fk_solution_entry_slice
  FOREIGN KEY (entry_slice_id) REFERENCES slice(slice_id)
  DEFERRABLE INITIALLY DEFERRED;
```

### 2.2 Concept / alias / card-link 表

```sql
CREATE TABLE concept (
  concept_id TEXT PRIMARY KEY,
  layer concept_layer_enum NOT NULL,
  name TEXT NOT NULL,
  chapter TEXT NULL,
  canonical_name TEXT NULL,
  subject TEXT NULL,
  tree_path TEXT NULL,
  status concept_status_enum NOT NULL DEFAULT 'shadow',
  source published_source_enum NOT NULL DEFAULT 'seed',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE concept_alias (
  alias TEXT NOT NULL,
  concept_id TEXT NOT NULL REFERENCES concept(concept_id) ON DELETE CASCADE,
  layer concept_layer_enum NOT NULL,
  chapter TEXT NULL,
  confidence NUMERIC(3,2) NOT NULL DEFAULT 0.80 CHECK (confidence >= 0 AND confidence <= 1),
  source published_source_enum NOT NULL DEFAULT 'seed',
  status alias_status_enum NOT NULL DEFAULT 'active',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (alias, concept_id)
);

CREATE TABLE knowledge_card_concept_link (
  card_id TEXT NOT NULL REFERENCES knowledge_card(card_id) ON DELETE CASCADE,
  concept_id TEXT NOT NULL REFERENCES concept(concept_id) ON DELETE CASCADE,
  layer concept_layer_enum NOT NULL,
  weight NUMERIC(4,3) NOT NULL DEFAULT 1.0 CHECK (weight > 0 AND weight <= 1),
  is_primary BOOLEAN NOT NULL DEFAULT FALSE,
  source published_source_enum NOT NULL DEFAULT 'author',
  status record_status_enum NOT NULL DEFAULT 'active',
  PRIMARY KEY (card_id, concept_id, layer)
);
```

### 2.3 运行时 / 审计 / mastery 表

```sql
CREATE TABLE student_concept_mastery (
  student_id TEXT NOT NULL,
  concept_id TEXT NOT NULL REFERENCES concept(concept_id) ON DELETE CASCADE,
  level NUMERIC(4,3) NOT NULL DEFAULT 0.30 CHECK (level >= 0 AND level <= 1),
  last_practiced TIMESTAMPTZ NOT NULL DEFAULT now(),
  next_review_at TIMESTAMPTZ NULL,
  practice_count INT NOT NULL DEFAULT 0,
  error_count INT NOT NULL DEFAULT 0,
  consecutive_correct INT NOT NULL DEFAULT 0,
  PRIMARY KEY (student_id, concept_id)
);

CREATE TABLE student_solution_mastery (
  student_id TEXT NOT NULL,
  solution_id TEXT NOT NULL REFERENCES solution(solution_id) ON DELETE CASCADE,
  question_id TEXT NOT NULL REFERENCES question(question_id) ON DELETE CASCADE,
  method_name TEXT NOT NULL,
  level NUMERIC(4,3) NOT NULL DEFAULT 0.30 CHECK (level >= 0 AND level <= 1),
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  use_count INT NOT NULL DEFAULT 0,
  linked_concepts JSONB NOT NULL DEFAULT '[]'::jsonb,
  last_outcome TEXT NOT NULL DEFAULT '',
  index_status solution_index_status_enum NOT NULL DEFAULT 'pending',
  PRIMARY KEY (student_id, solution_id)
);

CREATE TABLE rag_audit_task (
  task_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dedupe_key TEXT NOT NULL UNIQUE,
  task_type audit_task_type_enum NOT NULL,
  status audit_status_enum NOT NULL DEFAULT 'pending',
  student_id TEXT NULL,
  source_session_id TEXT NOT NULL,
  question_id TEXT NULL REFERENCES question(question_id) ON DELETE SET NULL,
  solution_id TEXT NULL REFERENCES solution(solution_id) ON DELETE SET NULL,
  solution_method TEXT NULL,
  chapter TEXT NULL,
  question_target_card_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  observed_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  reason TEXT NOT NULL DEFAULT '',
  proposal_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  decision_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
  attempts INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ NULL,
  published_at TIMESTAMPTZ NULL
);
```

---

## 3. 必须落在 schema 上的约束

```sql
CREATE UNIQUE INDEX uq_card_primary_concept_per_layer
  ON knowledge_card_concept_link(card_id, layer)
  WHERE is_primary = TRUE AND status = 'active';

CREATE INDEX idx_question_chapter_difficulty
  ON question(chapter, difficulty)
  WHERE status = 'active';

CREATE INDEX idx_question_card_relation
  ON question_card_link(question_id, relation, weight DESC)
  WHERE status = 'active';

CREATE INDEX idx_solution_question_standard
  ON solution(question_id, is_standard, updated_at DESC)
  WHERE status = 'active';

CREATE INDEX idx_solution_card_relation
  ON solution_card_link(solution_id, relation, weight DESC)
  WHERE status = 'active';

CREATE INDEX idx_slice_solution_order
  ON slice(solution_id, step_order);

CREATE INDEX idx_slice_edge_from
  ON slice_edge(solution_id, from_slice_id, priority);

CREATE INDEX idx_concept_alias_lookup
  ON concept_alias(alias, layer, chapter, confidence DESC)
  WHERE status = 'active';

CREATE INDEX idx_card_concept_lookup
  ON knowledge_card_concept_link(card_id, layer, is_primary, weight DESC)
  WHERE status = 'active';

CREATE INDEX idx_concept_mastery_review
  ON student_concept_mastery(student_id, next_review_at);

CREATE INDEX idx_solution_mastery_student
  ON student_solution_mastery(student_id, last_used_at DESC);

CREATE INDEX idx_audit_queue
  ON rag_audit_task(status, task_type, created_at);
```

---

## 4. 兼容与淘汰策略

- `student_card_mastery` 仅允许作为 legacy 归档快照保留，不再作为 v3 主路径 schema 的 canonical 表。
- `SemanticMemory.pending_audit_tasks` 只保留摘要镜像；权威任务源必须迁到 `rag_audit_task`。
- `solution_id` / `concept_id` 一旦发布，不允许重编码；merge / split 仅允许通过 lineage 流程处理。
- `RAG_DATA_MODEL_DESIGN.md` 仅作为历史草案归档，不再承载最终 DDL。

---

## 5. 实施优先级（按最小可落地顺序）

1. Phase B：`knowledge_card` / `knowledge_card_hint` / `question` / `question_card_link` / `solution` / `slice` / `slice_hint`
2. Phase C：`solution_card_link` / `slice_edge`
3. Phase D：`concept` / `concept_alias` / `knowledge_card_concept_link` / `rag_audit_task`
4. Phase E：`student_concept_mastery` / `student_solution_mastery`
5. Phase G/H：lineage / migration /看板相关附加表

---

## 6. 非目标

- 本文档不定义 `CardStore` / `CardRetriever` 的 Python 接口；见 `KNOWLEDGE_API_CONTRACT.md`
- 本文档不定义离线评测脚本和灰度门禁；见 `RAG_EVAL_AND_GATING.md`
- 本文档不重复解释每个字段为什么存在；原因见 `DATA_MODEL_CONVERGENCE.md`
