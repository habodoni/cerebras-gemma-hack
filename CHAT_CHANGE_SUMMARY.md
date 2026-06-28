# Ferry Integration Change Summary

This document summarizes the changes made during this chat, why they were made,
how they were implemented, and how they were verified.

## Goal

The project should expose one Open WebUI model named `ferry`. Users should not
choose local/cloud, online/offline, single-agent/multi-agent, or tool/no-tool
modes. Ferry should decide automatically.

Expected behavior:

- Simple prompts answer locally through Ollama using
  `LiquidAI/lfm2.5-1.2b-instruct`.
- Hard, current, artifact, or explicit Cerebras prompts burst to Cerebras using
  `gemma-4-31b`.
- Broad research/planning/comparison prompts automatically use multi-agent
  fan-out.
- Focused cloud tasks automatically use a single Gemma 4 agent with tools.
- Gemma 4 always has access to tools on the cloud path and decides when to use
  them, except artifact requests force `run_code` so files are actually created.
- Offline demos keep localhost/Open WebUI available, but cloud work queues until
  real internet connectivity returns.

## Major Changes

### Local model switched to Liquid

Why:

The local Ferry model should be the Liquid model, not Gemma. Gemma is the cloud
burst model through Cerebras.

How:

- Updated default local model config to `LiquidAI/lfm2.5-1.2b-instruct`.
- Updated `.env` examples and docs to describe Liquid as local and Gemma 4 as
  cloud.
- Verified `/api/status` reports:
  - `local_model`: `LiquidAI/lfm2.5-1.2b-instruct`
  - `cloud_model`: `gemma-4-31b`

### Local hanging reduced

Why:

Open WebUI sometimes kept loading forever when the local model received prompts
that were too long or too open-ended for the small local model.

How:

- Added local timeout settings.
- Added local message/context compaction.
- Added local max-token and temperature controls.
- Routed long-output or artifact-like prompts to cloud instead of local.

### Automatic routing improved

Why:

The user should see only one Ferry option. Ferry should automatically decide
whether a prompt is local, cloud single-agent, or cloud multi-agent.

How:

- Expanded router keyword coverage for:
  - current/search/web prompts
  - computation prompts
  - file/artifact prompts
  - PPTX/DOCX/XLSX/PDF/CSV/GIF/PNG/SVG/MP4 prompts
  - explicit `cerebras` prompts
- Added cloud mode routing:
  - `single_agent` for focused tool/artifact/current tasks
  - `multi_agent` for broad research, comparison, planning, design, and strategy

### Offline demo behavior fixed

Why:

When Wi-Fi was disabled, forced-online demo mode still tried to run cloud agents
and produced DNS errors like `[Errno -2] Name or service not known`.

How:

- Split dashboard/demo override state from real cloud reachability.
- Added `can_burst` and `can_burst_now` checks.
- Inline cloud work now only runs when the machine can actually reach the cloud.
- If the demo switch says offline or real reachability fails, cloud work queues
  instead of erroring.

### E2B file export fixed

Why:

The cloud agent could run code, but files created inside E2B were not reliably
available to the user as downloadable artifacts.

How:

- Added generated file storage under `data/generated`.
- Scanned E2B file roots including `/home/user` and `/mnt/data`.
- Exported newly created or modified files from the sandbox.
- Added download URLs served by Ferry at:
  `/api/files/{run_id}/{file_path}`
- Added traversal guards and file-size/file-count limits.

### PPTX creation fixed

Why:

The model was saying it could not create PowerPoint files even though E2B could
generate them with Python packages.

How:

- Artifact prompts now force the first tool call to `run_code`.
- The cloud agent system prompt explicitly says it can create PPTX/DOCX/XLSX/CSV
  and similar artifacts.
- PPTX requests route to cloud single-agent.
- Verified generated `.pptx` files are valid Microsoft OOXML files.

### GIF/image/video artifact creation added

