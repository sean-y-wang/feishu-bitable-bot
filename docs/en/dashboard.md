# Runtime dashboard

The dashboard runs **inside the bot process**: one extra internal HTTP port (stdlib `http.server`, no new dependencies) sharing config and runtime state with the Feishu long connection.

Two config layers:

| Layer | Where | Applied |
|---|---|---|
| Business (prompts, guide copy, chat bindings, field descriptions) | Feishu “bot config” Bitable (`remote_config`) | “Reload business config” button, or `refresh_seconds` auto-refresh |
| System (LLM, fallback, timeouts, thresholds, secrets) | local `roles.yaml` + `.env` (gitignored) | hot on save (a few items need a restart) |

## Enable

```bash
DASHBOARD_TOKEN=<python -c "import secrets;print(secrets.token_urlsafe(32))">
DASHBOARD_HOST=127.0.0.1   # loopback by default; set the LAN IP for office access
DASHBOARD_PORT=8787
```

No `DASHBOARD_TOKEN` → dashboard disabled. Token must be ≥16 chars. Open `http://<host>:8787/`; the API uses `Authorization: Bearer <token>`. Never expose it to the internet — it can change secrets.

## Panels

1. **Overview**: bot/scenario, roles, WS state, start time/uptime, today's records/queries/guides, LLM primary + chain, fallback count, last 30 errors.
2. **Business config**: source (table / local / local fallback), last load time, trigger, result, rows, error.
3. **System config**: LLM default, fallback order, per-provider model / same-vendor fallbacks / timeout; session timeout, intent confidence, context sizes, refresh interval. Validated, written back to the YAML (`.bak` kept, comments not preserved), then hot-reloaded.
4. **Secrets**: `FEISHU_APP_SECRET` and every `${VAR}` used as a provider `api_key`, **masked only** (`sk-****a1b2`); overwrite with a new value → `.env` (`BOT_ENV_FILE`). The API never returns plaintext.
5. **Reload**: re-read YAML + Feishu table and hot-swap; **on failure the old config stays** and the error is shown.

Every change is appended to `data/dashboard_audit.log` (key names only).

## Hot vs restart

Hot: business table, LLM default/fallback/models/timeouts, LLM API keys, session/intent/context knobs. Restart: Feishu App ID/Secret/domain, `DASHBOARD_*`, `BOT_CONFIG`/`BOT_SCENARIO`/`BOT_DB_PATH`/`BOT_WORKERS`/`LOG_LEVEL`, role structure (recommended).

## Deploy

`docker-compose.yml` maps `127.0.0.1:8787:8787` (container listens on `0.0.0.0`); change to `"<LAN IP>:8787:8787"` for office access. `./config` and `./.env` are mounted writable so the dashboard can save.
