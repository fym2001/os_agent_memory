# Phase 1 并行开发任务安排表
# OS Agent 记忆优化及高效应用研究

**阶段：** MVP 最小闭环 — 第一阶段核心模块开发  
**周期：** 2026-06-20 ～ 2026-07-03（2 周）  
**团队规模：** 4 人并行开发  
**约束条件：** 所有任务依赖 Phase 0 ✅（可用）

---

## 整体里程碑

```
Phase 1（06-20 ～ 07-03）
  ├─ Week 1（06-20 ～ 06-26）
  │  ├─ 人员 A：ingestion 核心完成 + store.py 补充
  │  ├─ 人员 B：preference_extractor.py + knowledge_extractor.py
  │  ├─ 人员 C：BM25 关键词检索 + 向量检索框架
  │  └─ 人员 D：forgetter.py 框架 + 基础评测模块
  │
  └─ Week 2（06-27 ～ 07-03）
     ├─ 人员 A：测试补完 + 完整流程验证
     ├─ 人员 B：workflow_extractor.py + 额外提取器
     ├─ 人员 C：混合检索实现 + 重排序
     └─ 人员 D：评测框架完整 + 遗忘逻辑验证
```

---

## 人员 A — 数据接入 & 存储（A 工程师）

**职责链：** Agent raw payload → 标准化 → 验证 → 落盘 SQLite  
**产出周期：** 2026-06-20 ～ 2026-07-03（14 天）  
**验收标准：** 所有单测 ✓，无数据损失，流程完整  

### 任务分解

| 优先级 | 任务 ID | 任务名称 | 文件 | 开始日期 | 预计完成 | 工期 | 依赖 | 状态 |
|---|---|---|---|---|---|---|---|---|
| P0 | A-01 | 实现 collector.py — 加载 JSONL 和解析原始事件 | `ingestion/collector.py` | 06-20 | 06-21 | 2d | Phase 0 ✅ | ⏳ |
| P0 | A-02 | 实现 adapter.py — RawEvent → MemoryEvent 转换 | `ingestion/adapter.py` | 06-20 | 06-22 | 2d | A-01 | ⏳ |
| P0 | A-03 | 实现 validator.py — 数据质量校验 | `ingestion/validator.py` | 06-21 | 06-23 | 1d | A-02 | ⏳ |
| P0 | A-04 | 实现 cleaner.py — 数据清洗（去噪、去重） | `ingestion/cleaner.py` | 06-22 | 06-24 | 2d | A-03 | ⏳ |
| P1 | A-05 | 补完 store.py — 事件持久化接口 | `memory/store.py` | 06-23 | 06-25 | 2d | Phase 0 ✅ | ⏳ |
| P0 | A-06 | 编写单元测试（collector + adapter + validator） | `tests/test_ingestion.py` | 06-24 | 06-26 | 2d | A-04 | ⏳ |
| P0 | A-07 | 编写单元测试（store.py） | `tests/test_memory_store.py` | 06-25 | 06-27 | 1d | A-05 | ⏳ |
| P1 | A-08 | 集成测试 — 端到端流程验证 | `tests/test_flow.py` | 06-27 | 06-28 | 1d | A-07 | ⏳ |

### 详细任务卡

#### A-01：collector.py（2d）

**功能：** 从 JSONL 文件加载原始事件，转换为 RawEvent

**函数签名：**
```python
def load_jsonl(path: str) -> list[dict[str, Any]]: ...
def create_raw_event(payload: dict[str, Any]) -> RawEvent: ...
def validate_raw_payload(payload: dict) -> bool: ...
```

**验收点：**
- [ ] 成功加载 `data/raw/office_demo_events.jsonl`
- [ ] 每行变为一个 RawEvent 对象，event_id 唯一
- [ ] 错误 JSONL 行被捕获，记录错误日志
- [ ] 时间戳正确解析和设置

---

#### A-02：adapter.py（2d）

**功能：** RawEvent 标准化转换为 MemoryEvent

**函数签名：**
```python
def raw_event_to_memory_event(raw_event: RawEvent) -> MemoryEvent: ...
def map_source(event_type: EventType) -> str: ...
```

