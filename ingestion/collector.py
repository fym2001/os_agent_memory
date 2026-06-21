import json
from pathlib import Path


def load_jsonl(file_path, skip_invalid=False):
    """
    Load raw events from a JSONL file.

    JSONL format:
    each line is a JSON object.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    events = []

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                if skip_invalid:
                    continue
                raise ValueError(f"Invalid JSON at line {line_no}: {e}") from e

            if not isinstance(event, dict):
                if skip_invalid:
                    continue
                raise ValueError(f"Line {line_no} is not a JSON object")

            events.append(event)

    return events