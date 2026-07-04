# B 组 LLM 增强记忆抽取方案

## 1. 为什么要改

原来 B 组的记忆抽取模块主要是规则和启发式实现，比如通过关键词、正则和频次统计来识别用户偏好、知识和工作流。

这个方案在第一阶段是合理的，因为它有几个优点：

- 稳定、可复现；
- 容易写单元测试；
- 不依赖外部模型；
- 方便和 A 组的 `MemoryEvent` 输入对接；
- 输出统一为 `MemoryCandidate`，后续存储和检索模块可以直接使用。

但是如果后续继续只靠硬编码规则，会遇到明显问题。用户真实表达通常不是固定模板，例如：

```text
这种报告以后别写太散，先给结论再展开。
类似这种流程以后可以直接复用。
这种文件下次别弄成一大段，最好整理成表格。
```

这些表达靠正则很难长期覆盖。所以这次调整的目标不是推翻原来的规则抽取，而是把 B 组模块升级成：

```text
规则 baseline + LLM 语义抽取 + 校验合并
```

这样既保留第一阶段稳定、可测的成果，也能往大模型应用方向扩展。

## 2. 参考的开源项目

这次设计主要参考了三个方向：

| 开源项目 | 参考点 | 对本项目的启发 |
| --- | --- | --- |
| LangMem | 从对话中提取、更新和整合长期记忆 | 记忆不是简单保存聊天记录，而是从上下文中抽取可复用信息 |
| Mem0 | 用户偏好、个性化长期记忆、跨会话记忆 | 偏好记忆要以用户为中心，支持长期复用 |
| Graphiti | 事件溯源、时间关系、知识演化和冲突处理 | 每条记忆都应该保留来源证据，并考虑后续冲突和更新 |

这里没有直接引入这些项目的依赖，只是参考它们的架构思想。

## 3. 总体架构

当前设计是一个混合管线：

```text
MemoryEvent / MemoryEvent[]
        |
        |-- 原有规则抽取器
        |     - PreferenceExtractor
        |     - KnowledgeExtractor
        |     - WorkflowExtractor
        |     - ToolExtractor
        |
        |-- 可选 LLM 语义抽取器
        |     - 复杂偏好理解
        |     - 工具结果总结
        |     - 可复用知识抽取
        |     - 工作流归纳
        |
        v
CandidateValidator
        |
        |-- 过滤临时指令
        |-- 过滤非长期记忆
        |-- 敏感信息脱敏或拒绝
        |-- 修正 confidence
        |
        v
CandidateMerger
        |
        |-- 规则结果和 LLM 结果去重
        |-- 两边同时命中时提高置信度
        |-- 标注潜在冲突
        |
        v
MemoryCandidate[]
```

核心思路是：

- 规则层负责稳定、明确、低成本的抽取；
- LLM 层负责复杂自然语言理解；
- Validator 负责防止误存、脏数据和敏感信息；
- Merger 负责合并、去重和冲突标注；
- 最终输出仍然是统一的 `MemoryCandidate`。

## 4. 原来的硬编码规则保留在哪里

原来的规则没有删除，主要仍然保留在这些模块里：

```text
extractors/preference_extractor.py
extractors/knowledge_extractor.py
extractors/workflow_extractor.py
extractors/tool_extractor.py
extractors/environment_extractor.py
```

这些规则现在的定位是 baseline，也就是稳定基线。

它们主要处理明确、简单、可测试的场景，例如：

```text
以后都用 Markdown 输出。
默认用中文回答。
回答尽量简洁。
用户连续多次使用 bash 工具。
用户多次使用 format=pdf 参数。
```

这些情况没有必要每次都调用大模型，因为规则识别更稳定、更便宜，也更容易验收。

## 5. 新增了什么

这次新增的核心文件是：

```text
extractors/llm_memory_extractor.py
```

里面主要新增了四个部分：

### 5.1 LLMMemoryExtractor

负责把大模型返回的结构化 JSON 转成 `MemoryCandidate`。

它不直接绑定某个模型 SDK，而是通过一个统一接口接入：

```python
complete_json(prompt, schema)
```

这样后续可以接：

- 云端大模型；
- 本地大模型；
- 团队封装的模型服务；
- 测试用 fake client。

### 5.2 HybridMemoryExtractor

这是混合抽取入口。

它会先调用原有规则抽取器，再根据情况决定是否调用 LLM。