**逻辑映射：**
```
CONVERSATION → source="conversation", actor="user"
TOOL_CALL → source="tool_call", actor="agent"
TOOL_RESULT → source="tool_result", actor="tool"
```

**验收点：**
- [ ] 三种事件类型映射正确
- [ ] 所有 RawEvent 字段映射到 MemoryEvent
- [ ] raw_event_id 关联保留
- [ ] source 和 actor 字段正确赋值

---

#### A-03：validator.py（1d）

**功能：** 对 MemoryEvent 执行质量校验

**函数签名：**
```python
def validate_memory_event(event: MemoryEvent) -> tuple[bool, list[str]]: ...
def validate_tool_result_event(event: MemoryEvent) -> tuple[bool, list[str]]: ...
def validate_conversation_event(event: MemoryEvent) -> tuple[bool, list[str]]: ...
```

**校验规则：**
```
✓ user_id 非空
✓ event_type 在 EventType 枚举范围
✓ scenario 在 Scene 枚举范围
✓ source 非空
✓ TOOL_RESULT 事件必须有 success 字段
✓ CONVERSATION 事件必须有 content
✓ timestamp 有效且不超过当前时间
```

**验收点：**
- [ ] 校验通过返回 (True, [])
- [ ] 校验失败返回 (False, ["错误1", "错误2"])
- [ ] 覆盖全部校验规则

---

#### A-04：cleaner.py（2d）

**功能：** 清洗、去噪、去重

**函数签名：**
```python
def remove_noise(events: list[MemoryEvent]) -> list[MemoryEvent]: ...
def deduplicate_events(events: list[MemoryEvent]) -> list[MemoryEvent]: ...
def normalize_content(content: str) -> str: ...
```

**清洗规则：**
```
✓ 去除空白内容事件
✓ 按 event_id 去重
✓ 标准化文本（trim、规范化换行）
✓ 标记和过滤异常格式
✓ 补全缺失的可选字段
```

**验收点：**
- [ ] 清洗后事件数量 ≤ 输入数量
- [ ] 重复事件被删除
- [ ] content 清洁统一

---

#### A-05：store.py 补充（2d）

**功能：** SQLite 持久化 — 完善现有 Phase 0 函数

**补充函数签名：**
```python
def insert_event(db_path: str, event: MemoryEvent) -> str: ...
def insert_event_batch(db_path: str, events: list[MemoryEvent]) -> int: ...
def query_events_by_session(db_path: str, session_id: str) -> list[MemoryEvent]: ...
def count_events(db_path: str, user_id: str) -> int: ...
```

**验收点：**
- [ ] insert_event 返回递增 event_id
- [ ] insert_event_batch 成功插入多条
- [ ] 查询能返回完整 MemoryEvent 对象
- [ ] 事务完整性保证

---

#### A-06：test_ingestion.py（2d）

**测试用例：**
```
✓ test_load_jsonl_normal()
✓ test_load_jsonl_empty_file()
✓ test_load_jsonl_corrupt_line()
✓ test_create_raw_event()
✓ test_adapter_conversation()
✓ test_adapter_tool_call()
✓ test_adapter_tool_result()
✓ test_validator_pass()
✓ test_validator_fail_missing_user_id()
✓ test_validator_fail_invalid_type()
✓ test_cleaner_deduplicate()
✓ test_cleaner_normalize()
```

**验收点：**
- [ ] `pytest tests/test_ingestion.py -v` 全部通过
- [ ] 覆盖率 ≥ 85%

---

#### A-07：test_memory_store.py（1d）

**测试用例：**
```
✓ test_insert_event()
✓ test_insert_event_batch()
✓ test_query_by_session()
✓ test_count_events()
✓ test_save_memory()
✓ test_mark_deleted()
```

**验收点：**
- [ ] `pytest tests/test_memory_store.py -v` 全部通过
- [ ] 覆盖率 ≥ 80%

---

#### A-08：test_flow.py 集成测试（1d）

**场景：** Collector → Adapter → Cleaner → Validator → Store 端到端流程

