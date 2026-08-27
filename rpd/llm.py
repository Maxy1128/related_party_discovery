"""Minimal OpenAI-compatible Chat Completions Structured Outputs client."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import TypeVar

from pydantic import BaseModel

from rpd.config import Settings
from rpd.http import PublicHttpClient


T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(self, settings: Settings, http: PublicHttpClient | None = None):
        self.settings = settings
        self.http = http or PublicHttpClient(settings)

    def parse(self, messages: list[dict[str, str]], output_type: type[T]) -> T:
        api_key = self.settings.require_llm_key()
        schema = _strict_json_schema(output_type.model_json_schema())
        response = self.http.post_json(
            f"{self.settings.llm_api_base}/chat/completions",
            {
                "model": self.settings.llm_model,
                "messages": messages,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "relationship_extraction",
                        "strict": True,
                        "schema": schema,
                    },
                },
            },
            headers={"Authorization": f"Bearer {api_key}", "Connection": "close"},
            timeout=(
                self.settings.http_connect_timeout_seconds,
                self.settings.llm_read_timeout_seconds,
            ),
        )
        try:
            message = response["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise StructuredOutputError("LLM response has no assistant message.") from exc
        if message.get("refusal"):
            raise StructuredOutputError("LLM refused the extraction request.")
        content = message.get("content")
        if not isinstance(content, str):
            raise StructuredOutputError("LLM response content is not JSON text.")
        try:
            return output_type.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValueError) as exc:
            raise StructuredOutputError("LLM output failed strict schema validation.") from exc


def _strict_json_schema(schema: dict) -> dict:
    """Adapt Pydantic JSON Schema to providers requiring every object key in required."""
    result = deepcopy(schema)

    def visit(node: object) -> None:
        if not isinstance(node, dict):
            return
        properties = node.get("properties")
        if isinstance(properties, dict):
            node["required"] = list(properties)
            for child in properties.values():
                visit(child)
        items = node.get("items")
        if isinstance(items, dict):
            visit(items)
        for key in ("anyOf", "oneOf", "allOf"):
            values = node.get(key)
            if isinstance(values, list):
                for child in values:
                    visit(child)
        defs = node.get("$defs")
        if isinstance(defs, dict):
            for child in defs.values():
                visit(child)

    visit(result)
    return result
