# B 侧记忆准入与正式入库设计

## 1. 任务边界

本模块承接 B 侧抽取器输出的 `MemoryCandidate`，将经过验证和冲突判断的候选转成正式 `MemoryRecord`。

完整链路为：

```text
MemoryEvent
  -> LLM-only extractors
  -> MemoryCandidate
  -> MemoryAdmissionService
  -> MemoryRecord
  -> SQLite / 后续向量库
```

本实现不修改已有抽取器函数签名、不修改核心字段名、不删除枚举，也不改变 Phase 0 SQLite 表结构。

## 2. LLM 与程序的职责划分

LLM 负责语义判断：

- `create`：没有等价或冲突的已有记忆，创建正式记忆。
- `duplicate`：与已有记忆语义相同，不重复写入。
- `merge`：内容互补，输出合并后的标准内容并创建新版本。
- `replace`：新事实更新或否定旧事实，新版本生效、旧版本被替代。
- `coexist`：场景或作用域不同，两条记忆同时有效。
- `pending`：证据不足或需要人工确认。
- `reject`：不应进入长期记忆。

确定性程序负责工程安全：

- 校验 LLM JSON 结构和目标记忆 ID。
- 入模前遮盖密钥等敏感字段。
- 拒绝敏感或不完整候选。
- 生成稳定 `memory_id`，保证重试幂等。
- 管理版本号和合法生命周期状态流转。
- 使用 SQLite 写事务保证冲突处理和入库的原子性。
- 并发期间发现快照变化时重新执行语义判断。
- LLM 不可用时保持 `PENDING`，不进行猜测性写入。

代码中不存在以文本长度、关键词或固定置信度阈值替代语义判断的入库规则。

## 3. 生命周期

准入层支持以下主要状态流转：

```text
PENDING -> ACTIVE / REJECTED / DELETED
ACTIVE -> SUPERSEDED / ARCHIVED / EXPIRED / DELETED
ARCHIVED -> ACTIVE / DELETED
SUPERSEDED -> ARCHIVED / DELETED
EXPIRED -> ARCHIVED / DELETED
REJECTED -> DELETED
```

相同状态的重复写入视为幂等操作；其他非法流转会抛出 `InvalidMemoryTransition`。

## 4. 冲突与版本处理

冲突判断输入包括：

- 当前 `MemoryCandidate` 完整结构。
- 同一 `user_id + memory_type + key` 下所有 `ACTIVE` 记忆。
- 不同 `scenario` 的记录也会提供给 LLM，用于判断是否可以并存。

`merge` 和 `replace` 会：

1. 校验模型引用的目标仍然处于 `ACTIVE`。
2. 将目标记录更新为 `SUPERSEDED`。
3. 创建版本号递增的新 `ACTIVE` 记录。
4. 在本次返回的 `MemoryRecord.supersedes` 中给出被替代记录 ID。

Phase 0 表没有 `supersedes`、`metadata`、`tags` 等列，因此这些扩展信息目前只保留在返回对象中；持久层仍严格使用原表字段。后续若团队统一允许扩表，再补充完整的持久化追溯关系。

## 5. 并发和可用性

- SQLite 使用 WAL 模式，读写可并行。
- 正式写入使用 `BEGIN IMMEDIATE`，冲突更新和新版本插入在同一事务完成。
- 调用 LLM 时不持有数据库写锁，避免模型延迟阻塞其他请求。
- 写入前重新核对活跃记忆快照；快照变化会重新读取并重新让 LLM 判断。
- `memory_id` 根据候选身份、场景和最终内容稳定生成，相同事实重试不会生成重复行。
- LLM 调用异常时返回 `PENDING` 且不修改数据库，防止错误覆盖已有记忆。

## 6. 文件说明

- `memory/admission.py`：准入服务、SQLite 事务和生命周期操作入口。
- `memory/conflict_resolver.py`：LLM 冲突 prompt、JSON Schema 和输出校验。
- `memory/version_manager.py`：稳定记忆 ID 和版本号计算。
- `memory/lifecycle_state.py`：合法状态流转定义。
- `tests/test_memory_admission.py`：正式入库、冲突、异常和并发测试。
- `tests/test_conflict_resolver.py`：LLM 决策契约测试。
- `demo/b_memory_admission_demo.py`：投屏展示的入库前后数据。

## 7. 本地运行

只看入库前后效果：

```powershell
cd E:\OS
python demo\b_memory_admission_demo.py
```

运行新增测试：

```powershell
python -m pytest tests\test_conflict_resolver.py tests\test_memory_admission.py -v
```

运行入库相关回归测试：

```powershell
python -m pytest tests\test_conflict_resolver.py tests\test_memory_admission.py tests\test_memory_store.py -v
```

演示和测试均使用 Fake LLM，不访问外部模型，也不需要额外安装依赖。生产环境只需要注入实现 `complete_json(prompt, schema)` 的真实 LLM 客户端。