```python
def test_end_to_end_flow():
    # 1. 加载 office_demo_events.jsonl
    raw_events = load_jsonl(DEMO_DATA_PATH)
    
    # 2. 转换为 MemoryEvent
    memory_events = [raw_event_to_memory_event(r) for r in raw_events]
    
    # 3. 清洗去重
    cleaned = deduplicate_events(memory_events)
    
    # 4. 校验
    for event in cleaned:
        valid, errs = validate_memory_event(event)
        assert valid, f"Failed: {errs}"
    
    # 5. 存储
    db = init_db(":memory:")
    count = insert_event_batch(db, cleaned)
    
    # 6. 验证
    assert count == len(cleaned)
    assert query_events_by_session(db, session_id) has results
```

**验收点：**
- [ ] 测试通过，无错误
- [ ] 数据完整性保证

---

## 人员 B — 记忆抽取器（B 工程师）

**职责链：** MemoryEvent → MemoryCandidate（多种类型）  
**产出周期：** 2026-06-20 ～ 2026-07-03（14 天）  
**验收标准：** 三个核心提取器完整，单测覆盖 ≥ 80%

### 任务分解

| 优先级 | 任务 ID | 任务名称 | 文件 | 开始日期 | 预计完成 | 工期 | 依赖 | 状态 |
|---|---|---|---|---|---|---|---|---|
| P0 | B-01 | 实现 preference_extractor.py — 显式和隐式偏好抽取 | `extractors/preference_extractor.py` | 06-20 | 06-23 | 3d | Phase 0 ✅ | ⏳ |
| P0 | B-02 | 实现 knowledge_extractor.py — 知识和技巧抽取 | `extractors/knowledge_extractor.py` | 06-21 | 06-24 | 3d | Phase 0 ✅ | ⏳ |
| P0 | B-03 | 编写单元测试 preference_extractor | `tests/test_extractors.py` | 06-24 | 06-25 | 1d | B-01 | ⏳ |
| P0 | B-04 | 编写单元测试 knowledge_extractor | `tests/test_extractors.py` | 06-25 | 06-26 | 1d | B-02 | ⏳ |
| P1 | B-05 | 实现 workflow_extractor.py — 工作流和流程抽取 | `extractors/workflow_extractor.py` | 06-27 | 06-30 | 3d | B-01 + B-02 | ⏳ |
| P2 | B-06 | 实现 tool_extractor.py — 工具成功率和模式 | `extractors/tool_extractor.py` | 07-01 | 07-03 | 2d | B-02 | ⏳ |
| P2 | B-07 | 实现 environment_extractor.py — 系统配置记忆 | `extractors/environment_extractor.py` | 07-01 | 07-03 | 2d | Phase 0 ✅ | ⏳ |

### 详细任务卡

#### B-01：preference_extractor.py（3d）

**功能：** 从对话、工具调用等事件中抽取用户偏好

**函数签名：**
```python
class PreferenceExtractor:
    def extract_from_conversation(event: MemoryEvent) -> list[MemoryCandidate]: ...
    def extract_from_tool_result(event: MemoryEvent) -> list[MemoryCandidate]: ...
    def extract_explicit_preference(content: str) -> list[MemoryCandidate]: ...
    def extract_implicit_preference(events: list[MemoryEvent]) -> list[MemoryCandidate]: ...
```

**显式偏好规则（关键词匹配）：**
```
✓ "以后...都..." → 规则性偏好
✓ "我喜欢/不喜欢..." → 明确倾向
✓ "下次...请..." → 工作流偏好
✓ "导出为 [格式]" → 输出格式偏好
```

**隐式偏好检测（高频行为）：**
```
✓ 同一操作重复 ≥ 3 次 → 工作流偏好
✓ 相同工具连续使用 → 工具偏好
✓ 相同参数频繁使用 → 参数偏好
```

**验收点：**
- [ ] 显式偏好规则覆盖 ≥ 10 种常见模式
- [ ] 隐式偏好能从高频行为推断
- [ ] confidence 分数合理（0.5~1.0）
- [ ] source_events 链接正确

---

#### B-02：knowledge_extractor.py（3d）

**功能：** 从工具结果、用户表述等抽取可复用知识

