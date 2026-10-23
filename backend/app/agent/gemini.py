"""Minimal Gemini API client (REST generateContent with function calling).

Request/response shapes follow Google's Gemini REST function-calling
examples: `tools[].functionDeclarations[]` with an OpenAPI-subset
`parameters` schema, `toolConfig.functionCallingConfig` (mode ANY forces a
function call), and `candidates[0].content.parts[].functionCall`. In
multi-turn use the model's content is appended unchanged (so any thought
signatures it carries are returned to the API), followed by a turn of
`functionResponse` parts.

The key is sent in the `x-goog-api-key` header, never in the URL, so it does
not appear in logs.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

from app.core.config import get_settings


class GeminiError(RuntimeError):
    pass


BUSY = {429, 500, 503}  # worth one retry, then a backup model


def friendly_error(exc: Exception) -> str:
    """One plain sentence for people; the full error stays in the run's technical details."""
    text = str(exc).lower()
    if "503" in text or "high demand" in text or "overloaded" in text:
        return "the Gemini model is busy at Google right now (high demand)"
    if "429" in text or "quota" in text or "rate" in text and "limit" in text:
        return "the AI key has used up its free quota for now"
    if "401" in text or "403" in text or "api key" in text or "permission" in text:
        return "the AI key was not accepted"
    if "timeout" in text or "timed out" in text:
        return "the AI service did not answer in time"
    if "http 400" in text or "empty answer" in text or "no answer" in text:
        return "Gemini could not handle this request"
    return "the AI service could not be reached"


def to_gemini_schema(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert a JSON Schema (Pydantic or hand-written) to the OpenAPI subset Gemini accepts."""
    defs = defs if defs is not None else schema.get("$defs", {})
    if "$ref" in schema:
        return to_gemini_schema(defs[schema["$ref"].split("/")[-1]], defs)
    nullable = False
    if "anyOf" in schema:
        options = [o for o in schema["anyOf"] if o.get("type") != "null"]
        nullable = len(options) < len(schema["anyOf"])
        base = to_gemini_schema(options[0], defs) if options else {"type": "STRING"}
        if nullable:
            base["nullable"] = True
        if schema.get("description"):
            base["description"] = schema["description"]
        return base
    typ = schema.get("type")
    if isinstance(typ, list):
        nullable = "null" in typ
        typ = next((t for t in typ if t != "null"), "string")
    out: dict[str, Any] = {}
    if typ:
        out["type"] = str(typ).upper()
    if "const" in schema:
        out["type"], out["enum"] = "STRING", [str(schema["const"])]
    if "enum" in schema:
        values = [v for v in schema["enum"] if v is not None]
        nullable = nullable or len(values) < len(schema["enum"])
        out["enum"] = [str(v) for v in values]
        out.setdefault("type", "STRING")
    if schema.get("description"):
        out["description"] = schema["description"]
    if typ == "object":
        props = schema.get("properties", {})
        out["properties"] = {k: to_gemini_schema(v, defs) for k, v in props.items()}
        if schema.get("required"):
            out["required"] = list(schema["required"])
    if typ == "array" and "items" in schema:
        out["items"] = to_gemini_schema(schema["items"], defs)
    for key in ("minimum", "maximum"):
        if key in schema:
            out[key] = schema[key]
    if nullable:
        out["nullable"] = True
    return out


def declaration(name: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
    params = to_gemini_schema(schema)
    if not params.get("properties"):  # functions without inputs omit `parameters`
        return {"name": name, "description": description}
    return {"name": name, "description": description, "parameters": params}


class GeminiClient:
    """`generate()` returns the raw JSON response. Tests pass a fake with the same method."""

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float | None = None,
                 transport: httpx.BaseTransport | None = None):
        s = get_settings()
        self.api_key = api_key or s.gemini_api_key
        self.model = model or s.gemini_model
        self.base_url = s.gemini_base_url.rstrip("/")
        self.timeout = timeout or s.llm_timeout_seconds
        self.transport = transport
        # Backup models, tried in order when Google says the main one is busy (HTTP 503/429/500).
        self.fallbacks = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]
        self.used_model = self.model

    def post(self, body: dict[str, Any]) -> dict[str, Any]:
        """generateContent with a short retry on a busy model, then the backup models."""
        last: GeminiError | None = None
        for model in [self.model, *[m for m in self.fallbacks if m != self.model]]:
            for attempt in range(2):
                url = f"{self.base_url}/models/{model}:generateContent"
                try:
                    with httpx.Client(timeout=self.timeout, transport=self.transport) as http:
                        r = http.post(url, json=body, headers={"x-goog-api-key": self.api_key})
                except httpx.HTTPError as exc:
                    raise GeminiError(f"Could not reach the Gemini API: {type(exc).__name__}: {exc}") from exc
                if r.status_code == 200:
                    self.used_model = model
                    return r.json()
                try:
                    detail = r.json().get("error", {}).get("message") or r.text
                except ValueError:
                    detail = r.text
                last = GeminiError(f"Gemini API returned HTTP {r.status_code} for {model}: {str(detail)[:300]}")
                if r.status_code not in BUSY:
                    raise last
                if attempt == 0:
                    time.sleep(1.5)
        assert last is not None
        raise last

    def generate(self, *, system: str, contents: list[dict[str, Any]], declarations: list[dict[str, Any]],
                 mode: str = "ANY", allowed: list[str] | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "tools": [{"functionDeclarations": declarations}],
            "toolConfig": {"functionCallingConfig": {"mode": mode, **({"allowedFunctionNames": allowed} if allowed else {})}},
        }
        return self.post(body)


def list_models(client: GeminiClient) -> list[str]:
    """Model names this key can call generateContent on (for the Settings check)."""
    with httpx.Client(timeout=15, transport=client.transport) as http:
        r = http.get(f"{client.base_url}/models", params={"pageSize": 200}, headers={"x-goog-api-key": client.api_key})
    if r.status_code != 200:
        return []
    return [m["name"].removeprefix("models/") for m in r.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])]


def generate_text(client: Any, system: str, prompt: str) -> str:
    """Plain text answer (no tools), for short summaries."""
    if hasattr(client, "generate_text"):
        return client.generate_text(system=system, prompt=prompt)
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    text = text_of(first_content(client.post(body)))
    if not text:
        raise GeminiError("Gemini returned an empty answer")
    return text


def first_content(response: dict[str, Any]) -> dict[str, Any]:
    candidates = response.get("candidates") or []
    if not candidates:
        reason = (response.get("promptFeedback") or {}).get("blockReason")
        raise GeminiError(f"Gemini returned no answer{f' (blocked: {reason})' if reason else ''}")
    content = candidates[0].get("content") or {}
    if not content.get("parts"):
        raise GeminiError(f"Gemini returned an empty answer (finishReason={candidates[0].get('finishReason')})")
    return content


def function_calls(content: dict[str, Any]) -> list[dict[str, Any]]:
    return [p["functionCall"] for p in content.get("parts", []) if "functionCall" in p]


def text_of(content: dict[str, Any]) -> str:
    return " ".join(p["text"] for p in content.get("parts", []) if isinstance(p.get("text"), str)
                    and not p.get("thought")).strip()


def function_response(call: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    part: dict[str, Any] = {"functionResponse": {"name": call["name"], "response": result}}
    if call.get("id"):
        part["functionResponse"]["id"] = call["id"]
    return part
