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
    local_model: str = os.getenv("LOCAL_MODEL", "LiquidAI/lfm2.5-1.2b-instruct")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    local_max_tokens: int = int(os.getenv("LOCAL_MAX_TOKENS", "64"))
    local_temperature: float = float(os.getenv("LOCAL_TEMPERATURE", "0.2"))
    local_timeout_seconds: float = float(os.getenv("LOCAL_TIMEOUT_SECONDS", "45"))
    local_context_messages: int = int(os.getenv("LOCAL_CONTEXT_MESSAGES", "4"))
    local_context_chars: int = int(os.getenv("LOCAL_CONTEXT_CHARS", "2400"))

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
    e2b_file_roots: list[str] = field(
        default_factory=lambda: _split(
            os.getenv("E2B_FILE_ROOTS", "/home/user,/mnt/data")
        )
    )
    e2b_file_list_depth: int = int(os.getenv("E2B_FILE_LIST_DEPTH", "6"))
    e2b_max_files: int = int(os.getenv("E2B_MAX_FILES", "20"))
    e2b_max_file_bytes: int = int(os.getenv("E2B_MAX_FILE_BYTES", str(10 * 1024 * 1024)))
    # Max tool-calling rounds in the agentic burst.
    agent_max_steps: int = int(os.getenv("AGENT_MAX_STEPS", "6"))
    # Number of parallel sub-agents for the multiverse fan-out model.
    multiverse_agents: int = int(os.getenv("MULTIVERSE_AGENTS", "3"))

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
    generated_files_dir: str = os.getenv("GENERATED_FILES_DIR", "./data/generated")
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")

    # UX.
    placeholder_text: str = os.getenv(
        "PLACEHOLDER_TEXT",
        "⏳ Queued. This one needs the big model — I'll answer the moment a "
        "connection window opens.",
    )
    heartbeat_interval: float = float(os.getenv("HEARTBEAT_INTERVAL", "10.0"))

    # Notifications on queue + return. none | macos | ntfy.
    #   macos → native notification on the machine running Ferry (Mac laptop flow)
    #   ntfy  → HTTP push to a topic, reaches your phone from the headless Jetson hub
    notify_mode: str = os.getenv("NOTIFY_MODE", "none")
    ntfy_server: str = os.getenv("NTFY_SERVER", "https://ntfy.sh")
    ntfy_topic: str = os.getenv("NTFY_TOPIC", "")

    @property
    def service_models(self) -> list[str]:
        # Open WebUI should show one model; Ferry does all routing internally.
        return ["ferry"]


settings = Settings()
