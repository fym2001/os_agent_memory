# B-side LLM-only memory extraction pipeline

## Current direction

B-side semantic memory extraction has moved from:

```text
rule-oriented extraction
```

to:

```text
LLM as the only semantic extraction path
```

The old public class names and method signatures are kept for Phase 1
compatibility, but their internal extraction logic no longer depends on
hardcoded regexes, keyword lists, frequency rules, event-type shortcuts,
text-length thresholds, or local confidence gates.

## External design ideas mapped to this project

| Reference idea | Adopted project design |
| --- | --- |
| LangMem: LLM-assisted long-term memory extraction and consolidation | LLM receives `MemoryEvent[]` and returns structured candidate memory |
| Mem0: user-scoped long-term preference and personalization memory | candidates preserve `user_id`, `session_id`, `task_id`, scope, confidence, evidence, and reason |
| Graphiti: temporal/provenance-aware evolving facts | candidates preserve source event ids, event timestamps, evidence, and possible conflict metadata |

These are design references only. The local code does not import a specific
model SDK and does not call a real model by itself.

## Architecture

```text
MemoryEvent / MemoryEvent[]
        |
        v
Sanitize and package event payloads
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

## Local modules

- `extractors/llm_memory_extractor.py`
  - `LLMJsonClient`: minimal protocol for a JSON-capable model adapter.
  - `set_default_llm_client(...)`: process-local adapter registration used by
    legacy static extractor signatures.
  - `LLMMemoryExtractor`: packages events, calls the injected LLM client, and
    converts model JSON into `MemoryCandidate`.
  - `LLMWorkflowBoundaryExtractor`: asks the model for workflow boundaries.
  - `CandidateValidator`: validates model output, rejects credential-like
    non-safety candidates, redacts common sensitive values, and filters
    candidates explicitly marked non-long-term by the model.
  - `CandidateMerger`: de-duplicates candidates and annotates possible
    conflicts.

- Thin compatibility facades:
  - `extractors/preference_extractor.py`
  - `extractors/knowledge_extractor.py`
  - `extractors/workflow_extractor.py`
  - `extractors/tool_extractor.py`
  - `extractors/environment_extractor.py`

## Compatibility with B task signatures

The following methods remain available:

- `PreferenceExtractor.extract_from_conversation(...)`
- `PreferenceExtractor.extract_from_tool_result(...)`
- `PreferenceExtractor.extract_explicit_preference(...)`
- `PreferenceExtractor.extract_implicit_preference(...)`
- `KnowledgeExtractor.extract_from_tool_result(...)`
- `KnowledgeExtractor.extract_from_conversation(...)`
- `KnowledgeExtractor.extract_templates(...)`
- `WorkflowExtractor.extract_tool_sequence(...)`
- `WorkflowExtractor.extract_multi_step_workflow(...)`
- `WorkflowExtractor.detect_workflow_boundary(...)`
- `ToolExtractor.calculate_tool_success_rate(...)`
- `ToolExtractor.extract_tool_pattern(...)`
- `EnvironmentExtractor.extract_from_tool_output(...)`

If no default LLM client is configured, these methods return empty results
instead of falling back to hardcoded extraction.

## Prompt/output contract

The LLM must return JSON shaped like:

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

The local pipeline converts this into normal `MemoryCandidate` objects.

## What was intentionally removed

- preference regex rules;
- knowledge/FAQ keyword extraction;
- repeated-task frequency extraction;
- workflow keyword boundary detection;
- path/category environment heuristics;
- event-type shortcuts for whether to call an LLM;
- `len(text) > 120`, `len(text) > 240`, `confidence >= 0.9`, and similar
  unexplained thresholds.

Remaining regexes are limited to secret/contact redaction and slug/id
normalization. Remaining numeric logic is limited to structural validation,
confidence clamping to `[0, 1]`, and boundary index checking.

## Verification

Targeted tests:

```powershell
python -m pytest tests/test_extractors.py tests/test_llm_memory_extractor.py tests/test_environment_extractor.py tests/test_tool_extractor.py tests/test_workflow_extractor.py tests/test_ingestion_to_extractors.py -q
```

Coverage:

```powershell
python -m coverage run -m pytest tests/test_extractors.py tests/test_llm_memory_extractor.py tests/test_environment_extractor.py tests/test_tool_extractor.py tests/test_workflow_extractor.py tests/test_ingestion_to_extractors.py -q
python -m coverage report -m --include="extractors/*"
```

Input/output demo:

```powershell
python demo\b_llm_pipeline_demo.py
```

The demo prints raw `MemoryEvent[]`, sanitized prompt JSON, fake LLM JSON, and
final `MemoryCandidate[]`.
