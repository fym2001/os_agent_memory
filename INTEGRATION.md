# Extractor integration contract

## Shared boundary

The B-side extractors consume `core.models.MemoryEvent` and return
`core.models.MemoryCandidate`. They do not alter `core/constants.py`,
`core/models.py`, or the Phase 0 SQLite schema.

The A-to-B contract is:

```text
Raw payload
  -> ingestion.collector.create_raw_event(...)
  -> ingestion.adapter.raw_event_to_memory_event(...)
  -> B-side extractor
  -> MemoryCandidate[]
```

Callers should provide `user_id`, `session_id`, and `task_id` for tenant
isolation and concurrent task separation. Timestamps must be UTC or
timezone-aware values accepted by `MemoryEvent`.

## B-side extractor semantics

The public class and method names remain compatible with the Phase 1 task
schedule:

- `PreferenceExtractor`
- `KnowledgeExtractor`
- `WorkflowExtractor`
- `ToolExtractor`
- `EnvironmentExtractor`

The implementation is now LLM-only for memory extraction. These classes do not
use regex templates, keyword lists, frequency counters, event-type shortcuts,
text-length thresholds, or locally computed confidence gates to decide what
memory should be extracted.

All semantic extraction is delegated to a configured JSON-capable LLM adapter:

```python
from extractors.llm_memory_extractor import set_default_llm_client

set_default_llm_client(client)  # client.complete_json(prompt, schema) -> JSON
```

If no LLM client is configured, legacy static methods return empty results
rather than silently falling back to hardcoded extraction rules. This keeps
failure behavior explicit for integration and evaluation.

## Pipeline

```text
MemoryEvent / MemoryEvent[]
        |
        v
Prompt payload packaging and secret/contact redaction
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

Local post-processing is limited to engineering boundaries:

- prompt packaging from `MemoryEvent`;
- credential/contact redaction before prompt and after model output;
- JSON/schema conversion into `MemoryCandidate`;
- empty/non-long-term candidate filtering based on model output flags;
- duplicate merging and conflict annotation for already-produced candidates.

It is not a rule-based memory extractor.

## A-side integration verification

The test `tests/test_ingestion_to_extractors.py` verifies that ingestion-style
`MemoryEvent` objects can directly drive B-side LLM extractors for:

- preference extraction from conversation events;
- knowledge/tool extraction from tool result events;
- environment extraction from metadata/tool-output dictionaries.

The test uses a fake LLM client, so it is deterministic and does not require a
network connection, API key, or model download.

## Verification commands

Targeted B-side verification:

```powershell
python -m pytest tests/test_extractors.py tests/test_llm_memory_extractor.py tests/test_environment_extractor.py tests/test_tool_extractor.py tests/test_workflow_extractor.py tests/test_ingestion_to_extractors.py -q
```

Coverage:

```powershell
python -m coverage run -m pytest tests/test_extractors.py tests/test_llm_memory_extractor.py tests/test_environment_extractor.py tests/test_tool_extractor.py tests/test_workflow_extractor.py tests/test_ingestion_to_extractors.py -q
python -m coverage report -m --include="extractors/*"
```

Dataset input/output demo:

```powershell
python demo\b_llm_pipeline_demo.py
```