如果没有传入 LLM client，它就只走规则版，不影响原系统。

### 5.3 CandidateValidator

负责对候选记忆做校验，防止 LLM 输出直接入库。

主要处理：

- 临时指令过滤；
- 非长期记忆过滤；
- 低置信度过滤；
- API key、token、password 等敏感内容拒绝；
- 手机号、邮箱等信息脱敏；
- 根据 evidence 完整性修正 confidence。

### 5.4 CandidateMerger

负责合并规则抽取和 LLM 抽取结果。

主要处理：

- 相同候选去重；
- 规则和 LLM 同时命中时提高置信度；
- 合并来源事件；
- 标注同类型下可能冲突的候选。

## 6. LLM 输出格式

LLM 不直接返回一段自然语言，而是要求返回结构化 JSON，例如：

```json
{
  "candidates": [
    {
      "is_memory_worthy": true,
      "is_long_term": true,
      "memory_type": "preference",
      "category": "response_order",
      "value": "conclusion_first",
      "scope": "report_writing",
      "content": "用户偏好报告类内容先给结论再展开",
      "confidence": 0.84,
      "evidence": "这种报告还是先给结论好一点",
      "reason": "用户表达了可复用的后续报告写作偏好",
      "sensitivity": "none"
    }
  ]
}
```

这样做的原因是：

- 输出可控；
- 方便测试；
- 方便校验；
- 方便后续入库；
- 可以保留 evidence 和 reason，便于解释。

## 7. 什么时候调用 LLM

不是所有事件都调用 LLM。

当前策略是：

- 明确规则能高置信识别的，不调用 LLM；
- 规则没覆盖但有偏好信号的，调用 LLM；
- 工具结果较长、可能包含可复用知识的，调用 LLM；
- session 级工作流归纳，适合调用 LLM；
- 普通临时任务、空内容、明显噪声，不调用 LLM。

这样可以控制成本和延迟，也能保证规则 baseline 继续发挥作用。

## 8. 和原 B 组需求的兼容性

这次改动没有破坏原来的 B 组要求：

- 不改已有函数签名；
- 不改 `MemoryEvent`；
- 不改 `MemoryCandidate`；
- 不改 `core/constants.py`；
- 不改 Phase 0 表结构；
- 原来的 `PreferenceExtractor`、`KnowledgeExtractor`、`WorkflowExtractor` 仍然可以单独使用；
- 新增 LLM 管线只是可选增强层。

也就是说，当前方案可以向后兼容。

## 9. 当前测试情况

本地 B 组相关测试结果：

```text
45 passed
```

覆盖率结果：

```text
TOTAL coverage: 88%
llm_memory_extractor.py coverage: 92%
```

新增测试使用的是 fake LLM client，不依赖真实 API，不需要模型 key，也不需要下载额外模型。

测试覆盖了：

- LLM 识别复杂自然语言偏好；
- 高置信规则命中时跳过 LLM；
- 规则和 LLM 同时命中时合并并提高置信度；
- 临时指令不进入长期记忆；
- 敏感信息过滤和脱敏；
- 工具结果抽取为知识或模板；
- session 级工作流归纳；
- 冲突候选标注。

## 10. 后续建议

后续建议分三步推进。

第一步，先确认团队是否认可这个混合架构：

```text
规则 baseline + LLM 语义抽取 + 校验合并
```

第二步，补评测数据集，而不是只依赖单元测试。

数据集建议包括：

- 明确偏好；
- 隐式偏好；
- 临时指令；
- 敏感信息；
- 冲突偏好；
- 可复用工具流程；
- 可复用知识模板。

评估指标可以包括：

- precision；
- recall；
- false positive rate；
- conflict rate；
- latency；
- fallback rate。

第三步，再接真实模型 adapter。

可以接：

- 云端模型；
- 本地模型；
- 学校或团队已有模型服务；
- mock 模型用于自动化测试。

## 11. 汇报时可以这样总结

这次修改的核心不是把硬编码全部删掉，而是调整它的定位。

原来的规则抽取继续作为稳定 baseline，负责简单、明确、可测试的情况；新增 LLM 语义层处理复杂自然语言、工具结果总结和工作流归纳；最后通过 validator 和 merger 控制误存、敏感信息、去重和冲突问题。

整体方向是把 B 组抽取模块从单纯规则识别，升级成更适合后续 Agent Memory 的混合记忆抽取管线。
