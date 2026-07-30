from __future__ import annotations

import importlib
import json


errors = importlib.import_module("Turning-Good-Agent.web.backend.http_errors")


def test_config_validation_response_has_top_level_field_errors() -> None:
    response = errors.config_validation_response({"runtime.max_tool_rounds": "必须大于 0"})

    assert response.status_code == 422
    assert json.loads(response.body) == {"field_errors": {"runtime.max_tool_rounds": "必须大于 0"}}