Why:

Prompts like "make a GIF of a neural network" were being answered as text or
search-only results instead of creating a real GIF.

How:

- Added GIF/PNG/SVG/MP4/image/video terms to routing and artifact detection.
- Updated the agent prompt to use Python libraries such as matplotlib, Pillow,
  imageio, or moviepy for visual artifacts.
- Artifact prompts force `run_code`.
- Mixed prompts like "search the web and make a GIF" force `web_search` first,
  then `run_code`, so the agent cannot stop after search-only output.
- Verified live generation of `neural_network_smoke.gif`.

### Explicit Cerebras routing added

Why:

If a user asks for Cerebras, Ferry should use Cerebras/Gemma 4 instead of local
Liquid.

How:

- Added `cerebras` as a cloud routing keyword.
- Added it as a single-agent cloud-mode keyword.
- Verified live prompt "Use Cerebras..." returns through the `Gemma 4 agent with
  tools` path.

### Single-agent parallel tool execution added

Why:

A single Gemma 4 agent should be able to call multiple independent tools in
parallel when the model emits multiple tool calls in the same turn.

How:

- Changed the single-agent tool loop to start each same-turn tool call with
  `asyncio.create_task`.
- Awaited all same-turn calls with `asyncio.gather`.
- Appended tool results back to the conversation in original tool-call order to
  keep the Chat Completions transcript valid.
- Kept dependent flows sequential across model rounds. For example, "search then
  make a GIF" remains search first, code second.

### Multi-agent fan-out retained

Why:

Broad tasks benefit from multiple specialists and synthesis.

How:

- Multi-agent mode plans specialist sub-agents.
- Sub-agents run concurrently.
- Each sub-agent can use tools.
- Final synthesis combines their findings into one user-facing answer.

## Testing Added

New tests cover:

- Artifact detection for PPTX, CSV, GIF, PNG, and downloadable files.
- Search-plus-GIF behavior requiring both web search and code execution.
- E2B artifact path handling, created-file detection, file export, and traversal
  rejection.
- Local payload caps and context trimming.
- Routing for:
  - local simple prompts
  - long-output prompts
  - search/current prompts
  - PPTX prompts
  - GIF prompts
  - explicit Cerebras prompts
  - multi-agent research/comparison prompts
- Offline watcher behavior for forced-open, forced-closed, and auto modes.
- Single-agent same-turn parallel tool execution.

## Verification Performed

Commands run successfully:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q ferry tests
git diff --check
docker compose up --build -d ferry
```

Latest test result:

- `28` unit tests passed.
- Python compile check passed.
- Git whitespace check passed.
- Ferry container rebuilt and restarted successfully.

Live checks performed:

- `/api/status` reports local Liquid and cloud Gemma 4.
- A prompt asking to search and create a neural-network GIF returned:
  - `Gemma 4 agent with tools`
  - `web_search`
  - `run_code`
  - a Ferry download URL
- Downloaded GIF validated as:
  - `GIF image data, version 89a, 600 x 400`
- A prompt explicitly asking to use Cerebras returned through the Gemma 4 cloud
  agent path.

## Demo Notes

Open WebUI can be used locally at:

```text
http://localhost:3000
```

Ferry runs at:

```text
http://localhost:8080
```

Recommended demo prompts:

```text
What is 21 times 2?
```

Expected: local Liquid response.

```text
Use Cerebras to answer in one sentence: what is 21 times 2?
```

Expected: Gemma 4 cloud agent response.

```text
make an extensive gif of a neural network search the web
```

Expected: Gemma 4 cloud agent searches, runs E2B code, and returns a GIF download
link.

Offline behavior:

- Turning off laptop Wi-Fi does not break localhost, Docker, Ferry, or Open
  WebUI.
- Local Liquid prompts should still work.
- Cloud/tool prompts requiring Cerebras, Exa, or E2B should queue until internet
  returns.