**函数签名：**
```python
class KnowledgeExtractor:
    def extract_from_tool_result(event: MemoryEvent) -> list[MemoryCandidate]: ...
    def extract_from_conversation(event: MemoryEvent) -> list[MemoryCandidate]: ...
    def extract_templates(events: list[MemoryEvent]) -> list[MemoryCandidate]: ...
```

**知识类型：**
```
✓ 工具使用案例（input + output 配对）
✓ 常见问题和解决方案
✓ 数据处理模板（batch export, merge files）
✓ 系统操作指南（desktop config, software setup）
```

**验收点：**
- [ ] 工具结果中关键信息被正确提取
- [ ] 模板识别有效（重复任务识别）
- [ ] confidence 与知识完整性关联
- [ ] 去重逻辑：相同模板不重复提取

---

#### B-03 & B-04：test_extractors.py（2d）

**测试用例：**
```
✓ test_preference_explicit_from_conversation()
✓ test_preference_implicit_from_frequency()
✓ test_preference_confidence_score()
✓ test_knowledge_from_tool_result()
✓ test_knowledge_template_extraction()
✓ test_knowledge_deduplication()
```

**验收点：**
- [ ] `pytest tests/test_extractors.py -v` 全部通过
- [ ] 覆盖率 ≥ 85%

---

#### B-05：workflow_extractor.py（3d）

**功能：** 从任务轨迹中提取工作流、流程、指令序列

**函数签名：**
```python
class WorkflowExtractor:
    def extract_tool_sequence(events: list[MemoryEvent]) -> list[MemoryCandidate]: ...
    def extract_multi_step_workflow(session_events: list[MemoryEvent]) -> list[MemoryCandidate]: ...
    def detect_workflow_boundary(events: list[MemoryEvent]) -> list[tuple[int, int]]: ...
```

**工作流检测：**
```
✓ 连续工具调用序列 → 工作流
✓ 多轮对话涉及多个工具 → 复杂流程
✓ 工具间存在依赖关系 → 序列化指令
```

**验收点：**
- [ ] 工作流提取覆盖 ≥ 2 个工作流模式
- [ ] 工具依赖关系识别
- [ ] 流程重现率 ≥ 80%

---

#### B-06：tool_extractor.py（2d）

**功能：** 从工具调用结果统计工具特征

**函数签名：**
```python
class ToolExtractor:
    def extract_tool_pattern(events: list[MemoryEvent]) -> list[MemoryCandidate]: ...
    def calculate_tool_success_rate(tool_name: str, events: list[MemoryEvent]) -> float: ...
```

**提取内容：**
```
✓ 工具成功率（success_count / total_count）
✓ 常见失败原因
✓ 平均响应时间
✓ 常用参数组合
```

**验收点：**
- [ ] 统计计算无偏差
- [ ] 数据完整性保证

---

#### B-07：environment_extractor.py（2d）

**功能：** 识别系统环境、配置、常用路径等

**函数签名：**
```python
class EnvironmentExtractor:
    def extract_from_tool_output(output: dict) -> list[MemoryCandidate]: ...
```

**提取内容：**
```
✓ 常用目录（Downloads, Documents）
✓ 系统语言、地区设置
✓ 已安装软件列表
✓ 系统版本信息
```

---

## 人员 C — 检索系统（C 工程师）

**职责链：** MemoryRecord + Query → RetrievalResult  
**产出周期：** 2026-06-20 ～ 2026-07-03（14 天）  
**验收标准：** 混合检索完整，检索延迟 ≤ 500ms

### 任务分解

| 优先级 | 任务 ID | 任务名称 | 文件 | 开始日期 | 预计完成 | 工期 | 依赖 | 状态 |
|---|---|---|---|---|---|---|---|---|
| P0 | C-01 | 实现 BM25 关键词检索 | `retrieval/keyword_retriever.py` | 06-20 | 06-22 | 2d | Phase 0 ✅ | ⏳ |
| P0 | C-02 | 实现向量检索框架 | `retrieval/vector_retriever.py` | 06-21 | 06-23 | 2d | Phase 0 ✅ | ⏳ |
| P1 | C-03 | 实现向量存储适配层 | `vector_store/faiss_vector_store.py` | 06-22 | 06-25 | 3d | Phase 0 ✅ | ⏳ |
| P0 | C-04 | 实现混合检索器 | `retrieval/hybrid_retriever.py` | 06-24 | 06-27 | 3d | C-01 + C-02 | ⏳ |
| P1 | C-05 | 实现重排序模块 | `retrieval/reranker.py` | 06-27 | 06-29 | 2d | C-04 | ⏳ |
| P0 | C-06 | 编写检索单元测试 | `tests/test_retrieval.py` | 06-27 | 07-01 | 3d | C-04 | ⏳ |
| P1 | C-07 | 性能基准测试（延迟 ≤ 500ms） | `tests/test_latency.py` | 07-01 | 07-03 | 2d | C-06 | ⏳ |

