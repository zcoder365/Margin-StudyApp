"""
ai provider wrapper.

everything that talks to a language model goes through get_ai().generate_json(),
so services and routes can be tested with a fake provider (set_ai) and no paid
requests. the provider never logs prompt contents (they contain student
documents).
"""

import os
import re
import json
import time
import logging

log = logging.getLogger("margin.ai")


class AIError(Exception):
    """the model call failed, timed out, was rate limited, or returned unusable output."""

    def __init__(self, message, kind="error"):
        super().__init__(message)
        self.kind = kind  # error | timeout | rate_limited | malformed | unavailable


def parse_json_text(text):
    """parse a model response that should be JSON, tolerating ``` fences and leading prose."""
    if text is None:
        raise AIError("empty response from model", kind="malformed")
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # fall back to the outermost JSON object/array in the text
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = s.find(open_c), s.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(s[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise AIError("model response was not valid JSON", kind="malformed")


class GeminiProvider:
    def __init__(self, api_key=None, model=None, timeout_seconds=90):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self.timeout_seconds = timeout_seconds
        self._client = None

    @property
    def available(self):
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            from google import genai
            from google.genai import types
            self._client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(timeout=self.timeout_seconds * 1000),
            )
        return self._client

    def generate_json(self, prompt, retries=1):
        if not self.available:
            raise AIError("AI provider is not configured (GEMINI_API_KEY missing)", kind="unavailable")
        from google.genai import types

        last_err = None
        for attempt in range(retries + 1):
            try:
                response = self._get_client().models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                return parse_json_text(response.text)
            except AIError as e:
                last_err = e
            except Exception as e:  # sdk raises several error types; classify by message
                msg = str(e).lower()
                if "429" in msg or "resource_exhausted" in msg or "rate" in msg:
                    last_err = AIError("the AI provider is rate limiting requests — try again in a minute", kind="rate_limited")
                elif "timeout" in msg or "deadline" in msg:
                    last_err = AIError("the AI provider timed out", kind="timeout")
                else:
                    last_err = AIError("the AI provider returned an error", kind="error")
                log.warning("gemini call failed (attempt %d): %s", attempt + 1, type(e).__name__)
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        raise last_err


_provider = None


def get_ai():
    global _provider
    if _provider is None:
        _provider = GeminiProvider()
    return _provider


def set_ai(provider):
    """used by tests to inject a fake provider."""
    global _provider
    _provider = provider
