import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_event_extractor as extractor  # noqa: E402


def _mock_client(events: list) -> MagicMock:
    client = MagicMock()
    payload = json.dumps({"events": events})
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=payload))],
    )
    return client


def test_extract_events_returns_parsed_events():
    client = _mock_client([{
        "actor": "PLA", "action": "中間線越境", "target": None, "location": "台湾海峡中間線",
        "start_at": "2026-08-01T00:00:00+00:00", "end_at": None, "quantities": {"機数": 10},
        "event_type": "military_activity", "confidence": 0.8, "unknown_fields": ["target"],
        "claim_text": "PLA機10機が中間線を越境した", "quote": "10 PLA aircraft crossed the median line.",
    }])

    events = extractor.extract_events(
        client, "gpt-4o", "10 PLA aircraft crossed the median line today.", "taiwan_strait",
    )

    assert len(events) == 1
    assert events[0]["actor"] == "PLA"
    assert events[0]["quote"] == "10 PLA aircraft crossed the median line."


def test_extract_events_empty_text_returns_empty_list_without_calling_llm():
    client = _mock_client([])
    assert extractor.extract_events(client, "gpt-4o", "", "taiwan_strait") == []
    client.chat.completions.create.assert_not_called()


def test_extract_events_with_indicator_choices_passes_through_indicator_code():
    client = _mock_client([{
        "actor": "PLA", "action": "中間線越境", "claim_text": "PLA機が越境した",
        "quote": "quote", "indicator_code": "MIL-01",
    }])

    events = extractor.extract_events(
        client, "gpt-4o", "PLA aircraft crossed the median line.", "taiwan_strait",
        indicator_choices=[{"code": "MIL-01", "name": "PLA航空活動・中間線越境"},
                            {"code": "MAR-01", "name": "中国海警・中国公船の活動"}],
    )

    assert events[0]["indicator_code"] == "MIL-01"
    sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "MIL-01" in sent_prompt and "MAR-01" in sent_prompt


def test_build_event_schema_restricts_indicator_code_enum():
    schema = extractor._build_event_schema(["MIL-01", "MAR-01"])
    field = schema["properties"]["events"]["items"]["properties"]["indicator_code"]
    assert field["enum"] == ["MIL-01", "MAR-01", None]
