# feishu-bitable-bot

[![CI](https://github.com/sean-y-wang/feishu-bitable-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/sean-y-wang/feishu-bitable-bot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

**Turn Feishu / Lark chat into structured Bitable rows — with AI, no public webhook required.**

[简体中文](README.zh-CN.md) · [Quick start](docs/en/quickstart.md) · [Feishu setup](docs/en/feishu-setup.md) · [Configuration](docs/en/configuration.md) · [Architecture](docs/en/architecture.md) · [Contributing](CONTRIBUTING.md)

---

Capture requirements, daily reports, and bugs from group chat or DM. The bot understands natural language (slash commands optional), pulls conversation context, confirms before writing when you want, and keeps a full trail back to the original message. Ask “is Acme API shipped yet?” and it searches the same table for progress.

```text
You:  @RecordBot order export is too slow, P1, need it by Friday
Bot:  📋 Preview →「缺陷登记」
      · 标题：订单导出过慢
      · 严重程度：P1
      · 截止日期：本周五
      Reply 好 to save, or 严重程度改成 P0 to edit.

You:  好
Bot:  ✅ Saved — [open record]

You:  @RecordBot Acme API 上线了吗？
Bot:  🔍 Found 2 related rows
      1. Acme API 流式返回 — 状态：联调中 · 负责人：…
```

Works over Feishu’s **WebSocket long connection** — deploy on a laptop or intranet box; no public callback URL.

## Why this project

| Pain | What the bot does |
|---|---|
| Chat decisions vanish into scrollback | Writes a structured row the moment someone speaks up |
| Filling Bitable by hand is slow | LLM extracts fields; you only confirm or fix |
| “Who said what, in which thread?” | Traceability: proposer, raw text, context excerpt, message link |
| “What’s the status of X?” | Progress query: table search + LLM rerank, no vector DB |
| Intranet / no public IP | Long connection outbound only; Docker-friendly |

## Features

- **Long connection (WebSocket)** — `lark-oapi` `ws.Client`; no inbound ports
- **Multi-role templates** — each role owns a Bitable target, fields, prompts, and optional query config
- **Natural-language intent** — LLM picks role + `record` / `query`; low confidence asks you to confirm; slash commands always win
- **Conversation context** — quoted message, thread, recent history (graceful degrade if scopes missing)
- **Confirm before write** — preview + inline edits (`优先级改成 P0`) before insert
- **Role system / guide prompts** — per-role tone; short messages get a guided follow-up instead of a bad row
- **Progress query** — Bitable `contains` search → LLM rerank → Top‑N summary
- **Traceability placeholders** — `$sender_name` `$raw_text` `$context` `$chat_name` `$message_link` `$now`
- **Private chat (p2p)** — DM without `@` when enabled
- **OpenAI-compatible LLM** — OpenAI, DashScope/Qwen, DeepSeek, Kimi, Zhipu, vLLM, Ollama, …
- **Deterministic validation** — numbers, selects (synonyms / edit distance), relative dates, users, URLs
- **Idempotent writes** — event/message dedup + Feishu `client_token` (UUIDv4)
- **Hardened Docker** — non-root, read-only rootfs, credentials only from env

## Requirements

- Python **3.11+**
- A Feishu / Lark **custom app** (bot + long-connection event subscription)
- One or more **Bitable** bases the app can edit
- An **OpenAI-compatible** LLM API key

## Quick start

```bash
git clone https://github.com/sean-y-wang/feishu-bitable-bot.git
cd feishu-bitable-bot
cp .env.example .env                       # App ID / Secret / LLM keys
cp config/roles.example.yaml config/roles.yaml   # app_token / table_id / fields
docker compose up -d --build
docker compose logs -f bot
```

Then: open the Feishu developer console → scopes + long connection + publish → add the app to each Base with edit permission → invite the bot to a group.

| Guide | Contents |
|---|---|
| [Quick start](docs/en/quickstart.md) | Local / Docker run, first mention |
| [Feishu setup](docs/en/feishu-setup.md) | Scopes, Bitable, publish checklist |
| [Configuration](docs/en/configuration.md) | Full `roles.yaml` reference |
| [Architecture](docs/en/architecture.md) | Modules & data flow |

中文文档：[快速开始](docs/zh-CN/quickstart.md) · [飞书配置](docs/zh-CN/feishu-setup.md) · [配置说明](docs/zh-CN/configuration.md) · [架构](docs/zh-CN/architecture.md)

## Project structure

```text
feishu-bitable-bot/
├── config/
│   └── roles.example.yaml      # copy → roles.yaml (gitignored)
├── docs/
│   ├── en/                     # English guides
│   └── zh-CN/                  # Chinese guides
├── src/bitable_bot/
│   ├── main.py                 # entrypoint & wiring
│   ├── service.py              # record / query / confirm pipeline
│   ├── router.py               # slash, @ / p2p, help, cancel
│   ├── context.py              # conversation context collector
│   ├── receipt.py              # interactive / text receipts
│   ├── config/                 # YAML load + pydantic models
│   ├── gateway/                # WebSocket long connection
│   ├── extractor/              # intent, extract, prompts
│   ├── query/                  # Bitable search + LLM rerank
│   ├── writer/                 # Feishu OpenAPI + insert
│   ├── validator/              # normalize & validate fields
│   ├── session/                # SQLite sessions, dedup, audit
│   └── providers/              # OpenAI-compatible client
├── tests/                      # unit + integration (all faked)
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## Configuration at a glance

Secrets stay in `.env` (never commit). Role templates live in `config/roles.yaml` (gitignored) — start from [`config/roles.example.yaml`](config/roles.example.yaml). Use placeholder tokens like `bascnXXXX` / `tblXXXX` until you paste real ones locally.

| Knob | Purpose |
|---|---|
| `intent.*` | NL role detection, confidence floor, query hint phrases |
| `context.*` | How much chat history to attach |
| `p2p_enabled` | Allow private chat without `@` |
| `ack_after_seconds` | “Got it, working…” while the LLM runs |
| `first_guide_*` | One-shot tip the first time a group @-mentions the bot |
| `roles[].system_prompt` / `guide_prompt` / `reply_style` | Per-role voice & short-content guidance |
| `roles[].confirm_before_write` | Preview before insert |
| `roles[].queryable` + `query.*` | Progress lookup |
| Traceability `default: $…` | Filled by the system, never by the LLM |

Details: [docs/en/configuration.md](docs/en/configuration.md).

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check src tests
pytest -q --cov=bitable_bot
```

Feishu OpenAPI and LLM calls are fully mocked in tests — no network required. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Roadmap

**Available on `feat/mvp-p0`**

- Long connection, multi-role templates, AI extract + validate, follow-ups
- NL intent, context, p2p, confirm-before-write, interactive receipt card
- Role prompts / guidance, progress query, first-guide, slow-ack

**Next**

- Richer card actions (edit / cancel the just-written row from the receipt)
- Startup check that Bitable columns match the template
- `/撤销` (undo), attachment fields
- Redis + multi-instance (long connection still prefers one receiver per app)
- Optional web UI for role templates

## Security

- Never commit `.env` or `config/roles.yaml` — both are gitignored
- Credentials only via environment variables (`${ENV_VAR}` in YAML)
- Logs redact sensitive fields; audit text storage is configurable (`audit_store_text`)

## License

[MIT](LICENSE) — free to use, modify, and redistribute.

---

[简体中文 README](README.zh-CN.md)
