# 项目结构说明

根据当前目标结构整理的目录树。

```text
os_agent_memory/
│
├── api/                                      # HTTP 接口层
│   ├── server.py                             # FastAPI 入口，注册路由、中间件、异常处理
│   ├── schemas.py                            # API 请求/响应 Pydantic 模型
│   ├── mappers.py                            # API schema 与 core model 之间的转换
│   ├── routes_events.py                      # 事件接口：写入 Agent 原始事件
│   ├── routes_memories.py                    # 记忆接口：抽取、检索、提交、遗忘、配置
│   └── routes_eval.py                        # 评测接口：运行评测、查看报告、查看统计
│
├── core/                                     # 全局核心定义，Phase 0 优先冻结
│   ├── models.py                             # RawEvent / MemoryEvent / MemoryCandidate / MemoryRecord 等核心模型
│   ├── config.py                             # 全局配置：路径、阈值、top_k、置信度、检索参数
│   └── constants.py                          # 枚举常量：EventType、MemoryType、Scene、Status、Priority
│
├── ingestion/                                # 数据接入与标准化层
│   ├── collector.py                          # 接收 Agent / Mock Agent 提交的原始 payload
│   ├── adapter.py                            # RawEvent → MemoryEvent，统一字段格式
│   ├── cleaner.py                            # 数据清洗：去噪、去重、格式修正、字段补全
│   └── validator.py                          # 质量校验：必填字段、类型、时间戳、低质量输入拦截
│
├── security/                                 # 安全与隐私层
│   ├── sensitive_detector.py                 # 敏感信息识别：手机号、身份证、银行卡、Token、密钥
│   ├── sanitizer.py                          # 脱敏与过滤：写入前处理 content / raw_payload
│   └── audit.py                              # 安全审计：敏感信息拦截、遗忘操作、访问记录
│
├── extractors/                               # 记忆抽取层：MemoryEvent → MemoryCandidate
│   ├── preference_extractor.py               # 偏好抽取：显式偏好 + 隐式高频行为
│   ├── knowledge_extractor.py                # 知识抽取：工具结果、历史案例、可复用模板
│   ├── workflow_extractor.py                 # 工作流抽取：从任务轨迹和工具链中提炼流程
│   ├── environment_extractor.py              # 环境记忆：常用目录、系统配置、软件状态
│   └── tool_extractor.py                     # 工具记忆：工具成功率、失败原因、响应时间、适用场景
│
├── memory/                                   # 记忆管理层：MemoryCandidate → MemoryRecord
│   ├── store.py                              # SQLite CRUD：events / memories / history / forget_log
│   ├── version_manager.py                    # 版本管理：版本递增、旧版本归档、supersedes 关系
│   ├── conflict_resolver.py                  # 冲突处理：覆盖、合并、场景隔离、置信度比较
│   ├── lifecycle.py                          # 生命周期：pending / active / superseded / deleted / expired
│   └── forgetter.py                          # 精准遗忘：自然语言匹配、软删除、索引失效、审计日志
│
├── flow/                                     # 短期 / 中期 / 长期记忆流转层
│   ├── short_term.py                         # 短期记忆：当前任务上下文，内存缓存
│   ├── mid_term.py                           # 中期记忆：会话摘要、任务状态、项目阶段信息
│   ├── long_term.py                          # 长期记忆：稳定偏好、知识、工作流、案例、模板
│   └── promotion.py                          # 晋升规则：短期→中期→长期，降级、过期和归档
│
├── policy/                                   # 记忆调度策略层
│   ├── memory_policy.py                      # 决定何时读记忆、写事件、抽取记忆、触发遗忘
│   ├── retrieval_router.py                   # 根据 AgentState 生成 RetrievePlan，多路检索计划
│   └── context_budget.py                     # 上下文预算管理，触发摘要、flush、压缩前写入
│
├── retrieval/                                # 检索层
│   ├── keyword_retriever.py                  # BM25 / SQLite FTS 关键词检索
│   ├── vector_retriever.py                   # 向量检索入口，不直接绑定具体向量库
│   ├── hybrid_retriever.py                   # 混合检索：关键词 + 向量 + 置信度 + 时间衰减
│   └── reranker.py                           # 重排序：场景加权、类型加权、安全策略优先、Top-K 输出
│
├── embeddings/                               # 文本向量化层
│   ├── kylin_embedding.py                    # 银河麒麟 Embedding SDK 封装，生产环境使用
│   ├── mock_embedding.py                     # 本地开发 mock embedding
│   └── embedding_service.py                  # 统一接口：encode(texts) → vectors
│
├── vector_store/                             # 向量数据库适配层
│   ├── kylin_vector_store.py                 # 银河麒麟向量数据库 SDK，生产环境使用
│   ├── faiss_vector_store.py                 # FAISS 开发环境 / 对比实验使用
│   ├── mock_vector_store.py                  # 单元测试用 mock 向量库
│   └── vector_service.py                     # 统一接口：add / search / delete / update
│
├── evaluation/                               # 量化评测层
│   ├── preference_eval.py                    # 偏好抽取准确率：Precision / Recall / F1
│   ├── knowledge_eval.py                     # 知识抽取质量评测
│   ├── retrieval_eval.py                     # 检索召回率：Recall@K / MRR / nDCG
│   ├── conflict_eval.py                      # 冲突处理正确率
│   ├── latency_eval.py                       # 检索延迟：P50 / P95 / P99
│   ├── forgetting_eval.py                    # 遗忘成功率、误删率、泄漏率
│   ├── security_eval.py                      # 敏感信息识别与过滤效果
│   ├── e2e_eval.py                           # 端到端 memory_on / memory_off 对照实验
│   └── report_generator.py                   # 自动生成 Markdown / JSON 测试报告
│
├── demo/                                     # 演示层
│   ├── agent_simulator.py                    # 模拟 Agent 调用：写事件、检索、工具调用、抽取、遗忘
│   ├── office_demo.py                        # 办公场景端到端 Demo：月报生成、PDF 偏好、工作流复用
│   └── run_demo.py                           # 一键运行完整演示
│
├── ui/                                       # 可视化界面，可选但建议保留
│   ├── web_app.py                            # PC Web 页面入口
│   ├── pages/
│   │   ├── events_page.py                    # 查看事件流
│   │   ├── memories_page.py                  # 查看、检索、删除记忆
│   │   ├── eval_page.py                      # 查看评测指标
│   │   └── settings_page.py                  # 用户配置和系统配置
│   └── assets/
│
├── data/                                     # 数据目录
│   ├── raw/                                  # 原始样例数据
│   │   ├── sample_sessions.json              # 模拟 Agent 会话轨迹
│   │   ├── conversation.jsonl                # 用户对话日志样例
│   │   ├── tool_calls.jsonl                  # 工具调用样例
│   │   ├── tool_results.jsonl                # 工具执行结果样例
│   │   ├── user_behavior.jsonl               # 用户行为样例
│   │   └── user_config.jsonl                 # 手动配置样例
│   │
│   ├── processed/                            # 中间结果，调试用
│   │   ├── memory_events.jsonl               # 标准化后的 MemoryEvent
│   │   ├── memory_candidates.jsonl           # 候选记忆
│   │   └── memory_records.jsonl              # 正式记忆导出
│   │
│   └── gold/                                 # 评测标准答案
│       ├── preference_gold.json              # 偏好抽取 gold
│       ├── knowledge_gold.json               # 知识抽取 gold
│       ├── retrieval_gold.json               # 检索 gold：query + gold_memory_ids
│       ├── conflict_gold.json                # 冲突处理 gold
│       ├── forget_gold.json                  # 遗忘 gold
│       ├── security_gold.json                # 安全过滤 gold
│       └── e2e_gold.json                     # 端到端任务 gold
│
├── tests/                                    # 单元测试与集成测试
│   ├── test_core_models.py
│   ├── test_ingestion.py
│   ├── test_security.py
│   ├── test_extractors.py
│   ├── test_memory_store.py
│   ├── test_conflict_resolver.py
│   ├── test_forgetter.py
│   ├── test_flow.py
│   ├── test_policy.py
│   ├── test_retrieval.py
│   ├── test_embeddings.py
│   ├── test_vector_store.py
│   ├── test_evaluation.py
│   └── test_integration.py
│
├── memory_store/                             # 运行时生成目录，不提交真实用户数据
│   ├── memories.db                           # SQLite：MemoryRecord / history / forget_log
│   ├── events.db                             # SQLite：RawEvent / MemoryEvent
│   ├── vector_index/                         # 向量索引目录
│   │   ├── index.faiss                       # 开发环境 FAISS 索引，生产可替换为麒麟向量库
│   │   └── vector_map.json                   # vector_id → memory_id
│   └── snapshots/                            # Markdown 快照，仅用于展示和调试
│       └── users/
│           └── {user_id}/
│               ├── preferences.md
│               ├── knowledge.md
│               ├── workflows.md
│               ├── tools.md
│               └── safety.md
│
├── docs/                                     # 项目文档
│   ├── requirements.md                       # 需求分析
│   ├── architecture.md                       # 系统架构图、模块说明、数据流
│   ├── interface_contract.md                 # 核心模型与模块接口契约
│   ├── api.md                                # API 文档
│   ├── memory_policy.md                      # 记忆调度策略说明
│   ├── evaluation.md                         # 评测数据集与指标说明
│   ├── deployment.md                         # 银河麒麟 V11 部署指南
│   ├── user_manual.md                        # 用户手册
│   └── test_report.md                        # 测试报告
│
├── scripts/                                  # 脚本
│   ├── init_db.py                            # 初始化 SQLite 表
│   ├── build_index.py                        # 构建向量索引
│   ├── run_demo.sh                           # 一键运行 Demo
│   ├── run_eval.sh                           # 一键运行评测
│   └── export_report.py                      # 导出测试报告
│
├── .ai_skills/                               # 团队 AI 协作提示词，可选
│   ├── 00_project_rules.md
│   ├── 01_interface_contract.md
│   ├── 02_module_implementation.md
│   ├── 03_code_review.md
│   ├── 04_test_writer.md
│   └── 05_docs_writer.md
│
├── .gitignore
├── requirements.txt
├── README.md
├── TECH_DESIGN.md
└── CLAUDE.md
```

