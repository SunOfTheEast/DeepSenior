# DeepSenior 待办清单

> 更新时间：2026-04-04

## 高优先级

- [ ] **记忆检索（Memory Retrieval）**：当前只有 `get_recent_episodes(limit=10)` 按时间取最近 N 条。缺按 concept/method/error_type 针对性召回（如"学生上次做十字相乘法的情况"）。推荐系统和 Tutor 个性化引导的前置依赖。
- [ ] **记忆分层压缩（Memory Compression）**：Episodic memory 只增不减，线性膨胀。需要：老旧 episode 压缩为周/月摘要、相似 episode 合并为统计、profile_summary 定期重生成。否则远期记忆完全丢失。
- [ ] **推荐系统两路 Recall + Merge**：基于学生画像（weak_concepts/slots）+ 题目相似度（problem_type/method_type）的多路召回，DraftQuestionBank 加 cluster 维度查询。
- [ ] **实时绑卡（学生拍照/教师上传）**：无预绑定的新题走 EmbeddingCardIndex search → L0 菜单 → 对话中 search_knowledge 精修。

## 中优先级

- [ ] **Socratic/Act prompt 加 card_menu（仅新题场景）**：对无预绑定的题，LLM 需要看 card_menu 才能选卡。
- [ ] **Think→Act tool 循环全路径启用**：search_knowledge/get_similar_problem 目前只在 ActAgent 条件触发，Socratic 路径未启用。
- [ ] **Pass 2c JSON 解析失败（4 sections）**：LaTeX 反斜杠未正确转义，需在 `_extract_json` 中加转义预处理。
- [ ] **applies-to 关系显式化**：当前作为 tag_clusters.yaml 子字段，未独立建模。
- [ ] **Lesson 模块（自动备课）**：结合学生画像 + 知识图谱 + 推荐系统，自动选题组课 + 排序 + 教学设计。依赖推荐系统和记忆检索。

## 低优先级

- [ ] **6 张 Type B parentless leaves 验证**：孤儿认领代码已写，需重跑 Pass 2b 验证效果。
- [ ] **"分解特征" cluster 混入方法名**：源头 problem_tags 混了方法名，需 card_generator prompt 迭代。
- [ ] **69 张卡无 cluster**：raw tags 全低频，可接受，通过章节/公式路径召回。
- [ ] **3 道题缺 solution_paths**：有 bound_card_ids 兜底，SolverAgent 输出 JSON 解析失败。

## 延后（等条件成熟）

- [ ] **Benchmark 框架**：导数/解析几何压轴题测试，评估不同模型的解题正确率 + RAG 调用质量 + token 消耗。
- [ ] **cluster 名跨 run 不稳定**：等权威书建立基准词汇表后解决。
- [ ] **类型统一 KnowledgeCard → PublishedKnowledgeCard**：SYSTEM_DOCS §5.12.8.6 有三阶段迁移设计。
- [ ] **全局废话标签库（content/noise_tags.yaml）**：等多本书积累后建立。
- [ ] **概念拓扑显式化（is-a / prerequisite / applies-to）**：等多书数据积累后统一建模。
- [ ] **习题集适配（培优新方法等）**：Pass 2c 需分支处理，答案走分级策略。

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
- [x] SolverAgent — 解题 + RAG tool calling + 绑卡副产品
- [x] 题目-卡片绑定批量执行 — 269/269 全部绑卡，avg 2.0 paths/题
- [x] DraftQuestion 加 solution_paths + bound_card_ids 字段
- [x] SolverAgent 接入 pipeline（run_solve + CLI solve 命令）
- [x] Solver fallback 链 — primary → fallback model 自动切换
- [x] 自动绑卡 — run_solve() 加入 run_full/run_from_pdf 全链路
- [x] DeepSeek V3.2 thinking + tools — reasoning_content 回传，extra_body 传参
- [x] EmbeddingCardIndex 接入 Tutor 运行时 — factory 自动选，SkillRegistry 暴露，TutorToolRegistry 接收
- [x] SolverAgent 绑卡 → Tutor session 桥接 — ProblemContext.bound_card_ids + card_preloader 优先加载
- [x] solution_paths 注入 Grader/Planner prompt — key_steps 对应 checkpoint 设计
