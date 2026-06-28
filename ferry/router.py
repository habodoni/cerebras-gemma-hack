"""The router: decide local-now vs queue-for-cloud for each message.

No user toggle — Ferry decides. Modes:
  heuristic    — length/keyword rule (deterministic, demo-safe; the default)
  llm          — ask the local edge model to classify, fall back to heuristic
  always_cloud — force everything into the backlog (handy for demos)
  always_local — never queue
"""
from __future__ import annotations

from .config import settings
from .db import _last_user_text

# Signals that a prompt likely wants the big model.
HARD_KEYWORDS = (
    "analyze", "analysis", "research", "compare", "comprehensive", "in depth",
    "in-depth", "detailed", "write a", "draft", "essay", "report", "plan",
    "design", "architect", "debug", "refactor", "optimi", "prove", "derive",
    "explain why", "step by step", "step-by-step", "summarize this", "translate",
    "code", "algorithm", "strategy", "evaluate", "pros and cons", "search",
    "web", "current", "latest", "today", "recent", "news", "calculate",
    "compute", "run code", "create a file", "csv", "generate", "story", "poem",
    "repeat", "many times", "exactly", "pptx", "powerpoint", "presentation",
    "slide deck", "slides", "deck", "docx", "xlsx", "spreadsheet", "pdf",
    "gif", "animation", "animated", "visualization", "png", "image", "svg",
    "mp4", "video", "cerebras",
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


def heuristic_route(messages: list[dict]) -> tuple[str, str]:
    text = _last_user_text(messages)
    lowered = text.lower()
    if len(text) >= settings.router_length_threshold:
        return "cloud", f"heuristic: long prompt ({len(text)} chars)"
    for kw in HARD_KEYWORDS:
        if kw in lowered:
            return "cloud", f"heuristic: keyword '{kw}'"
    return "local", "heuristic: short/simple"


async def decide(clients, messages: list[dict]) -> tuple[str, str]:
    """Return (route, reason) where route is 'local' or 'cloud'."""
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
