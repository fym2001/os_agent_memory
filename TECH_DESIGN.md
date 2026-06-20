# 技术设计文档（TECH_DESIGN）
# OS Agent 记忆优化及高效应用研究

**版本：** v1.0  
**最后更新：** 2026-06-13

---

## 目录

1. [技术栈选择](#1-技术栈选择)
2. [项目结构](#2-项目结构)
3. [数据模型](#3-数据模型)
4. [关键技术点](#4-关键技术点)

---

## 1. 技术栈选择

### 1.1 选型原则

比赛有两条硬约束：① 必须调用**银河麒麟 embedding SDK**；② 检索延迟 **≤ 500ms**。因此所有选型优先考虑**端侧本地运行、无外部服务依赖、轻量低资源占用**。

### 1.2 后端服务

| 组件 | 选型 | 理由 |
|---|---|---|
| Web 框架 | **FastAPI** | 异步支持、自动生成 OpenAPI 文档、Pydantic 数据校验，适合快速搭建 REST API |
| ASGI 服务器 | **Uvicorn** | FastAPI 官方推荐，启动快，资源占用低 |
| 数据校验 | **Pydantic v2** | 随 FastAPI 引入，零额外依赖，request/response schema 一体管理 |
| 语言版本 | **Python 3.11** | 麒麟系统可用，性能比 3.9/3.10 提升明显，类型注解支持完整 |

### 1.3 存储层

系统采用**三层存储**，各层职责明确：

| 存储层 | 选型 | 存储内容 | 理由 |
|---|---|---|---|
| 事件流 | **SQLite（JSONL 降级）** | raw_events 原始事件记录 | 无需独立进程，端侧友好；JSONL 作为轻量备选 |
| 结构化索引 | **SQLite** | 记忆条目元数据、版本历史、冲突日志、遗忘审计 | 支持 SQL 查询和事务，适合版本化管理 |
| 向量索引 | **FAISS（IndexFlatIP）** | 记忆内容的向量表示，用于语义检索 | 纯本地，无网络依赖，百万级向量毫秒级检索 |
| 可读记忆文件 | **Markdown** | 五类长期记忆的人类可读版本 | 便于调试、演示、手工审查，也是评委最直观看到的输出 |

### 1.4 检索与 Embedding

| 组件 | 选型 | 说明 |
|---|---|---|
| Embedding | **银河麒麟 embedding SDK** | 比赛强制要求，适配麒麟系统本地推理 |
| 向量检索 | **FAISS** | 配合麒麟 embedding SDK 使用，IndexFlatIP 余弦相似度 |
| 关键词检索 | **rank_bm25** | 轻量 BM25，补充向量检索的精确词匹配能力 |
| 混合融合 | 加权线性融合（RRF 备选） | 向量分 × 0.6 + BM25 分 × 0.4，可通过配置调整权重 |

### 1.5 偏好抽取与冲突判断

| 组件 | 选型 | 说明 |
|---|---|---|
| 规则引擎 | 关键词 + 正则 | MVP 阶段快速实现显式偏好抽取 |
| LLM 抽取 | **本地 Ollama**（qwen2.5:7b） | 用于隐式偏好挖掘和冲突语义判断；无网络依赖 |
| 敏感信息识别 | 正则规则集 | 覆盖手机号、身份证、密钥、密码等，不依赖外部模型 |

### 1.6 测试与工具

| 工具 | 用途 |
|---|---|
| pytest | 单元测试、集成测试 |
| httpx | FastAPI 接口集成测试（AsyncClient） |
| rich | CLI 演示输出美化 |
| python-dotenv | 配置文件管理 |

---

## 2. 项目结构

```
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



**关键设计决策：**

- `core/models.py` 定义所有数据类，`core/constants.py` 定义所有枚举，Phase 0 冻结，全员只 import 不修改
- `embeddings/embedding_service.py` 统一接口屏蔽麒麟 SDK 与本地模型差异，C 开发期间用 `mock_embedding.py`，上麒麟环境切换 `kylin_embedding.py` 即可
- `memory/store.py` 是唯一操作 SQLite 的入口，其他所有模块通过它读写数据，不直接写 SQL
- `data/gold/` 是评测的唯一数据源，由 D 维护，其他人不修改
- `memory_store/` 运行时自动生成，不提交到 git，`.gitignore` 排除

---

## 3. 数据模型

### 3.1 记忆条目（Memory）

存储于 `memory_store/users/{user_id}/memories.db`，同时同步写入 `snapshots/` 下对应 Markdown 文件。

**SQLite 表：memories**

```sql
CREATE TABLE memories (
    memory_id   TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    memory_type TEXT NOT NULL,   -- preference | knowledge | workflow | tool | safety
    key         TEXT NOT NULL,   -- 记忆标识键，同 user_id+memory_type+key 唯一
    value       TEXT NOT NULL,   -- 记忆核心值（JSON 字符串或纯文本）
    content     TEXT NOT NULL,   -- 人类可读描述，用于检索和 Markdown 展示
    scene       TEXT DEFAULT 'general',
    confidence  REAL DEFAULT 0.8,
    priority    TEXT DEFAULT 'normal',  -- high | normal（high 的不被低置信度覆盖）
    version     INTEGER DEFAULT 1,
    status      TEXT DEFAULT 'active',  -- active | deleted | deprecated
    source      TEXT,                   -- user_message | tool_result | manual_config | session_commit
    source_events TEXT,                 -- JSON 数组，来源 event_id 列表
    vector_id   INTEGER,                -- 对应 FAISS 索引中的行号
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT                    -- 遗忘时填写
);

CREATE UNIQUE INDEX idx_memories_key
    ON memories (user_id, memory_type, key)
    WHERE status = 'active';
```

**五种 memory_type 说明：**

| memory_type | key 示例 | value 示例 |
|---|---|---|
| preference | `meeting_minutes_format` | `"表格形式"` |
| knowledge | `meeting_template_fields` | `"议题、结论、待办、负责人、截止时间"` |
| workflow | `office_summary_workflow` | `"读取文件 → 整理 Markdown → 导出 PDF"` |
| tool | `tool_success:export_document` | `"export_document"` |
| safety | `no_upload_rule` | `"涉密文件本地处理，不上传外部服务"` |

---

### 3.2 版本历史（Memory History）

每次记忆被覆盖时，旧版本归档到此表，支持回溯查询。

```sql
CREATE TABLE memory_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id   TEXT NOT NULL,
    version     INTEGER NOT NULL,
    value       TEXT,
    content     TEXT,
    confidence  REAL,
    archived_at TEXT NOT NULL
);
```

---

### 3.3 原始事件（Raw Event）

存储于 `memory_store/users/{user_id}/events.db`，是所有记忆的原始数据来源，也是中期记忆的载体。

```sql
CREATE TABLE raw_events (
    event_id    TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    event_type  TEXT NOT NULL,  -- user_message | tool_result | llm_plan | final_answer | manual_config
    scene       TEXT,
    content     TEXT NOT NULL,
    raw_payload TEXT,           -- 完整原始数据（JSON）
    source      TEXT DEFAULT 'agent',
    timestamp   TEXT NOT NULL
);

CREATE INDEX idx_events_user_session ON raw_events (user_id, session_id);
CREATE INDEX idx_events_timestamp    ON raw_events (user_id, timestamp);
```

---

### 3.4 遗忘审计日志（Forget Log）

```sql
CREATE TABLE forget_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    instruction     TEXT NOT NULL,      -- 用户输入的自然语言遗忘指令
    deleted_ids     TEXT NOT NULL,      -- JSON 数组，被删除的 memory_id 列表
    match_keywords  TEXT,               -- 命中的关键词
    executed_at     TEXT NOT NULL
);
```

---

### 3.5 向量索引映射

FAISS 本身不存 metadata，需要维护一个 `vector_id → memory_id` 的映射表。

```sql
CREATE TABLE vector_index (
    vector_id   INTEGER PRIMARY KEY,   -- FAISS 中的行号（0-based）
    memory_id   TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    content     TEXT NOT NULL,         -- 构建向量时的原始文本（用于重建索引）
    is_active   INTEGER DEFAULT 1
);
```

---

### 3.6 内存数据结构（运行时）

**候选记忆（candidate）** — 从事件中抽取后、写入数据库前的中间结构：

```python
{
    "memory_type": "preference",       # 必填
    "key": "meeting_minutes_format",   # 必填，唯一标识
    "value": "表格形式",                # 必填
    "content": "用户偏好会议纪要使用表格形式输出。",  # 必填，人类可读
    "scene": "meeting_minutes",        # 可选，默认 general
    "confidence": 0.92,                # 0.0～1.0
    "priority": "normal",              # high | normal
    "source": "user_message",          # 来源类型
    "source_events": ["evt_abc123"],   # 来源事件 ID 列表
}
```

**检索结果（memory context）** — 返回给 Agent 注入提示词：

```python
{
    "preferences":   [...],   # 偏好记忆列表
    "knowledge":     [...],   # 知识记忆列表
    "workflows":     [...],   # 流程记忆列表
    "tools":         [...],   # 工具记忆列表
    "safety_rules":  [...],   # 安全记忆列表（强制包含）
    "prompt_block":  "...",   # 拼装好的提示词块，Agent 直接注入
    "latency_ms":    120,     # 检索耗时
}
```

---

## 4. 关键技术点

### 4.1 混合检索融合（召回率 ≥ 85% 的核心）

**问题：** 单一检索方式各有盲区——向量检索对精确关键词不敏感，BM25 对语义变体召回率低。

**方案：** 倒数排名融合（RRF，Reciprocal Rank Fusion）

```python
def rrf_score(rank: int, k: int = 60) -> float:
    return 1.0 / (k + rank)

def hybrid_retrieve(query: str, top_k: int = 5):
    bm25_results  = bm25_search(query, top_n=20)   # 关键词检索，取前20
    faiss_results = vector_search(query, top_n=20)  # 向量检索，取前20

    scores = {}
    for rank, item in enumerate(bm25_results):
        scores[item.memory_id] = scores.get(item.memory_id, 0) + rrf_score(rank)
    for rank, item in enumerate(faiss_results):
        scores[item.memory_id] = scores.get(item.memory_id, 0) + rrf_score(rank)

    # 安全记忆强制注入，不参与 top_k 竞争
    safety = [m for m in all_active if m.memory_type == "safety"]
    ranked = sorted(scores, key=scores.get, reverse=True)[:top_k]
    return fetch_by_ids(ranked) + safety
```

**注意事项：**
- FAISS 使用 `IndexFlatIP`（内积），需要对向量归一化（L2 norm）才等价于余弦相似度
- 麒麟 embedding SDK 输出的向量在写入 FAISS 前必须做 `faiss.normalize_L2(vec)`
- BM25 的语料库（corpus）需要在每次记忆写入/删除后增量更新，不要全量重建（性能瓶颈）

---

### 4.2 置信度评分与多源证据融合（偏好准确率 ≥ 85% 的核心）

**问题：** 单次事件抽取的偏好可能是噪声，需要累积多次证据才能提升置信度。

**方案：** 贝叶斯更新式多源融合

```python
def update_confidence(existing_conf: float, new_conf: float, source: str) -> float:
    # 不同来源的权重
    source_weight = {
        "manual_config": 1.0,   # 用户手动配置，直接设为 1.0
        "user_message":  0.9,   # 用户显式表达
        "tool_result":   0.6,   # 工具行为推断
        "session_commit":0.75,  # session 级别聚合
    }
    w = source_weight.get(source, 0.5)
    # 加权平均，新证据权重更高
    return min(existing_conf * 0.4 + new_conf * w * 0.6, 1.0)
```

**注意事项：**
- 置信度低于阈值（建议 0.5）的候选记忆不写入正式记忆，暂存为 `pending` 状态
- 手动配置的 priority=high 记忆不参与置信度覆盖逻辑，无论新证据置信度多高都不覆盖

---

### 4.3 知识冲突检测与合并（冲突处理正确率 ≥ 88% 的核心）

**问题：** 同一 key 的记忆前后矛盾（"以前用 Word，现在改 PDF"），需要正确判断用新值覆盖旧值，而不是并存。

**两类冲突：**

| 类型 | 示例 | 处理策略 |
|---|---|---|
| 直接覆盖型 | 输出格式从 Word 改为 PDF | 新值覆盖旧值，旧值归档到 history |
| 场景差异型 | 工作文档用 PDF，个人笔记用 Markdown | 拆分为两条不同 scene 的记忆，互不覆盖 |

**冲突检测流程：**

```
新候选记忆到来
    ↓
查询 SQLite：user_id + memory_type + key 是否有 active 记录？
    ├── 无 → 直接插入
    └── 有 → 进入冲突处理
              ↓
         scene 是否相同？
              ├── 不同 → 新增一条独立记忆（场景差异型）
              └── 相同 → 比较 priority 和 confidence
                          ├── 新的 priority=high → 覆盖
                          ├── 旧的 priority=high → 保留旧的，记录冲突日志
                          └── 同级 → 新 confidence ≥ 旧 confidence → 覆盖并归档旧版本
```

**注意事项：**
- 覆盖时旧版本必须写入 `memory_history` 表，version 字段递增
- 冲突日志用于测试报告中的"冲突处理正确率"指标计算

---

### 4.4 自然语言遗忘（精准性保证）

**问题：** 用户说"忘掉关于 Word 格式的偏好"，需要准确定位对应记忆，既不能漏删，也不能误删。

**方案：** 两阶段匹配

```
阶段一：关键词粗筛
    从遗忘指令中提取实体词（工具名、格式名、场景词）
    在 memories 表中做 LIKE 模糊匹配
    候选集通常 < 10 条

阶段二：语义精排（可选，置信度不足时启用）
    对候选集用 embedding 相似度二次过滤
    相似度 > 0.85 才标记为 deleted

执行：
    UPDATE memories SET status='deleted', deleted_at=now() WHERE memory_id IN (...)
    写入 forget_log
    FAISS 中对应向量标记为无效（is_active=0），下次重建索引时清除
```

**注意事项：**
- 遗忘操作是软删除（`status='deleted'`），不物理删除行，保证审计可追溯
- FAISS 不支持单向量删除，采用"标记无效 + 定期重建索引"策略（每隔 N 次写操作触发一次重建）

---

### 4.5 检索延迟 ≤ 500ms 的保证

**延迟拆解（目标：总延迟 < 500ms）：**

| 步骤 | 目标耗时 | 优化手段 |
|---|---|---|
| Embedding 计算（query） | < 100ms | 麒麟 SDK 本地推理，query 向量可做 LRU 缓存 |
| FAISS 向量检索 | < 50ms | IndexFlatIP 在万级向量下 < 10ms |
| BM25 检索 | < 30ms | 内存常驻 BM25 模型，不重新加载 |
| SQLite 元数据查询 | < 20ms | 命中索引的点查，加 WAL 模式提升读写并发 |
| 结果融合 + 组装 | < 10ms | 纯内存操作 |
| **总计** | **< 210ms（P50）** | 留 290ms 余量应对 P95 场景 |

**关键配置：**
```python
# SQLite WAL 模式，提升并发读性能
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA cache_size=-64000")  # 64MB 缓存

# BM25 模型在服务启动时预加载，常驻内存
# FAISS 索引在服务启动时 mmap 加载，避免每次检索重载
```

---

### 4.6 短/中/长期记忆数据流转

**三层记忆的交互边界：**

```
短期记忆（Agent 内存）
    │ session 结束后
    │ POST /memory/commit
    ↓
中期记忆（events.db → raw_events 表）
    │ 定期（每 N 天 / 每 N 条事件）触发聚合
    │ extractors/ 从事件流中再次抽取偏好
    ↓
长期记忆（memories.db + snapshots/ + vectors.faiss）
    │ 检索时由 retrieval/ 读取
    ↓
Agent 提示词（prompt_block 注入）
```

**中期到长期的触发条件（避免频繁写入）：**
- 单 session commit 直接触发（强信号：用户明确表达偏好）
- 事件累积超过 50 条触发批量聚合（弱信号：行为推断）
- 每日定时任务触发一次全量聚合（兜底）

---

### 4.7 银河麒麟 embedding SDK 适配

**接入方式（待 SDK 文档确认后填充细节）：**

```python
# 统一 embedding 接口，方便后续切换
class EmbeddingModel:
    def encode(self, texts: list[str]) -> np.ndarray:
        ...

class KylinEmbedding(EmbeddingModel):
    """银河麒麟 embedding SDK 封装"""
    def __init__(self):
        # from kylin_sdk import EmbeddingClient  # 实际 import 路径待确认
        pass

    def encode(self, texts: list[str]) -> np.ndarray:
        # 调用麒麟 SDK，返回 float32 向量数组
        # 返回前做 L2 归一化，保证与 FAISS IndexFlatIP 的余弦相似度一致
        vecs = self._client.encode(texts)
        faiss.normalize_L2(vecs)
        return vecs
```

**注意事项：**
- 用抽象基类隔离 SDK 依赖，本地开发时可用 `sentence-transformers` 的小模型（如 `paraphrase-multilingual-MiniLM-L12-v2`）作为替代，对应 `embeddings/mock_embedding.py`，上麒麟环境时切换为 `embeddings/kylin_embedding.py` 即可，`embeddings/embedding_service.py` 对外接口不变
- SDK 首次加载耗时可能较长（模型初始化），需在服务启动时预热，不要在请求路径上懒加载

---

### 4.8 敏感信息识别与脱敏

在 `ingestion/cleaner.py` 和 `security/sanitizer.py` 中，所有写入前的内容都经过以下规则过滤：

```python
SENSITIVE_PATTERNS = [
    (r'1[3-9]\d{9}',                        '***手机号***'),
    (r'\d{17}[\dXx]',                        '***身份证***'),
    (r'(?i)(password|passwd|secret)\s*[:=]\s*\S+', '***密钥***'),
    (r'(?i)(api.?key|token)\s*[:=]\s*\S+',   '***Token***'),
    (r'\d{16,19}',                            '***银行卡号***'),
]
```

**注意事项：**
- 脱敏在 `ingestion/cleaner.py` 清洗阶段执行，早于写入 `events.db`，原始敏感数据不落盘
- `raw_payload` 字段同样需要过滤，`security/sanitizer.py` 对 content 和 raw_payload 同时处理，不能只过滤其中一个

---

*文档完*
