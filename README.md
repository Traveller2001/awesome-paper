# Awesome Paper 3.0

**English | [中文](docs/README_zh.md)**

Agentic arXiv paper tracker — configure scraping, LLM classification, and multi-channel notifications through natural conversation.

## Features

- **Conversational Agent**: `python run.py` — configure and operate everything via natural language
- **Smart Onboarding**: auto-detects config status on startup, guides you to fill in the gaps
- **Async Parallel Classification**: `asyncio` + `AsyncOpenAI` with configurable concurrency
- **Plugin Notifiers**: Feishu supported, easy to extend
- **Multi LLM Backend**: any OpenAI-compatible API (DeepSeek, OpenRouter, OpenAI, etc.)
- **Profile System**: all configs persisted in `profiles/`, changes take effect immediately
- **Resumable Pipeline**: stage state persisted, auto-skips completed stages on restart
- **Pipeline Supervisor**: captures noisy stdout, delivers concise summaries to keep the agent context lean

## Project Structure

```
awesome-paper/
├── run.py                    # Entry point
├── agent.py                  # Agent core logic
├── profiles/                 # User config (managed by agent)
│   └── default.json
├── core/
│   ├── config.py             # Profile config system
│   ├── orchestrator.py       # Async pipeline orchestration
│   ├── supervisor.py         # Pipeline monitor — captures output, produces concise reports
│   └── storage.py            # State tracking + data persistence
├── sources/
│   ├── base.py               # Source base class
│   └── arxiv.py              # arXiv scraper
├── analyzers/
│   ├── base.py               # Analyzer base class
│   └── llm_classifier.py     # LLM async parallel classifier
├── notifiers/
│   ├── base.py               # Notifier base class
│   └── feishu.py             # Feishu notifier
├── llm/
│   └── client.py             # Multi-backend LLM client (sync + async)
├── data/
│   ├── raw/                  # Raw scraped data
│   ├── paper_database/       # Classified archive
│   └── daily/                # Daily summaries
└── requirements.txt
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure LLM API

Edit `profiles/default.json` with your LLM API info (the only manual step):

```json
{
  "llm": {
    "analyzer": {
      "api_base": "https://openrouter.ai/api/v1",
      "model": "arcee-ai/trinity-large-preview:free",
      "api_key": "sk-or-v1-your-key-here"
    },
    "agent": {
      "api_base": "https://openrouter.ai/api/v1",
      "model": "arcee-ai/trinity-large-preview:free",
      "api_key": "sk-or-v1-your-key-here"
    }
  }
}
```

Any OpenAI-compatible API works. You can set the key directly in `api_key`, or use `api_key_env` to read from an environment variable.

### 3. Run

```bash
python run.py
```

The agent detects your config, tells you what's ready and what's missing, then guides you through the rest:

```
Assistant: Hi! Current config status:
  - arXiv categories: cs.CL, cs.CV, cs.LG, cs.AI ✓
  - Interest tags: not configured
  - Notification: not configured (optional, results saved locally)
  - Analyzer LLM: ready ✓
  Want to set up interest tags, or run the pipeline directly?

You: I'm interested in reasoning, multi-agent, and RAG
  [Agent] Calling: configure_subscription(...)
Assistant: Added 3 interest tags. Want to run the pipeline?

You: Sure, go ahead
  [Agent] Calling: run_pipeline(...)
Assistant: Done! Scraped 265 papers, classified and saved to data/daily/.
```

All other config (categories, interest tags, Feishu webhook) is done through conversation.

## Pipeline

```
Scrape  →  Classify  →  Send
  ↓           ↓          ↓
arXiv API   AsyncOpenAI  Feishu Webhook
XML parse   Semaphore    Rich cards
Dedup       Taxonomy     Tag filtering
```

Stage state is persisted to `data/automation_status.json`; completed stages are skipped on re-run.

## Pipeline Supervisor

`PipelineSupervisor` sits between the agent and the orchestrator:

- **Captures noise**: redirects `stdout` to suppress internal `print()` output
- **Concise reports**: compresses results into a one-line summary + structured fields
- **Tool result slimming**: `query_papers` capped to 10 items (title + area only); `show_config` strips keywords/schedule/data_dirs

The agent receives compact results like:

```json
{"status": "completed", "paper_count": 47, "summary": "Scraped 47 papers across 4 categories, classified 47, sent via feishu."}
```

## Configuration

All config is stored in `profiles/<name>.json` (default: `profiles/default.json`).

| Field | Description |
|-------|-------------|
| `subscriptions.categories` | arXiv categories, e.g. `cs.CL`, `cs.CV`, `cs.AI` |
| `subscriptions.interest_tags` | Interest tags for prioritization and filtering |
| `channels` | Notification channels (optional) |
| `channels[].exclude_tags` | Filter out papers with these tags |
| `llm.analyzer` | LLM config for the paper classifier |
| `llm.agent` | LLM config for the conversational agent |
| `llm.*.api_key` | API key (direct) |
| `llm.*.api_key_env` | Or env var name (e.g. `LLM_API_KEY`) |
| `llm.*.max_concurrency` | Max parallel LLM calls |

> Only `llm` API info needs manual setup. Everything else can be configured through conversation.

## Extending

**Add a notifier**: create a file in `notifiers/`, extend `BaseNotifier`, implement `send_digest()` / `from_channel_config()`, register in `NOTIFIER_REGISTRY`.

**Add a paper source**: create a file in `sources/`, extend `BaseSource`, implement `fetch()` / `save_raw()`.

## FAQ

**No papers on weekends?** arXiv updates on weekdays. Specify a date in conversation: `run pipeline for Feb 12`.

**Switch LLM?** Tell the agent: `switch analyzer to GPT-4o-mini`, or edit `profiles/default.json`.

**Faster classification?** Increase `llm.analyzer.max_concurrency` (default 5), subject to API rate limits.

**No notification channel?** Fine — results are saved in `data/daily/`. Search via conversation: `search for reasoning papers`.
