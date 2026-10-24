# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). [简体中文](CHANGELOG.zh-CN.md)

## [Unreleased]

### Fixed
- Dashboard: LLM provider `base_url` is now read-only / restart-required. It was wrongly listed as hot-editable; edits via the API are rejected and the UI marks it "read-only / restart".

### Docs
- Added this changelog (EN + 中文); documented Mac mini LAN binding (`DASHBOARD_HOST=<LAN IP>`), hot vs restart settings, `roles.yaml` comments lost on dashboard save (`.bak` kept), and that fallback providers need their API keys.

## 2026-10 — Built-in runtime dashboard (#7)

### Added
- In-process dashboard (stdlib `http.server`, token auth, default `127.0.0.1:8787`): runtime overview, Feishu connection state, business-config load status with one-click hot reload, editable system config (LLM default/fallback/models/timeouts, session/intent/context knobs), masked secrets with overwrite (never returns plaintext), audit log `data/dashboard_audit.log`.
- `DASHBOARD_TOKEN` / `DASHBOARD_HOST` / `DASHBOARD_PORT` / `BOT_ENV_FILE` env vars.

## 2026-10 — Bindings, remote business config, LLM fallback (#6)

### Added
- `chat_field_bindings`: pin a Bitable field value (e.g. project/direction) per Feishu group chat.
- `remote_config`: business role config (prompts, guide text, bindings, field hints) read from a Feishu Bitable table, periodic refresh, fallback to local YAML; secrets stay in `.env`.
- LLM fallback via `ChainedProvider`: same-provider `fallback_models` first, then cross-provider `llm.fallback`.
- Configuration docs refreshed for go-live.

## 2026-10 — Requirements Assistant UX optimizations (#5)

### Changed
- Help gate plus collect / consult intents; fast rules + 3-way LLM classifier (help / record / query) with confidence threshold.
- 15-minute session timeout and smoother confirm / modify / cancel flow.
