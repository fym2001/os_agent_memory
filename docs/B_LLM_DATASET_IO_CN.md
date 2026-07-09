# B 组 LLM 处理前后数据集输入输出说明

这份文档用于展示 B 侧记忆抽取在 LLM 化之后，数据从进入抽取器到输出
`MemoryCandidate` 的完整形态。核心结论是：代码不再用硬编码规则决定“提取什么”，
只负责数据整理、脱敏、结构校验、去重和冲突标记。

## 1. 展示命令

在项目根目录运行：

```bash
python demo/b_llm_pipeline_demo.py
```

这个 demo 使用 fake LLM client，不需要真实 API key，也不会访问外部网络。

## 2. 数据流总览

```text
原始数据集 MemoryEvent[]
        |
        v
入模前 JSON 整理与脱敏
        |
        v
LLM prompt + schema
        |
        v
LLM 原始 JSON 输出
        |
        v
本地校验、脱敏、去重、冲突标记
        |
        v
最终 MemoryCandidate[]
```

## 3. LLM 处理前：原始数据集

输入来自 A 侧或 ingestion 侧标准化后的 `MemoryEvent`。

示例一：用户对话事件

```json
{
  "event_id": "demo-conv-001",
  "user_id": "user-demo",
  "session_id": "session-demo",
  "task_id": "task-demo",
  "event_type": "conversation",
  "scenario": "office",
  "source": "conversation",
  "actor": "user",
  "content": "这种报告以后别写太散，先给结论再展开。"
}
```

示例二：工具结果事件

```json
{
  "event_id": "demo-tool-001",
  "user_id": "user-demo",
  "session_id": "session-demo",
  "task_id": "task-demo",
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
  },
  "success": true
}
```

说明：

- 这一步展示的是原始事件对象；
- `api_key` 这种字段可能存在于工具输出里；
- 这里还没有做记忆判断。

## 4. LLM 处理前：实际入模 JSON

进入 LLM 前，代码只做结构整理和安全脱敏，不做“关键词命中”“长度大于多少”
这类抽取判断。

工具输出中的敏感字段会变成：

```json
{
  "output": {
    "status": "success",
    "file": "report.pdf",
    "api_key": "[REDACTED_SECRET]"
  }
}
```

这一步可以向对方说明：

- 脱敏是安全边界，不是记忆抽取规则；
- 模型看到的是可解释、可复用的事件 JSON；
- 密钥、手机号、邮箱等信息不会原样进入 prompt。

## 5. LLM 请求：prompt 和输出契约

Prompt 中明确要求模型做语义判断：

```json
{
  "extraction_policy": [
    "Use semantic understanding to decide whether information is long-term memory.",
    "Do not depend on keyword lists, regex templates, text length thresholds, or rule candidates.",
    "Extract preference, knowledge, workflow, template, tool, environment, safety, profile, task_state, or session_summary memory when supported by evidence.",
    "Mark temporary/current-task-only information as is_memory_worthy=false or is_long_term=false.",
    "Never output credentials or raw secrets. Use redacted evidence when needed.",
    "Keep evidence and reason explicit so reviewers can understand why the memory exists."
  ]
}
```

这一步对应主管关心的点：不是用 `len(text) > 120`、`confidence >= 0.9`
之类阈值决定，而是让 LLM 按语义和证据输出结构化结果。

## 6. LLM 原始输出

LLM 返回 JSON，形态如下：

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

LLM 原始输出里必须包含：

- `memory_type`：记忆类型；
- `content`：可复用记忆内容；
- `confidence`：模型自评置信度；
- `evidence`：来自输入事件的证据；
- `reason`：为什么这是长期记忆；
- `is_memory_worthy` / `is_long_term`：是否值得进入长期记忆。

## 7. LLM 处理后：最终 MemoryCandidate

代码把 LLM JSON 转成团队统一的 `MemoryCandidate`：

```json
{
  "candidate_id": "6d426b361f1d0f4c",
  "user_id": "user-demo",
  "memory_type": "preference",
  "key": "preference.response_order.conclusion_first",
  "content": "用户偏好报告类内容先给结论再展开",
  "scenario": "office",
  "confidence": 0.88,
  "source": "llm_extracted",
  "source_events": ["demo-conv-001", "demo-tool-001"],
  "source_summaries": ["这种报告以后别写太散，先给结论再展开。"],
  "tags": ["llm", "preference", "response_order"],
  "metadata": {
    "extraction_method": "llm_semantic",
    "schema_version": 2,
    "evidence": "这种报告以后别写太散，先给结论再展开。",
    "reason": "LLM 判断这是长期写作偏好。",
    "sensitivity": "none"
  }
}
```

本地后处理负责：

- JSON 解析失败保护；
- 空内容过滤；
- 非长期记忆过滤；
- 敏感凭据候选拒绝；
- 输出字段补齐；
- 相同 key/content 去重；
- 相同 key 不同内容时标记冲突。

注意：这些是数据质量和安全处理，不是用规则替代 LLM 抽取。

## 8. 当前 demo 能展示的三类输出

| 输入证据 | LLM 判断 | 最终输出 |
| --- | --- | --- |
| “这种报告以后别写太散，先给结论再展开。” | 长期表达风格偏好 | `preference.response_order.conclusion_first` |
| `batch_export` 工具输入输出 | 可复用工具使用案例 | `knowledge.tool_case.batch_export` |
| 对话偏好 + 工具导出结果 | 可复用报告生成流程 | `workflow.report_generation.conclusion_then_export` |

同时 demo 还故意模拟一个错误候选：

```json
{
  "memory_type": "knowledge",
  "category": "credential",
  "content": "用户 API key 是 sk-demo-secret",
  "sensitivity": "api_key"
}
```

这个候选会被本地校验层拒绝，不会进入最终 `MemoryCandidate[]`。

## 9. 对外说明话术

可以这样讲：

> 我这里展示的是 B 侧 LLM 处理前后的数据形态。处理前是标准
> MemoryEvent，包括用户对话和工具结果；入模前会先做脱敏，保证敏感字段不会原样进模型；
> LLM 负责判断哪些内容能成为长期记忆，并返回结构化 JSON；代码再把这个 JSON 校验、
> 去重、补齐来源，转换成 MemoryCandidate。现在抽取判断不再依赖正则、关键词、频次统计
> 或长度阈值，代码只保留安全和工程边界。
