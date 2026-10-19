"""Document retrieval + Gemini generation with retries, model fallback and tool calling."""
import glob
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass

import numpy as np
from opentelemetry import trace

from app import tools

LLM_MODE = os.getenv("LLM_MODE", "real")  # "fake" = offline, no API calls (tests)
CHAT_MODEL = os.getenv("CHAT_MODEL", "gemini-3.8-flash")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "gemini-3.5-flash-lite")
EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")
# Default model thinking pushed eval answers to 30-60s; short grounded lookups don't need it.
# Set to "" to use the model's default.
THINKING_LEVEL = os.getenv("THINKING_LEVEL", "low")
DOCS_DIR = os.getenv("DOCS_DIR", "docs")
# USD per 1M tokens: check current Vertex AI pricing and update
PRICE_IN = float(os.getenv("PRICE_INPUT_PER_M", "0.30"))
PRICE_OUT = float(os.getenv("PRICE_OUTPUT_PER_M", "2.50"))

SYSTEM = (
    "You are SentinelRAG, an SRE assistant. Answer ONLY from the provided context and tool results. "
    "If the answer is not there, say you don't know. Cite sources like [slo-policy.md]."
)

tracer = trace.get_tracer(__name__)
_client = None


def log(**fields):
    # Cloud Run turns JSON on stdout into structured logs (jsonPayload.*)
    print(json.dumps(fields), flush=True)


def client():
    global _client
    if _client is None:
        from google import genai
        # Local: uses GEMINI_API_KEY. Cloud Run: uses Vertex AI via GOOGLE_GENAI_USE_VERTEXAI=true
        _client = genai.Client()
    return _client


@dataclass
class Chunk:
    source: str
    text: str


def load_chunks(docs_dir=DOCS_DIR, max_chars=800):
    chunks = []
    for path in sorted(glob.glob(os.path.join(docs_dir, "*.md"))):
        with open(path, encoding="utf-8") as f:
            paragraphs = [p.strip() for p in f.read().split("\n\n") if p.strip()]
        buf = ""
        for p in paragraphs:
            if buf and len(buf) + len(p) > max_chars:
                chunks.append(Chunk(os.path.basename(path), buf))
                buf = ""
            buf = f"{buf}\n\n{p}" if buf else p
        if buf:
            chunks.append(Chunk(os.path.basename(path), buf))
    return chunks


def _fake_embed(texts):
    vecs = np.zeros((len(texts), 256))
    for i, t in enumerate(texts):
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            vecs[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1
    return vecs


def embed(texts, task_type):
    if LLM_MODE == "fake":
        vecs = _fake_embed(texts)
    else:
        from google.genai import types
        vecs = np.array([
            client().models.embed_content(
                model=EMBED_MODEL, contents=t,
                config=types.EmbedContentConfig(task_type=task_type),
            ).embeddings[0].values
            for t in texts
        ])
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.where(norms == 0, 1, norms)


class Index:
    def __init__(self, chunks):
        self.chunks = chunks
        self.vectors = embed([c.text for c in chunks], "RETRIEVAL_DOCUMENT")

    def search(self, query, k=3):
        q = embed([query], "RETRIEVAL_QUERY")[0]
        scores = self.vectors @ q
        return [(self.chunks[i], float(scores[i])) for i in np.argsort(scores)[::-1][:k]]


def _generate(prompt):
    from google.genai import types
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM, temperature=0.2, tools=tools.ALL_TOOLS,
        thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVEL.upper()) if THINKING_LEVEL else None,
    )
    last_error = None
    for model in (CHAT_MODEL, FALLBACK_MODEL):
        for attempt in range(3):
            try:
                t0 = time.perf_counter()
                resp = client().models.generate_content(model=model, contents=prompt, config=config)
                u = resp.usage_metadata
                log(severity="INFO", message="llm_call", model=model, attempt=attempt + 1,
                    generate_ms=round((time.perf_counter() - t0) * 1000),
                    thoughts_tokens=getattr(u, "thoughts_token_count", None),
                    afc_turns=len(getattr(resp, "automatic_function_calling_history", None) or []))
                usage = ((u.prompt_token_count or 0), (u.candidates_token_count or 0)) if u else (0, 0)
                return resp.text or "", model, usage
            except Exception as e:  # retry, then fall back to the cheaper model
                last_error = e
                log(severity="WARNING", message="llm_retry", model=model,
                    attempt=attempt + 1, error=str(e)[:200])
                time.sleep(2 ** attempt)
    raise last_error


def answer(index, question):
    start = time.perf_counter()
    with tracer.start_as_current_span("retrieve"):
        hits = index.search(question)
    retrieve_ms = round((time.perf_counter() - start) * 1000)
    context = "\n\n".join(f"[{c.source}]\n{c.text}" for c, _ in hits)
    prompt = f"Context:\n{context}\n\nQuestion: {question}"

    with tracer.start_as_current_span("llm.generate") as span:
        if LLM_MODE == "fake":
            text, model, usage = f"(fake) Based on [{hits[0][0].source}]: {hits[0][0].text[:200]}", "fake", (0, 0)
        else:
            text, model, usage = _generate(prompt)
        span.set_attribute("llm.model", model)

    prompt_tokens, output_tokens = usage
    cost = prompt_tokens / 1e6 * PRICE_IN + output_tokens / 1e6 * PRICE_OUT
    latency_ms = round((time.perf_counter() - start) * 1000)
    log(severity="INFO", message="llm_request", model=model,
        fallback_used=model == FALLBACK_MODEL, prompt_tokens=prompt_tokens,
        output_tokens=output_tokens, total_tokens=prompt_tokens + output_tokens,
        est_cost_usd=round(cost, 8), latency_ms=latency_ms, retrieve_ms=retrieve_ms)

    return {
        "answer": text,
        "sources": sorted({c.source for c, _ in hits}),
        "model": model,
        "usage": {"prompt_tokens": prompt_tokens, "output_tokens": output_tokens,
                  "est_cost_usd": round(cost, 8)},
        "latency_ms": latency_ms,
    }
