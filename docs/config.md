# 配置说明（config/roles.yaml）

完整示例见 [`config/roles.example.yaml`](../config/roles.example.yaml)。凭证只用 `${ENV_VAR}`（支持 `${VAR:-默认值}`）引用环境变量。

- `llm.providers.<name>`：`type: openai_compatible`，`base_url`、`api_key`、`model`；`supports_json_schema: true` 时使用 `response_format=json_schema(strict)`，否则使用 `json_object`，不支持时自动降级为纯 prompt 约束。
- `session.timeout_minutes` / `session.max_followups`：追问超时和最多追问轮次，超过即放弃、不写入。
- `chats_allowlist`：群白名单，空表示不限制。
- `audit_store_text`：是否在 SQLite `audit_log` 中保存原文。
- `roles[]`：`command`、`aliases`、`name`、`llm`（可选覆盖默认）、`allowed_chats`、`allowed_users`（open_id）、`target{app_token,table_id}`、`fields[]`。
- `fields[]`：`key`（给 AI 的字段名）、`field`（表格列名）、`type`、`required`、`default`、`description`、`options[{value,synonyms}]`、`min`/`max`/`precision`（number）、`max_len`（text）。
- 内置默认值变量：`$sender`、`$today`、`$now`、`$chat_name`、`$message_link`（MVP 中 `$chat_name`/`$message_link` 暂为空）。

环境变量：

| 变量 | 说明 |
|---|---|
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 必填，应用凭证 |
| `FEISHU_DOMAIN` | `feishu`（默认）或 `lark` |
| `FEISHU_BOT_OPEN_ID` | 可选；机器人自身 open_id，用于精确去掉 @机器人（不填时把消息开头的第一个 @ 视为机器人） |
| `BOT_CONFIG` | 配置文件路径，默认 `config/roles.yaml`（镜像内 `/app/config/roles.yaml`） |
| `BOT_DB_PATH` | SQLite 路径，默认 `data/bot.db`（镜像内 `/data/bot.db`） |
| `BOT_WORKERS` | 处理线程数，默认 4 |
| `LOG_LEVEL` | 日志级别，默认 INFO |
| `OPENAI_API_KEY` 等 | 配置文件中引用的 LLM Key |
