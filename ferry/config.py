"""Runtime configuration, loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Settings:
    # Local edge model (the offline brain), served by Ollama's OpenAI-compatible API.
    local_model: str = os.getenv("LOCAL_MODEL", "gemma4:e2b")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

    # Cloud burst model on Cerebras.
    cerebras_base_url: str = os.getenv("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1")
    cerebras_model: str = os.getenv("CEREBRAS_MODEL", "gemma-4-31b")
    # Pool of API keys (one per teammate) so we can fan out wider than 100 RPM/key.
    cerebras_api_keys: list[str] = field(
        default_factory=lambda: _split(os.getenv("CEREBRAS_API_KEYS", ""))
    )
    cerebras_max_tokens: int = int(os.getenv("CEREBRAS_MAX_TOKENS", "1024"))
    # none | low | medium | high — reasoning is off by default on Gemma 4.
    cerebras_reasoning_effort: str = os.getenv("CEREBRAS_REASONING_EFFORT", "none")

    # Web search tool (Exa) — used by burst sub-agents that need live info.
    exa_api_key: str = os.getenv("EXA_API_KEY", "")
    # Code execution sandbox (E2B) — run code / create files mid-answer.
    e2b_api_key: str = os.getenv("E2B_API_KEY", "")
    # Max tool-calling rounds in the agentic burst.
    agent_max_steps: int = int(os.getenv("AGENT_MAX_STEPS", "6"))

    # Router: heuristic | llm | always_cloud | always_local
    router_mode: str = os.getenv("ROUTER_MODE", "heuristic")
    router_length_threshold: int = int(os.getenv("ROUTER_LENGTH_THRESHOLD", "280"))

    # Connectivity watcher.
    watcher_interval: float = float(os.getenv("WATCHER_INTERVAL", "2.5"))
    watcher_probe_url: str = os.getenv(
        "WATCHER_PROBE_URL", "https://api.cerebras.ai/v1/models"
    )

    # Burst drainer.
    drain_concurrency: int = int(os.getenv("DRAIN_CONCURRENCY", "24"))
    drain_poll_interval: float = float(os.getenv("DRAIN_POLL_INTERVAL", "1.0"))
    max_attempts: int = int(os.getenv("MAX_ATTEMPTS", "3"))

    # Storage.
    db_path: str = os.getenv("DB_PATH", "./data/ferry.db")

    # UX.
    placeholder_text: str = os.getenv(
        "PLACEHOLDER_TEXT",
        "⏳ Queued. This one needs the big model — I'll answer the moment a "
        "connection window opens.",
    )
    heartbeat_interval: float = float(os.getenv("HEARTBEAT_INTERVAL", "10.0"))

    @property
    def service_models(self) -> list[str]:
        # Primary auto-routing model, plus escape hatches for demo control.
        return ["ferry", "ferry-local", "ferry-cloud", "ferry-agent"]


settings = Settings()
