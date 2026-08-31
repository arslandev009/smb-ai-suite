"""Sync HTTP client for the two native llama.cpp servers (embedding + generation).
Sync, not async — Streamlit's execution model is sync top-to-bottom, so there's
no benefit to asyncio here (unlike a real concurrent API server)."""
import json
import re

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.config import settings

_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)

# Standard llama.cpp JSON grammar (llama.cpp's own grammars/json.gbnf) — forces
# every token the model produces to be valid JSON, structurally. This is the
# exact same fix job-market-pipeline already relies on for its extraction
# calls ("GBNF grammar-constrained decoding ... makes malformed JSON
# structurally impossible, eliminating an entire category of retry logic").
# Root cause of the B2 scoring failures: on longer prompts, the model was
# spending its whole max_tokens budget on unconstrained reasoning/commentary
# before ever reaching JSON — several calls hit exactly max_tokens (700) and
# got cut off mid-thought, never mind mid-JSON. Grammar constraint prevents
# that outright rather than us continuing to patch the parser after the fact.
JSON_GRAMMAR = r"""
root   ::= object
value  ::= object | array | string | number | ("true" | "false" | "null") ws

object ::=
  "{" ws (
            string ":" ws value
    ("," ws string ":" ws value)*
  )? "}" ws

array  ::=
  "[" ws (
            value
    ("," ws value)*
  )? "]" ws

string ::=
  "\"" (
    [^"\\\x7F\x00-\x1F] |
    "\\" (["\\bfnrt] | "u" [0-9a-fA-F]{4})
  )* "\"" ws

number ::= ("-"? ([0-9] | [1-9] [0-9]*)) ("." [0-9]+)? ([eE] [-+]? [0-9]+)? ws

ws ::= | " " | "\n" [ \t]{0,20}
"""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def embed(text: str) -> list[float]:
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(f"{settings.llamacpp_embedding_url}/embedding", json={"content": text})
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            data = data[0]
        embedding = data["embedding"]
        if isinstance(embedding[0], list):
            embedding = embedding[0]
        return embedding


def embed_batch(texts: list[str]) -> list[list[float]]:
    return [embed(t) for t in texts]


def _chat_completion(payload: dict) -> str:
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(f"{settings.llamacpp_generation_url}/v1/chat/completions", json=payload)
        resp.raise_for_status()
        message = resp.json()["choices"][0]["message"]
        content = (message.get("content") or "").strip()
        if not content:
            # Reasoning models (Qwen3.5 included) can put everything into a
            # separate 'reasoning_content' field and never produce a distinct
            # final-answer segment, leaving 'content' genuinely empty. Rather
            # than silently return nothing, fall back to whatever reasoning
            # text exists — imperfect, but a real answer beats a blank one.
            content = (message.get("reasoning_content") or "").strip()
        return content


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=8))
def generate(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 700,
    temperature: float = 0.2,
    grammar: str | None = None,
) -> str:
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        # Qwen3-family models default to "thinking" mode, which was the real
        # root cause behind several bugs here: content coming back truncated
        # (thinking ate the whole token budget) or entirely empty (everything
        # landed in reasoning_content, content never got written). This is
        # the standard llama.cpp/vLLM mechanism to turn thinking off outright
        # at the template level, rather than continuing to patch around its
        # symptoms after the fact.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if grammar:
        payload["grammar"] = grammar

    try:
        return _chat_completion(payload)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            # Either 'grammar' or 'chat_template_kwargs' isn't accepted by
            # this llama-server build — strip whichever extra fields we added
            # and retry with a plain request rather than hard-failing every
            # call in the suite over an unsupported parameter.
            payload.pop("grammar", None)
            payload.pop("chat_template_kwargs", None)
            return _chat_completion(payload)
        raise


def generate_json(system_prompt: str, user_prompt: str) -> dict:
    # NOTE: grammar-constrained decoding (JSON_GRAMMAR above) was tried here
    # and reverted — with this model/server combo it produced completely
    # empty output on every call instead of the (partially working) plain
    # output we had before. Most likely cause: Qwen3.5 wants to emit
    # reasoning tokens before its answer, and a grammar that only permits
    # "{" as the very first token gives the sampler nowhere valid to go.
    # Back to unconstrained generation with a larger budget (so a reasoning
    # preamble doesn't eat the whole token limit before reaching the JSON)
    # plus robust extraction/error-surfacing below. The extra instruction
    # appended here is centralized so every generate_json() caller across
    # every project gets it without editing each individual prompt.
    system_prompt = (
        system_prompt
        + "\n\nRespond with the JSON object only. Do not think step by step, "
        "do not explain your reasoning, do not include any text before or "
        "after the JSON object."
    )
    raw_original = generate(system_prompt, user_prompt, temperature=0.0, max_tokens=1200).strip()
    raw = raw_original
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
    raw = raw.strip()

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        snippet = raw_original[:400].replace("\n", " \u23ce ")
        raise ValueError(f"model output wasn't valid JSON, raw output: {snippet!r}") from e


def check_health() -> dict:
    """Used by the hub sidebar's status pills — same idea as job-market-pipeline's
    check_llama_health(), extended to both servers."""
    status = {"embedding": False, "generation": False}
    try:
        with httpx.Client(timeout=2.0) as client:
            r = client.post(f"{settings.llamacpp_embedding_url}/embedding", json={"content": "ping"})
            status["embedding"] = r.status_code == 200
    except Exception:
        pass
    try:
        with httpx.Client(timeout=2.0) as client:
            r = client.post(
                f"{settings.llamacpp_generation_url}/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
            )
            status["generation"] = r.status_code == 200
    except Exception:
        pass
    return status