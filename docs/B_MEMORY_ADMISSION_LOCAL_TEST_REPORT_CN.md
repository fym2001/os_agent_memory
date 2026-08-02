# B 侧记忆准入功能本地测试报告

## 基本信息

- 测试日期：2026-08-02
- 本地分支：`feat/b-memory-admission`
- 基线：`feat/b-llm-only-memory-extractors`（commit `55438cf`）
- 提交流程：本地验证通过后推送个人 fork，并向团队 `master` 提交独立 PR
- Python：3.13.5
- pytest：8.3.4

## 本次实现

新增 `MemoryCandidate -> MemoryRecord` 正式入库链路，覆盖：

- LLM 语义准入和冲突判断。
- 创建、语义去重、合并、替换、场景并存、待确认和拒绝。
- 稳定 ID 与重复请求幂等。
- 版本递增和旧版本 `SUPERSEDED`。
- 合法生命周期状态流转。
- SQLite WAL、写事务、并发快照校验和事务回滚。
- 敏感信息保护、模型输出结构校验和模型异常时的失败关闭。
- `MemoryEvent -> LLM extractor -> MemoryCandidate -> MemoryRecord` 对接测试。

未修改核心模型字段、现有函数签名、已有枚举值和 Phase 0 表结构。

## 测试结果

### 1. 入库专项与原存储回归

```powershell
python -m pytest tests/test_conflict_resolver.py tests/test_memory_admission.py tests/test_memory_store.py -q
```

结果：`62 passed`。

### 2. B 侧相关链路回归

```powershell
python -m pytest tests/test_conflict_resolver.py tests/test_memory_admission.py tests/test_memory_store.py tests/test_extractors.py tests/test_llm_memory_extractor.py tests/test_ingestion_to_extractors.py -q
```

结果：`81 passed`。

### 3. 新增入库模块覆盖率

```powershell
python -m coverage run -m pytest tests/test_conflict_resolver.py tests/test_memory_admission.py tests/test_memory_store.py -q
python -m coverage report -m --include="memory/admission.py,memory/conflict_resolver.py,memory/version_manager.py,memory/lifecycle_state.py"
```

结果：

| 模块 | 覆盖率 |
| --- | ---: |
| `memory/admission.py` | 89% |
| `memory/conflict_resolver.py` | 90% |
| `memory/lifecycle_state.py` | 100% |
| `memory/version_manager.py` | 100% |
| 合计 | 90% |

满足原任务要求的覆盖率不低于 85%。

### 4. 全量测试

```powershell
python -m pytest tests -q --tb=short --disable-warnings
```

结果：`179 passed, 5 failed`。

5 项失败全部来自 `tests/test_flow.py`，共同原因是仓库缺少：

```text
E:\OS\data\raw\office_demo_events.jsonl
```

该问题在本次开发前已经存在，与新增入库代码无关。本次未擅自创建或修改 A 侧演示数据。

## 关键测试场景

- 首次候选正常生成 `ACTIVE/version=1` 正式记忆。
- 同一候选重复提交不重复入库，也不重复调用 LLM。
- 语义相同但表述不同的候选复用已有记录。
- 新偏好替换旧偏好：旧记录 `SUPERSEDED`，新记录 `ACTIVE/version=2`。
- 信息互补时生成合并后的新版本。
- 不同场景的记忆允许同时保持 `ACTIVE`。
- LLM 输出非法或要求人工审核时进入 `PENDING`。
- LLM 不可用时不修改数据库。
- 敏感候选在调用 LLM 前被拒绝。
- 两个线程同时提交同一候选时只产生一条记录。
- 替换过程中模拟写入失败时，事务回滚且旧记忆仍保持 `ACTIVE`。
- 生命周期非法状态流转会被拒绝。

## 演示效果

```powershell
python demo\b_memory_admission_demo.py
```

演示会依次打印：

1. 入库前的 `MemoryCandidate`。
2. 脱敏后发送给 LLM 的候选和已有记忆。
3. Fake LLM 的原始结构化决策。
4. 入库后的 `AdmissionResult` 和正式 `MemoryRecord`。
5. PDF 偏好被 Word 偏好替换后的两个版本。
6. 相同候选重试的幂等结果。
7. `ACTIVE -> ARCHIVED -> ACTIVE` 生命周期流转。

演示使用临时 SQLite 和 Fake LLM，不访问外部服务，退出后自动删除临时数据库。

## 当前限制与对接点

- Phase 0 `memories` 表没有 `source_events`、`tags`、`metadata`、`supersedes` 和 `vector_id` 列，因此这些扩展信息只在本次返回对象中完整保留，重新从 SQLite 读取时只能恢复原表字段。
- 当前正式写入目标为 SQLite；仓库现有向量存储实现仍为空，待 C 侧提供稳定适配器后再接入。
- D 侧草稿 PR #4 修改的是入库后的生命周期、整合和遗忘流程；本分支使用新的 `memory/admission.py` 与 `memory/lifecycle_state.py`，避免直接覆盖 D 侧 `memory/lifecycle.py`。
- 合并前应以届时最新 `master` 为基线重新执行全量测试和 B-D 对接测试。
