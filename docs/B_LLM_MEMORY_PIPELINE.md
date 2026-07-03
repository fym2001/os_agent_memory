# B-side LLM-enhanced memory extraction plan

## Why this change

The original B-side extractors are rule-first implementations for Phase 1.
They are still useful because they are deterministic, cheap, easy to test, and
compatible with `MemoryEvent -> MemoryCandidate`.  However, preference and
knowledge extraction are semantically open-ended.  Continuing to add regexes is
not a scalable final design.

This local update keeps the existing rule extractors as a stable baseline and
adds an optional LLM semantic layer.  The code does not import any model SDK and
does not call a real model by itself.  Callers inject an object implementing
`complete_json(prompt, schema)`.

## External design ideas mapped to this project

| Reference idea | Adopted project design |
| --- | --- |
| LangMem: use an LLM to expand or consolidate long-term memory from conversations and current memory state | `LLMMemoryExtractor` receives original events plus rule candidates, then returns structured candidate memory |
| Mem0: user-scoped long-term preference and personalization memory | generated candidates keep `user_id`, `session_id`, `task_id`, scope, confidence, and evidence |
| Graphiti: temporal/provenance-aware evolving facts | LLM candidates include `provenance`, `source_event_ids`, evidence, possible conflicts, and temporal event metadata |

## Architecture

```text
MemoryEvent(s)
   |
   |-- existing rule extractors
   |     - PreferenceExtractor
   |     - KnowledgeExtractor
   |     - WorkflowExtractor
   |     - ToolExtractor
   |
   |-- optional LLM semantic extractor
   |     - schema-constrained JSON output
   |     - injected LLM client
   |     - no vendor dependency
   |
   v
CandidateValidator
   - remove temporary/non-long-term candidates
   - reject credential-like content
   - redact common sensitive values
   - adjust confidence by evidence completeness
   |
   v
CandidateMerger
   - de-duplicate rule and LLM candidates
   - boost candidates corroborated by both paths
   - annotate possible conflicts under the same type/category
   |
   v
MemoryCandidate[]
```

## New local modules

- `extractors/llm_memory_extractor.py`
  - `LLMJsonClient`: minimal protocol for a JSON-capable model adapter.
  - `LLMMemoryExtractor`: converts model JSON into `MemoryCandidate`.
  - `HybridMemoryExtractor`: rule-first extractor with optional LLM enrichment.
  - `CandidateValidator`: filters, redacts, and confidence-adjusts candidates.
  - `CandidateMerger`: de-duplicates and merges rule/LLM candidates.

- `tests/test_llm_memory_extractor.py`
  - uses a fake LLM client;
  - no network, no API key, no model dependency.

## Compatibility with original B tasks

The original B function signatures remain unchanged:

- `PreferenceExtractor.extract_from_conversation(...)`
- `PreferenceExtractor.extract_explicit_preference(...)`
- `PreferenceExtractor.extract_implicit_preference(...)`
- `KnowledgeExtractor.extract_from_tool_result(...)`
- `KnowledgeExtractor.extract_from_conversation(...)`
- `KnowledgeExtractor.extract_templates(...)`
- `WorkflowExtractor.extract_tool_sequence(...)`
- `WorkflowExtractor.extract_multi_step_workflow(...)`
- `WorkflowExtractor.detect_workflow_boundary(...)`

The LLM path is additive:

```python
HybridMemoryExtractor.extract_from_conversation(event, llm_client=None)
HybridMemoryExtractor.extract_from_tool_result(event, llm_client=None)
HybridMemoryExtractor.extract_from_session(events, llm_client=None)
```

If `llm_client` is `None`, the pipeline remains rule-only.  If the model fails
in production, callers can fall back to the existing rule extractors without
breaking downstream storage.

## Prompt/output contract

The LLM returns JSON shaped like:

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

The pipeline then validates and converts this into a normal `MemoryCandidate`.

## Trigger policy

The LLM layer is not called for every event.  It is intended for:

- natural language that has preference signals but no confident rule match;
- longer tool results or summaries that may contain reusable knowledge;
- session-level workflow extraction and compaction;
- user feedback or task summaries where the long-term value is semantic.

High-confidence simple rules, such as `以后都用 Markdown 输出`, can skip LLM
calls to reduce cost and latency.

## Current verification

Local verification command:

```powershell
python -m pytest tests/test_extractors.py tests/test_workflow_extractor.py tests/test_tool_extractor.py tests/test_environment_extractor.py tests/test_ingestion_to_extractors.py tests/test_llm_memory_extractor.py -q
```

Current result:

```text
38 passed
```

## Next engineering steps before upload

1. Decide whether the team wants this in the same B PR line or a separate
   Phase 2 branch.
2. Agree on a production LLM adapter interface:
   - cloud model;
   - local model;
   - mock-only for evaluation.
3. Build a preference/knowledge/workflow evaluation dataset:
   - clear long-term preferences;
   - implicit repeated behaviors;
   - temporary instructions that must not be stored;
   - sensitive content that must be rejected or redacted.
4. Add offline metrics:
   - precision;
   - recall;
   - false-positive rate;
   - conflict rate;
   - latency and fallback rate.

