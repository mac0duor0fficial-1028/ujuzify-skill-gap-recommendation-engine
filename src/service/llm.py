"""Optional OpenAI-compatible LLM passthrough for the coach (disabled unless configured).

Configure with LLM_BASE_URL (+ LLM_API_KEY, LLM_MODEL) or provider keys (GROQ_API_KEY,
OPENROUTER_API_KEY, TOGETHER_API_KEY, HF_TOKEN) / OLLAMA_BASE_URL. The deterministic coach is the default
and the fallback when the upstream model errors.
"""
from __future__ import annotations

import json
import os

from src.service.errors import EngineError

PRESETS = [
    {"id": "meta-llama/Llama-3.3-70B-Instruct", "label": "Llama 3.3 70B", "family": "Meta Llama",
     "aliases": {"groq": "llama-3.3-70b-versatile", "openrouter": "meta-llama/llama-3.3-70b-instruct", "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "ollama": "llama3.3", "huggingface": "meta-llama/Llama-3.3-70B-Instruct"}},
    {"id": "meta-llama/Llama-3.1-8B-Instruct", "label": "Llama 3.1 8B", "family": "Meta Llama",
     "aliases": {"groq": "llama-3.1-8b-instant", "openrouter": "meta-llama/llama-3.1-8b-instruct", "together": "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo", "ollama": "llama3.1:8b", "huggingface": "meta-llama/Llama-3.1-8B-Instruct"}},
    {"id": "Qwen/Qwen2.5-72B-Instruct", "label": "Qwen 2.5 72B", "family": "Alibaba Qwen",
     "aliases": {"openrouter": "qwen/qwen-2.5-72b-instruct", "together": "Qwen/Qwen2.5-72B-Instruct-Turbo", "ollama": "qwen2.5:32b", "huggingface": "Qwen/Qwen2.5-72B-Instruct"}},
    {"id": "mistralai/Mistral-Small-24B-Instruct-2501", "label": "Mistral Small 24B", "family": "Mistral AI",
     "aliases": {"openrouter": "mistralai/mistral-small-24b-instruct-2501", "together": "mistralai/Mistral-Small-24B-Instruct-2501", "ollama": "mistral-small", "huggingface": "mistralai/Mistral-Small-24B-Instruct-2501"}},
]


def _env(n: str) -> str:
    return os.getenv(n, "").strip()


def configured_providers() -> dict[str, bool]:
    return {"huggingface": bool(_env("HF_TOKEN") or _env("HUGGINGFACE_TOKEN")), "groq": bool(_env("GROQ_API_KEY")),
            "openrouter": bool(_env("OPENROUTER_API_KEY")), "together": bool(_env("TOGETHER_API_KEY")),
            "ollama": bool(_env("OLLAMA_BASE_URL")), "custom": bool(_env("LLM_BASE_URL") or _env("OPENSOURCE_BASE_URL"))}


def resolve(provider: str, model_id: str, custom_base: str = "") -> dict:
    provider = (provider or "auto").lower()
    cfg = configured_providers()
    if provider == "auto":
        provider = next((p for p in ("custom", "groq", "huggingface", "openrouter", "together", "ollama") if cfg[p]), "")
        if not provider:
            raise EngineError("No open-source provider is configured on the server (set LLM_BASE_URL, GROQ_API_KEY, "
                              "OPENROUTER_API_KEY, TOGETHER_API_KEY, HF_TOKEN or OLLAMA_BASE_URL).", 400)
    bases = {"huggingface": ("https://router.huggingface.co/v1", _env("HF_TOKEN") or _env("HUGGINGFACE_TOKEN")),
             "groq": ("https://api.groq.com/openai/v1", _env("GROQ_API_KEY")),
             "openrouter": ("https://openrouter.ai/api/v1", _env("OPENROUTER_API_KEY")),
             "together": ("https://api.together.xyz/v1", _env("TOGETHER_API_KEY"))}
    if provider in bases:
        base, key = bases[provider]
        if not key:
            raise EngineError(f"Provider '{provider}' is not configured on the server.", 400)
    elif provider == "ollama":
        base = (custom_base or _env("OLLAMA_BASE_URL") or "http://localhost:11434/v1").rstrip("/")
        base, key = (base if base.endswith("/v1") else base + "/v1"), ""
    else:
        base, key = (custom_base or _env("LLM_BASE_URL") or _env("OPENSOURCE_BASE_URL")).rstrip("/"), _env("LLM_API_KEY") or _env("OPENSOURCE_API_KEY")
        if not base:
            raise EngineError("Custom LLM endpoint is not configured (LLM_BASE_URL).", 400)
        provider = "custom"
    preset = next((p for p in PRESETS if p["id"] == model_id), None)
    model = (preset["aliases"].get(provider) if preset else None) or model_id or _env("LLM_MODEL")
    if not model:
        raise EngineError("No model selected.", 400)
    return {"provider": provider, "base": base, "key": key, "model": model}


def chat(message: str, history: list, context: dict, target: dict, timeout: float = 35.0) -> str:
    import httpx

    system = ("You are Ujuzify Coach, an accurate, concise career mentor for Kenyan technology graduates. "
              "Ground career answers in the candidate context below; never invent achievements, salaries or guarantees.\n"
              f"Candidate context JSON:\n{json.dumps(context or {})[:6000]}")
    msgs = [{"role": "system", "content": system}]
    for h in (history or [])[-12:]:
        if isinstance(h, dict) and isinstance(h.get("text"), str) and h["text"].strip():
            msgs.append({"role": "assistant" if h.get("role") == "assistant" else "user", "content": h["text"]})
    msgs.append({"role": "user", "content": message})
    headers = {"Content-Type": "application/json"}
    if target["key"]:
        headers["Authorization"] = f"Bearer {target['key']}"
    try:
        r = httpx.post(f"{target['base']}/chat/completions", headers=headers, timeout=timeout,
                       json={"model": target["model"], "messages": msgs, "temperature": 0.4, "max_tokens": 1200})
    except httpx.HTTPError as exc:
        raise EngineError(f"{target['provider']} endpoint unreachable: {exc.__class__.__name__}", 502) from exc
    if r.status_code >= 400:
        raise EngineError(f"{target['provider']} ({target['model']}) returned HTTP {r.status_code}", 502)
    try:
        return (r.json()["choices"][0]["message"]["content"] or "").strip() or "The model returned an empty response."
    except (KeyError, IndexError, ValueError) as exc:
        raise EngineError("Unexpected response from the model endpoint.", 502) from exc
