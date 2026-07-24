#!/usr/bin/env python3
"""
Tiny provider-agnostic LLM client (OpenAI-compatible chat endpoint).

Defaults to Groq's free API serving open-source models (Llama 3.3 70B). Because
the request/response shape is the OpenAI standard, you can retarget any
compatible provider by setting env vars — no code change:

  LLM_API_KEY   (or GROQ_API_KEY)   the key
  LLM_BASE_URL  default https://api.groq.com/openai/v1
  LLM_MODEL     default llama-3.3-70b-versatile

All AI features (item classification, report narrative, resilient price
parsing) go through here so the whole system has ONE swappable model backend
and each caller can cheaply check available() and fall back to rules if no key
is set. Uses only stdlib (urllib) — no extra dependency.
"""
import json
import os
import urllib.request
import urllib.error

# Use `or` (not a default arg): the workflow may pass these as EMPTY strings when
# the optional repo variables are unset, and "" must still fall back to the default.
BASE_URL = (os.environ.get("LLM_BASE_URL") or "https://api.groq.com/openai/v1").rstrip("/")
MODEL = os.environ.get("LLM_MODEL") or "llama-3.3-70b-versatile"


def api_key():
    return os.environ.get("LLM_API_KEY") or os.environ.get("GROQ_API_KEY")


def available():
    return bool(api_key())


def chat(system, user, json_mode=True, temperature=0.0, timeout=60):
    """Single-shot chat completion. Returns the assistant message string.
    Raises on missing key or transport/API error — callers should catch and
    fall back to their rule-based path."""
    key = api_key()
    if not key:
        raise RuntimeError("No LLM_API_KEY / GROQ_API_KEY set")
    body = {
        "model": MODEL,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"LLM HTTP {e.code}: {e.read()[:200]!r}")
    return resp["choices"][0]["message"]["content"]


def chat_json(system, user, **kw):
    """chat() but parse the reply as JSON (best-effort: strips code fences)."""
    txt = chat(system, user, json_mode=True, **kw).strip()
    if txt.startswith("```"):
        txt = txt.split("```", 2)[1].lstrip("json").strip() if "```" in txt else txt
    return json.loads(txt)