### 详细任务卡

#### C-01：keyword_retriever.py（2d）

**功能：** 基于 BM25 的关键词检索

**函数签名：**
```python
class KeywordRetriever:
    def __init__(self, memories: list[MemoryRecord]): ...
    def retrieve(query: str, top_k: int = 5) -> list[RetrievalResult]: ...
    def index_memory(memory: MemoryRecord) -> None: ...
    def remove_memory(memory_id: str) -> None: ...
```

**实现方案：**
```
✓ 使用 rank_bm25 库或自实现 BM25
✓ 索引 memory 的 content + key
✓ 返回 top_k 结果，带 BM25 分数
```

**验收点：**
- [ ] 关键词完全匹配率 ≥ 90%
- [ ] 支持多词查询
- [ ] 索引和删除操作支持

---

#### C-02：vector_retriever.py（2d）

**功能：** 向量检索的统一接口（不绑定具体向量库）

**函数签名：**
```python
class VectorRetriever:
    def __init__(self, embedding_service, vector_store): ...
    def retrieve(query: str, top_k: int = 5) -> list[RetrievalResult]: ...
    def add_memory(memory: MemoryRecord, embedding: list[float]) -> None: ...
    def delete_memory(memory_id: str) -> None: ...
```

**流程：**
```
1. Query text → embedding_service.encode() → embedding vector
2. Vector + top_k → vector_store.search() → memory_ids + scores
3. memory_ids → look up MemoryRecord → RetrievalResult
```

**验收点：**
- [ ] 接口完整，支持不同向量库
- [ ] 向量查询返回 top_k 结果
- [ ] 关联 metadata 正确

---

#### C-03：faiss_vector_store.py（3d）

**功能：** FAISS 向量数据库适配实现

**函数签名：**
```python
class FAISSVectorStore:
    def __init__(self, dimension: int = 384): ...
    def add(self, memory_ids: list[str], vectors: list[list[float]]) -> None: ...
    def search(self, query_vector: list[float], top_k: int) -> list[tuple[str, float]]: ...
    def delete(self, memory_ids: list[str]) -> None: ...
    def save(self, path: str) -> None: ...
    def load(self, path: str) -> None: ...
```

**实现细节：**
```
✓ IndexFlatIP（余弦相似度）
✓ 内存存储（便于快速检索）
✓ 可持久化到磁盘
✓ 支持增量添加和删除
```

**验收点：**
- [ ] 向量添加和搜索功能完整
- [ ] 相似度计算准确（余弦相似度）
- [ ] 磁盘持久化支持

---

#### C-04：hybrid_retriever.py（3d）

**功能：** 混合检索：融合关键词 + 向量 + 场景 + 时间衰减

**函数签名：**
```python
class HybridRetriever:
    def __init__(self, keyword_retriever, vector_retriever, config): ...
    def retrieve(
        query: str,
        top_k: int = 5,
        scenario: Scene = None,
        memory_types: list[MemoryType] = None
    ) -> list[RetrievalResult]: ...
```

**融合策略：**
```
score = w_keyword × keyword_score + w_vector × vector_score
       + scenario_bonus + recency_boost

其中：
  w_keyword = 0.4  （精确匹配权重）
  w_vector = 0.6   （语义匹配权重）
  scenario_bonus = 0.2 if memory.scenario == query.scenario else 0
  recency_boost = exp(-α × (now - memory.updated_at).days)
```

