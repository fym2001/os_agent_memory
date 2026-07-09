# B 组 LLM-only 记忆抽取方案

## 1. 当前改动结论

B 侧抽取逻辑已经从：

```text
规则 baseline + LLM 语义增强
```

调整为：

```text
LLM 作为唯一正式语义抽取路径
```

也就是说，`PreferenceExtractor`、`KnowledgeExtractor`、`WorkflowExtractor`、
`ToolExtractor`、`EnvironmentExtractor` 这些旧类名仍然保留，但它们内部不再运行
正则、关键词、频次统计、工具序列规则或路径分类规则。

保留旧类名和旧函数签名的原因是向后兼容团队接口，避免影响 A 侧 `MemoryEvent`
输入和后续存储/检索模块。

## 2. 为什么要这样改

会议反馈的核心是：偏好、知识、工作流这类记忆抽取不能长期依赖硬编码。

硬编码的问题是：

- 自然语言表达变化太多，正则很难覆盖；
- “以后”“默认”“别写太散”这类表达需要语义判断；
- 工具结果、流程模板、长期偏好之间经常混在一起，需要模型归纳；
- 规则里出现 `120`、`240`、`0.9` 这类阈值时，很难解释来源；
- 后期要做泛化评测，规则堆叠会越来越难维护。

因此现在的方案是让 LLM 做判断，代码只负责数据协议和安全边界。

## 3. 新架构

```text
MemoryEvent / MemoryEvent[]
        |
        v
Sanitize before prompt
        |
        v
LLMJsonClient.complete_json(prompt, schema)
        |
        v
LLM structured JSON
        |
        v
CandidateValidator
        |
        v
CandidateMerger
        |
        v
MemoryCandidate[]
```

其中：

- `MemoryEvent` 是 A 侧/ingestion 侧传进来的标准事件；
- `LLMJsonClient` 是模型适配接口，可以接云端模型、本地模型或 fake client；
- `CandidateValidator` 只做安全和结构校验，不做规则抽取；
- `CandidateMerger` 只做去重和冲突标记，不做规则增强；
- 输出仍然是 `MemoryCandidate[]`，方便后续存储模块继续对接。

## 4. 保留了什么，删除了什么

保留：

- 旧函数名；
- 旧返回类型；
- `MemoryEvent` / `MemoryCandidate` 数据结构；
- Phase 0 表结构；
- 敏感信息过滤；
- 候选去重；
- 冲突标记；
- fake LLM 测试方式。

删除或停用：

- 偏好正则；
- FAQ 正则；
- 工具成功率本地统计；
- 工作流关键词边界检测；
- 路径类型硬编码识别；
- `should_call_llm` 里的事件类型捷径；
- `len(text) > 120`、`len(text) > 240`、`confidence >= 0.9` 这类门槛。

## 5. 旧 API 现在怎么工作

### PreferenceExtractor

```python
PreferenceExtractor.extract_from_conversation(event)
PreferenceExtractor.extract_from_tool_result(event)
PreferenceExtractor.extract_explicit_preference(content)
PreferenceExtractor.extract_implicit_preference(events)
```

现在全部调用默认 LLM client，只返回 `MemoryType.PREFERENCE`。

### KnowledgeExtractor

```python
KnowledgeExtractor.extract_from_tool_result(event)
KnowledgeExtractor.extract_from_conversation(event)
KnowledgeExtractor.extract_templates(events)
```

现在全部调用 LLM，由模型判断是否是知识或模板。

### WorkflowExtractor

```python
WorkflowExtractor.detect_workflow_boundary(events)
WorkflowExtractor.extract_tool_sequence(events)
WorkflowExtractor.extract_multi_step_workflow(events)
```

边界和流程都由 LLM 输出，不再用关键词或工具序列规则。

### ToolExtractor

```python
ToolExtractor.extract_tool_pattern(events)
ToolExtractor.calculate_tool_success_rate(tool_name, events)
```

工具经验由 LLM 输出。成功率不再本地统计，而是读取 LLM 输出候选中的
`metadata.success_rate`。

### EnvironmentExtractor

```python
EnvironmentExtractor.extract_from_tool_output(output)
```

环境信息由 LLM 从系统上下文中判断，不再本地根据 key/path 推断。

## 6. LLM 处理前后数据结构

### 6.1 LLM 处理前输入

输入是 `MemoryEvent` 或 `MemoryEvent[]`。

示例：

```json
{
  "event_id": "demo-conv-001",
  "user_id": "user-demo",
  "event_type": "conversation",
  "content": "这种报告以后别写太散，先给结论再展开。"
}
```

工具结果示例：

```json
{
  "event_id": "demo-tool-001",
  "event_type": "tool_result",
  "tool_name": "batch_export",
  "input": {
    "files": ["a.docx", "b.docx"],
    "format": "pdf"
  },
  "output": {
    "status": "success",
    "file": "report.pdf",
    "api_key": "sk-demo-secret"
  }
}
```

### 6.2 入模前脱敏

进入 LLM 前会把敏感字段替换掉：

```json
{
  "output": {
    "status": "success",
    "file": "report.pdf",
    "api_key": "[REDACTED_SECRET]"
  }
}
```

这一步是安全处理，不是记忆抽取规则。

### 6.3 LLM 原始输出

LLM 必须返回结构化 JSON：

```json
{
  "candidates": [
    {
      "is_memory_worthy": true,
      "is_long_term": true,
      "memory_type": "preference",
      "category": "response_order",
      "value": "conclusion_first",
      "scope": "demo",
      "content": "用户偏好报告类内容先给结论再展开",
      "confidence": 0.88,
      "evidence": "这种报告以后别写太散，先给结论再展开。",
      "reason": "LLM 判断这是长期写作偏好。",
      "sensitivity": "none"
    }
  ]
}
```

### 6.4 LLM 处理后输出

代码把 LLM JSON 转为 `MemoryCandidate`：

```json
{
  "memory_type": "preference",
  "key": "preference.response_order.conclusion_first",
  "content": "用户偏好报告类内容先给结论再展开",
  "confidence": 0.88,
  "source": "llm_extracted",
  "metadata": {
    "extraction_method": "llm_semantic",
    "evidence": "这种报告以后别写太散，先给结论再展开。",
    "reason": "LLM 判断这是长期写作偏好。"
  }
}
```

## 7. 本地演示脚本

运行：

```bash
python demo/b_llm_pipeline_demo.py
```

它会打印：

1. LLM 处理前的原始 `MemoryEvent` 数据集；
2. 入模前脱敏后的 JSON；
3. LLM prompt 结构；
4. fake LLM 原始输出 JSON；
5. 校验/脱敏/转换后的 `MemoryCandidate[]`；
6. 旧 B 侧 API 现在的 LLM-only 输出。

## 8. 当前测试命令

```bash
python -m pytest tests/test_extractors.py tests/test_llm_memory_extractor.py tests/test_environment_extractor.py tests/test_tool_extractor.py tests/test_workflow_extractor.py tests/test_ingestion_to_extractors.py -q
```

这些测试使用 fake LLM client，不需要真实 API key，也不需要下载模型。

## 9. 对外汇报话术

可以这样讲：

> 我这次把 B 侧抽取方向从规则增强改成了 LLM-only。旧的函数名和数据结构没有变，
> 但内部不再依赖正则、关键词、频次统计或长度阈值。现在输入统一是 MemoryEvent，
> 入模前先脱敏，LLM 返回结构化 JSON，最后由本地 validator/merger 转成
> MemoryCandidate。这样既满足主管说的“大模型替代硬编码”，又不破坏团队已有接口。
