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
    "code", "algorithm", "strategy", "evaluate", "pros and cons",
)

ROUTER_SYSTEM = (
    "You are a routing classifier. Decide whether a user request can be answered "
    "well by a tiny on-device model RIGHT NOW (LOCAL), or whether it needs a large "
    "cloud model (CLOUD) because it is complex, long, multi-step, or high-stakes. "
    "Reply with exactly one word: LOCAL or CLOUD."
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