**验收点：**
- [ ] 两种检索方式结果融合
- [ ] 场景过滤和加权工作
- [ ] 时间衰减逻辑正确
- [ ] 返回结果排序合理

---

#### C-05：reranker.py（2d）

**功能：** 对混合检索结果重排序，优化最终排名

**函数签名：**
```python
class RetrievalReranker:
    def rerank(
        results: list[RetrievalResult],
        query: str,
        context: dict = None
    ) -> list[RetrievalResult]: ...
```

**重排序因素：**
```
✓ 类型权重（preference > knowledge > workflow）
✓ 置信度过滤（confidence < threshold 降级）
✓ 安全策略优先（敏感信息标记）
✓ 多样性加权（避免结果重复）
```

**验收点：**
- [ ] 重排逻辑清晰
- [ ] Top-K 结果质量提升
- [ ] 执行延迟 < 50ms

---

#### C-06：test_retrieval.py（3d）

**测试用例：**
```
✓ test_keyword_retrieval_exact_match()
✓ test_keyword_retrieval_partial_match()
✓ test_vector_retrieval_semantic()
✓ test_hybrid_retrieval_fusion()
✓ test_hybrid_retrieval_scenario_filter()
✓ test_reranker_type_priority()
✓ test_reranker_confidence_filter()
```

**验收点：**
- [ ] `pytest tests/test_retrieval.py -v` 全部通过
- [ ] 覆盖率 ≥ 80%

---

#### C-07：test_latency.py（2d）

**性能基准测试：** 验证检索延迟 ≤ 500ms

```python
def test_hybrid_retrieval_latency():
    # 1000 条记忆，查询 1000 次
    start = time.time()
    for _ in range(1000):
        results = hybrid_retriever.retrieve(query)
    elapsed = (time.time() - start) / 1000
    
    # P50, P95, P99 延迟统计
    assert elapsed["p50"] < 100ms  # 中位数 < 100ms
    assert elapsed["p95"] < 300ms  # 95分位 < 300ms
    assert elapsed["p99"] < 500ms  # 99分位 < 500ms
```

**验收点：**
- [ ] P99 延迟 ≤ 500ms（比赛约束）
- [ ] 1000 条记忆规模下性能稳定
- [ ] 向量检索不是瓶颈（可考虑量化）

---

## 人员 D — 遗忘 & 评测（D 工程师）

**职责链：** 记忆生命周期管理 + 量化评测  
**产出周期：** 2026-06-20 ～ 2026-07-03（14 天）  
**验收标准：** 遗忘逻辑完整，评测框架可用

### 任务分解

| 优先级 | 任务 ID | 任务名称 | 文件 | 开始日期 | 预计完成 | 工期 | 依赖 | 状态 |
|---|---|---|---|---|---|---|---|---|
| P0 | D-01 | 实现 forgetter.py — 软删除和精准遗忘 | `memory/forgetter.py` | 06-20 | 06-23 | 3d | Phase 0 ✅ | ⏳ |
| P0 | D-02 | 实现 preference_eval.py — 偏好抽取评测 | `evaluation/preference_eval.py` | 06-21 | 06-24 | 3d | Phase 0 ✅ | ⏳ |
| P0 | D-03 | 实现 retrieval_eval.py — 检索召回率评测 | `evaluation/retrieval_eval.py` | 06-22 | 06-25 | 3d | Phase 0 ✅ | ⏳ |
| P1 | D-04 | 实现 latency_eval.py — 延迟统计 | `evaluation/latency_eval.py` | 06-25 | 06-27 | 2d | Phase 0 ✅ | ⏳ |
| P0 | D-05 | 编写单元测试（forgetter + eval） | `tests/test_evaluation.py` | 06-27 | 06-29 | 2d | D-01 + D-03 | ⏳ |
| P1 | D-06 | 实现 conflict_eval.py — 冲突处理评测 | `evaluation/conflict_eval.py` | 06-29 | 07-01 | 2d | Phase 0 ✅ | ⏳ |
| P2 | D-07 | 实现 forgetting_eval.py — 遗忘成功率 | `evaluation/forgetting_eval.py` | 07-01 | 07-03 | 2d | D-01 | ⏳ |

### 详细任务卡

#### D-01：forgetter.py（3d）

