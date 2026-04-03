# 基石概念层（方案 D）— 已实施

> 状态：已完成（2026-04-02）

## 背景

Pipeline v2 的《因式分解技巧》304 张卡片审计发现 41% 零前置依赖，根因：67.7% 的 `requires_concepts` 指向书外基础知识（共 272 个去重外部概念），本书无卡片 teaches 这些概念。

## 方案选型

经过 A/B/C/D 四种架构对比（详见 `doc/concept_topology_options.md`），选定：
- **方案 D**：per-book 内部图 + 基石概念聚类 + 跨书懒桥接
- **D→C 演进路径**：D 积累 foundation concepts + bridge 边 → 密度足够时形式化为全局概念 DAG

## 已实施内容

### 数据结构
- `DraftCard` 新增 `assumed_knowledge: list[str]` 字段（foundation concept IDs）
- 新增 `FoundationConcept` dataclass：`concept_id` / `name` / `covers` / `description` / `difficulty` / `source_book`

### 独立 Pass
- `foundation` 命令独立于 Pass 3，只花一次 LLM 调用
- 流程：检测 `requires_concepts - all_taught` → 272 个外部概念直接送 LLM 语义分组 → 30 个基石概念 → 回填 264/304 张卡片的 `assumed_knowledge`
- 预聚类方案（bigram Jaccard / 子串合并）经实验证明不如直接 LLM 分组，已删除

### 孤儿认领（附带修复）
- Prompt：要求显式声明 `parent_local_ref`（填 anchor ref 或填 null，不允许遗漏）
- 代码：Step 3.5 孤儿认领，按 concept overlap 匹配同 section anchor，score ≥ 1 才认领

### 方案 C 接口
- `covers` 字段：支持跨书匹配（`bookB.foundation.covers ∩ bookA.cards.teaches_concepts`）
- `source_book` 字段：追踪概念来源

## 关键文件
- `agent/knowledge/pdf_pipeline/data_structures.py` — FoundationConcept + assumed_knowledge
- `agent/knowledge/pdf_pipeline/relationship_builder.py` — `build_foundation_concepts()` + `_build_foundation_layer()`
- `agent/knowledge/pdf_pipeline/draft_store.py` — `save/load_foundation_concepts()`
- `agent/knowledge/pdf_pipeline/pipeline_runner.py` — `run_foundation()`
- `agent/knowledge/pdf_pipeline/prompts/zh/relationship_builder.yaml` — `foundation_grouper_system/user`
- `agent/knowledge/pdf_pipeline/card_generator.py` — Step 3.5 孤儿认领
- `agent/knowledge/pdf_pipeline/prompts/zh/card_generator.yaml` — 显式声明约束
- `tools/pdf_pipeline_cli.py` — `foundation` 子命令

## 产出
- `content/drafts/因式分解技巧/foundation_concepts.yaml` — 30 个基石概念
- 304 张卡片 YAML 含 `assumed_knowledge` 字段
