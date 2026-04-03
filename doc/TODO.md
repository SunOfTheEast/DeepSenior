# DeepSenior 待办清单

> 更新时间：2026-04-03

## 高优先级

- [ ] **题目-卡片绑定（question_cards）**：DraftQuestion 目前无 tags 字段，推荐系统的 tags 查询依赖此绑定。需用 applies-to 映射做召回 + LLM 确认。
- [ ] **EmbeddingCardIndex 接入 pipeline**：已实现但未替换 SimpleCardIndex。需在 card_retriever 或 tutor_manager 初始化时切换。
- [ ] **Socratic agent prompt 加 {card_menu}**：context_builder 已注入 card_menu，但 Socratic prompt 模板未引用，对话引导仍无卡片感知。

## 中优先级

- [ ] **Think→Act tool 循环全路径启用**：search_knowledge/get_similar_problem 目前只在 ActAgent 条件触发，Socratic 路径未启用。
- [ ] **Pass 2c JSON 解析失败（4 sections）**：LLM 输出的 LaTeX 反斜杠未正确转义，需在 `_extract_json` 中加转义预处理。
- [ ] **applies-to 关系显式化**：当前作为 tag_clusters.yaml 子字段，未独立建模。需评估是否抽为独立层。

## 低优先级

- [ ] **6 张 Type B parentless leaves 验证**：孤儿认领代码已写，需重跑 Pass 2b 验证效果。
- [ ] **"分解特征" cluster 混入方法名**：源头 problem_tags 混了方法名，需 card_generator prompt 迭代。
- [ ] **69 张卡无 cluster**：raw tags 全低频，可接受，通过章节/公式路径召回。

## 延后（等条件成熟）

- [ ] **cluster 名跨 run 不稳定**：等权威书（如《新高考数学你真的掌握了吗》）建立基准词汇表后解决。
- [ ] **类型统一 KnowledgeCard → PublishedKnowledgeCard**：SYSTEM_DOCS §5.12.8.6 有三阶段迁移设计，待 RAG 消费层稳定后实施。
- [ ] **全局废话标签库（content/noise_tags.yaml）**：等多本书积累后建立跨书废话库，反馈 card_generator prompt。
- [ ] **概念拓扑显式化（is-a / prerequisite / applies-to）**：三种边关系隐含在卡片数据中，等多书数据积累后统一建模。
- [ ] **习题集适配（培优新方法等）**：Pass 2c 需分支处理，答案走分级策略（轻量模型→强模型 fallback）。

## 已完成

- [x] 基石概念层（方案 D）— 30 个 foundation concepts，264 张卡回填 assumed_knowledge
- [x] 孤儿认领代码 — prompt 显式声明 + Step 3.5 concept overlap 匹配
- [x] Pass 2c 题目提取 — 93 archetype + 176 exercise = 269 题
- [x] Tag clustering + 废话过滤 — 白名单过滤 + 三维独立聚类 + applies-to 映射
- [x] search_knowledge 增强 — query 搜索模式 + card_ids 展开模式
- [x] get_similar_problem tool — DraftQuestionBank 适配
- [x] L0 card menu 注入 context_builder — 所有 agent 可见
- [x] EmbeddingCardIndex — ZhipuAI embedding-3 语义搜索，缓存到磁盘
- [x] card_generator prompt 动态反馈 — filtered_tags 注入坏标签示例