**功能：** 自然语言遗忘命令解析与执行

**函数签名：**
```python
class MemoryForgetter:
    def parse_forget_command(instruction: str) -> ForgetCommand: ...
    def execute_forget(command: ForgetCommand) -> ForgetResult: ...
    def soft_delete(memory_ids: list[str]) -> None: ...
    def invalidate_indices(memory_ids: list[str]) -> None: ...
    def audit_forget(command: ForgetCommand) -> AuditLog: ...
```

**遗忘命令示例：**
```
"忘记所有 PDF 导出的偏好" → 查询 + 删除相关记忆
"删除关于项目 X 的知识" → 场景过滤 + 删除
"清除所有密码相关的记忆" → 敏感信息过滤 + 删除
```

**实现细节：**
```
✓ NLP 解析遗忘指令（关键词提取）
✓ 查询匹配记忆（模糊匹配）
✓ 软删除（标记 deleted=true，保留审计日志）
✓ 向量索引失效化（删除 vector_store 中的向量）
✓ 记录遗忘操作（who, when, what）
```

**验收点：**
- [ ] 遗忘命令解析准确率 ≥ 85%
- [ ] 软删除不丢失数据（可恢复）
- [ ] 审计日志完整
- [ ] 索引失效化无遗漏

---

#### D-02：preference_eval.py（3d）

**功能：** 评测偏好抽取的准确率

**函数签名：**
```python
class PreferenceEvaluator:
    def evaluate(
        extracted: list[MemoryCandidate],
        golden: list[MemoryCandidate]
    ) -> PreferenceEvalResult: ...
```

**评测指标：**
```
✓ Precision = TP / (TP + FP)        # 提取结果的正确率
✓ Recall = TP / (TP + FN)           # 召回的比例
✓ F1 = 2 × P × R / (P + R)
✓ Confidence Calibration            # 置信度与正确率的一致性
```

**验收点：**
- [ ] 指标计算正确
- [ ] 支持多类型偏好评测
- [ ] 结果报告清晰

---

#### D-03：retrieval_eval.py（3d）

**功能：** 评测检索的召回率和排序质量

**函数签名：**
```python
class RetrievalEvaluator:
    def evaluate(
        retrieved: list[RetrievalResult],
        golden: list[MemoryRecord],
        query: str
    ) -> RetrievalEvalResult: ...
```

**评测指标：**
```
✓ Recall@K = |retrieved ∩ golden| / |golden|
✓ MRR = 1 / rank(first_relevant)    # 平均倒数排名
✓ nDCG@K                             # 归一化折扣累计收益
✓ Precision@K
```

**验收点：**
- [ ] 指标计算无误
- [ ] 支持 K=1, 5, 10 等多个 K 值
- [ ] 评测流程可复现

---

#### D-04：latency_eval.py（2d）

**功能：** 检测和报告系统延迟分布

**函数签名：**
```python
class LatencyEvaluator:
    def measure_latency(
        operation: callable,
        iterations: int = 1000
    ) -> LatencyStats: ...
```

**统计指标：**
```
✓ P50, P95, P99 延迟
✓ 平均延迟
✓ 最大/最小延迟
✓ 标准差
```

**验收点：**
- [ ] 统计计算准确
- [ ] P99 ≤ 500ms
- [ ] 样本量 ≥ 100

---

#### D-05：test_evaluation.py（2d）

**测试用例：**
```
✓ test_preference_eval_precision()
✓ test_preference_eval_recall()
✓ test_retrieval_eval_recall_at_k()
✓ test_retrieval_eval_ndcg()
✓ test_latency_eval_percentile()
✓ test_forget_soft_delete()
✓ test_forget_audit_log()
```

**验收点：**
- [ ] `pytest tests/test_evaluation.py -v` 全部通过
- [ ] 覆盖率 ≥ 80%

---

#### D-06：conflict_eval.py（2d）

**功能：** 评测冲突检测和解决的准确性

**函数签名：**
```python
class ConflictEvaluator:
    def evaluate_conflict_detection(
        detected: list[MemoryConflict],
        golden: list[MemoryConflict]
    ) -> ConflictEvalResult: ...
```

