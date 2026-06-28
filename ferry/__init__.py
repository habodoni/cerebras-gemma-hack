"""Ferry — local-first AI for intermittent connectivity.

Open WebUI talks to Ferry as if it were a normal OpenAI-compatible model.
Ferry answers easy prompts locally (LiquidAI/lfm2.5-1.2b-instruct via Ollama) and queues hard
ones in a SQLite backlog, draining them to gemma-4-31b on Cerebras the moment
a connection window opens.
"""

__version__ = "0.1.0"
