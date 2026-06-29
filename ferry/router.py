"""The router: decide local-now vs queue-for-cloud for each message.

No user toggle — Ferry decides. Modes:
  heuristic    — length/keyword rule (deterministic, demo-safe; the default)
  llm          — ask the local edge model to classify, fall back to heuristic
  always_cloud — force everything into the backlog (handy for demos)
  always_local — never queue
"""
from __future__ import annotations

import re

from .config import settings
from .db import _last_user_text

# Bulk-repetition requests ("say hi 200 times") want long output — cloud, even
# though the prompt is short. Two+ digits avoids "3 times a day"; the lookahead
# avoids multiplication ("17 times 4"), which a local model should just answer.
_BULK_REPEAT = re.compile(r"\b\d{2,}\s*times\b(?!\s*\d)")

# Signals a prompt likely needs the big cloud model — either it needs tools
# (search / files / compute) or it's long-form, multi-step reasoning. Pure-language
# tasks (translate, a short story, a poem) are deliberately NOT here so the local
# edge model keeps them — that is the local-first promise. Bare "code"/"generate"
# were also dropped: too substring-matchy ("decode", "generation") and too broad.
HARD_KEYWORDS = (
    "analyze", "analysis", "research", "compare", "comprehensive", "in depth",
    "in-depth", "detailed", "write a", "draft", "essay", "report", "plan",
    "design", "architect", "debug", "refactor", "optimi", "prove", "derive",
    "explain why", "step by step", "step-by-step", "summarize this",
    "algorithm", "strategy", "evaluate", "pros and cons", "search",
    "web", "current", "latest", "today", "recent", "news", "calculate",
    "compute", "run code", "create a file", "csv", "pptx", "powerpoint",
    "presentation", "slide deck", "slides", "deck", "docx", "xlsx",
    "spreadsheet", "pdf", "gif", "animation", "animated", "visualization",
    "png", "image", "svg", "mp4", "video", "cerebras",
)

# Explicit, plain-language route overrides — let a user steer Ferry directly.
FORCE_CLOUD_PHRASES = (
    "use cerebras", "use the cloud", "use cloud", "via cerebras", "on cerebras",
    "@cloud", "@cerebras", "force cloud", "burst this", "use gemma 4",
    "use the big model",
)
FORCE_LOCAL_PHRASES = (
    "use local", "stay local", "use the local model", "@local", "on device",
    "on-device", "keep it local", "answer locally",
)

MULTI_AGENT_KEYWORDS = (
    "research", "compare", "comparing", "comparison", "analysis",
    "comprehensive", "in depth",
    "in-depth", "detailed analysis", "plan", "strategy", "evaluate",
    "pros and cons", "tradeoff", "trade-off", "recommend", "recommendation",
    "architect", "design", "multi-step", "end-to-end", "break down",
)

SINGLE_AGENT_KEYWORDS = (
    "search", "web", "current", "latest", "today", "recent", "news",
    "calculate", "compute", "run code", "python", "create a file", "csv",
    "pptx", "powerpoint", "presentation", "slide deck", "slides", "deck",
    "docx", "xlsx", "spreadsheet", "pdf",
    "gif", "animation", "animated", "visualization", "png", "image", "svg",
    "mp4", "video", "cerebras",
)

ROUTER_SYSTEM = (
    "You are a routing classifier. Decide whether a user request can be answered "
    "well by a tiny on-device model RIGHT NOW (LOCAL), or whether it needs a large "
    "cloud model (CLOUD) because it is complex, long, multi-step, or high-stakes. "
    "Reply with exactly one word: LOCAL or CLOUD."
)

AGENT_ROUTER_SYSTEM = (
    "You are Ferry's cloud execution router. The request already needs the cloud. "
    "Choose SINGLE_AGENT for focused tasks, current facts, tool use, or computation. "
    "Choose MULTI_AGENT for broad research, planning, comparison, design, strategy, "
    "or tasks that benefit from parallel specialists and synthesis. Reply with "
    "exactly one word: SINGLE_AGENT or MULTI_AGENT."
)


def explicit_override(messages: list[dict]) -> tuple[str, str] | None:
    """Honor a user who states the route in plain language ('use cerebras')."""
    text = _last_user_text(messages).lower()
    if any(p in text for p in FORCE_CLOUD_PHRASES):
        return "cloud", "user override: cloud"
    if any(p in text for p in FORCE_LOCAL_PHRASES):
        return "local", "user override: local"
    return None


def heuristic_route(messages: list[dict]) -> tuple[str, str]:
    text = _last_user_text(messages)
    lowered = text.lower()
    if len(text) >= settings.router_length_threshold:
        return "cloud", f"heuristic: long prompt ({len(text)} chars)"
    if _BULK_REPEAT.search(lowered):
        return "cloud", "heuristic: bulk repetition"
    for kw in HARD_KEYWORDS:
        if kw in lowered:
            return "cloud", f"heuristic: keyword '{kw}'"
    return "local", "heuristic: short/simple"


async def decide(clients, messages: list[dict]) -> tuple[str, str]:
    """Return (route, reason) where route is 'local' or 'cloud'."""
    # A user who explicitly asks for a route wins over everything else.
    override = explicit_override(messages)
    if override is not None:
        return override
    mode = settings.router_mode
    if mode == "always_local":
        return "local", "forced local"
    if mode == "always_cloud":
        return "cloud", "forced cloud"
    if mode == "llm":
        try:
            probe = [
                {"role": "system", "content": ROUTER_SYSTEM},
                {"role": "user", "content": _last_user_text(messages)[:2000]},
            ]
            answer = (await clients.ollama_complete(probe, settings.local_model)).strip().upper()
            if "CLOUD" in answer:
                return "cloud", "llm router: CLOUD"
            if "LOCAL" in answer:
                return "local", "llm router: LOCAL"
        except Exception:
            pass  # fall through to heuristic
    return heuristic_route(messages)


async def decide_cloud_mode(clients, messages: list[dict]) -> tuple[str, str]:
    """Return ('single_agent'|'multi_agent', reason) for cloud-worthy prompts."""
    text = _last_user_text(messages)
    lowered = text.lower()
    multi_hits = [kw for kw in MULTI_AGENT_KEYWORDS if kw in lowered]
    single_hits = [kw for kw in SINGLE_AGENT_KEYWORDS if kw in lowered]

    if len(text) >= max(settings.router_length_threshold * 2, 560):
        return "multi_agent", f"multi-agent: long prompt ({len(text)} chars)"
    if len(multi_hits) >= 2:
        return "multi_agent", "multi-agent: " + ", ".join(multi_hits[:3])
    if single_hits and not multi_hits:
        return "single_agent", "single-agent: " + ", ".join(single_hits[:3])

    try:
        probe = [
            {"role": "system", "content": AGENT_ROUTER_SYSTEM},
            {"role": "user", "content": text[:3000]},
        ]
        answer = (await clients.ollama_complete(probe, settings.local_model)).strip().upper()
        if "MULTI_AGENT" in answer:
            return "multi_agent", "llm cloud router: MULTI_AGENT"
        if "SINGLE_AGENT" in answer:
            return "single_agent", "llm cloud router: SINGLE_AGENT"
    except Exception:
        pass

    if multi_hits:
        return "multi_agent", "multi-agent: " + ", ".join(multi_hits[:3])
    return "single_agent", "single-agent: focused cloud task"