**评测指标：**
```
✓ 冲突检测准确率
✓ 冲突解决正确率
✓ 误判率（假阳性）
```

---

#### D-07：forgetting_eval.py（2d）

**功能：** 评测遗忘操作的成功率和副作用

**函数签名：**
```python
class ForgettingEvaluator:
    def evaluate_forget_success_rate(forget_ops: list[ForgetCommand]) -> float: ...
    def evaluate_false_delete_rate(forget_ops: list) -> float: ...
    def evaluate_information_leakage(query: str) -> float: ...
```

**评测指标：**
```
✓ 遗忘成功率 = 成功删除数 / 总删除数
✓ 误删率 = 不应删除但被删除 / 总删除数
✓ 信息泄露率 = 遗忘后仍可检索到 / 应删除数
```

---

## 并行开发约束与沟通

### 🟢 允许修改

- 在现有数据模型中**增加可选字段**（向后兼容）
- 在 `constants.py` 中增加新的枚举值
- 创建新函数，不改现有函数签名

### 🔴 禁止修改

- ❌ 现有字段名称（如 `memory_id` → `mem_id`）
- ❌ 现有函数签名的参数类型或返回值类型
- ❌ 删除已有枚举值
- ❌ 修改 Phase 0 的表结构

### 代码提交规范

**每完成一个任务，需要：**
1. 所有单测通过（`pytest <test_file> -v`）
2. 代码注释清晰（仅在必要时）
3. 提交信息：`[TaskID] 任务名 — 简要说明`
   ```
   例：[A-01] collector.py 实现 — 支持 JSONL 加载和 RawEvent 转换
   ```

### 团队同步会议

- **每周一次 sync**（周三 14:00）
- **内容**：进度汇报、阻塞点解决、数据模型协议确认
- **Duration**：15 分钟

### 常见阻塞点处理

| 问题 | 负责人 | 解决方案 |
|---|---|---|
| 数据模型需要扩展 | 全员 | 同步后统一更新，所有人同时编译测试 |
| 上游任务延期 | 依赖方 | 立即通知下游，并行改用 mock 对象推进 |
| 测试数据不足 | A 工程师 | 准备 3 套 demo 数据（小/中/大规模） |
| 性能不达标 | 对应工程师 | 立即上报，优化方案需提前规划 |

---

## 验收检查清单 — Phase 1 完成标准

**时间点：** 2026-07-03  

### 数据接入 & 存储（人员 A）
- [ ] collector + adapter + validator + cleaner 全部实现
- [ ] test_ingestion.py 覆盖率 ≥ 85%
- [ ] test_memory_store.py 覆盖率 ≥ 80%
- [ ] 端到端流程测试通过

### 记忆抽取（人员 B）
- [ ] preference_extractor 支持显式 + 隐式偏好
- [ ] knowledge_extractor 支持工具结果、模板提取
- [ ] workflow_extractor 初步支持工作流识别
- [ ] test_extractors.py 覆盖率 ≥ 85%

### 检索系统（人员 C）
- [ ] keyword_retriever 完整可用
- [ ] vector_retriever 接口完整
- [ ] faiss_vector_store 可持久化
- [ ] hybrid_retriever 融合关键词 + 向量
- [ ] reranker 初步支持
- [ ] P99 延迟 ≤ 500ms
- [ ] test_retrieval.py 覆盖率 ≥ 80%

### 遗忘 & 评测（人员 D）
- [ ] forgetter.py 支持软删除、审计
- [ ] preference_eval / retrieval_eval / latency_eval 实现
- [ ] test_evaluation.py 覆盖率 ≥ 80%

### 整体质量
- [ ] 所有单测通过：`pytest tests/ -v`
- [ ] 代码无严重 bug（能跑完 demo 流程）
- [ ] 文档注释清晰
- [ ] Git 提交日志整洁

---

## 下一阶段（Phase 2）

**时间：** 2026-07-04 ～ 2026-07-10

**焦点：** API 路由 + 集成测试 + 性能优化

- 人员 E 实现 `api/routes_*.py`
- 全员集成测试和 bug fix
- 性能瓶颈识别和优化

---

**Phase 1 开发可以开始！** 🚀
