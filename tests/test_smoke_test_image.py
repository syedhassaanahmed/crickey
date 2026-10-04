from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_test_image.py"
_SPEC = importlib.util.spec_from_file_location("smoke_test_image", _SCRIPT)
assert _SPEC is not None
smoke_test_image = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(smoke_test_image)

HEALTH_BODY = smoke_test_image.HEALTH_BODY
SmokeError = smoke_test_image.SmokeError
assert_tool_names = smoke_test_image.assert_tool_names


def test_assert_tool_names_accepts_exact_set() -> None:
    assert_tool_names(
        [
            "leaderboard",
            "better_than_player",
            "player_record",
            "find_player",
            "query_stats",
        ]
    )


def test_assert_tool_names_rejects_missing_or_extra_tool() -> None:
    with pytest.raises(SmokeError, match="tool names"):
        assert_tool_names(["query_stats", "extra"])


def test_health_body_is_exact_transport_response() -> None:
    assert HEALTH_BODY == b'{"status":"ok"}'
