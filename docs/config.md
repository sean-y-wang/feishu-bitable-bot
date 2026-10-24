# 配置说明（config/roles.yaml）

完整示例见 [`config/roles.example.yaml`](../config/roles.example.yaml)。凭证只用 `${ENV_VAR}`（支持 `${VAR:-默认值}`）引用环境变量。

- `llm.providers.<name>`：`type: openai_compatible`，`base_url`、`api_key`、`model`；`supports_json_schema: true` 时使用 `response_format=json_schema(strict)`，否则使用 `json_object`，不支持时自动降级为纯 prompt 约束。
- `session.timeout_minutes` / `session.max_followups`：追问超时和最多追问轮次，超过即放弃、不写入。
- `chats_allowlist`：群白名单，空表示不限制。
- `audit_store_text`：是否在 SQLite `audit_log` 中保存原文。
- `roles[]`：`command`、`aliases`、`name`、`llm`（可选覆盖默认）、`allowed_chats`、`allowed_users`（open_id）、`target{app_token,table_id}`、`fields[]`。
- `fields[]`：`key`（给 AI 的字段名）、`field`（表格列名）、`type`、`required`、`default`、`description`、`options[{value,synonyms}]`、`min`/`max`/`precision`（number）、`max_len`（text）。
- 内置默认值变量：`$sender`（人员字段=发送者）、`$sender_name`、`$today`、`$now`、`$raw_text`、`$context`、`$chat_name`、`$message_link`。其中 `$raw_text`/`$context`/`$chat_name`/`$message_link`/`$now` 为系统字段：不出现在 LLM schema 中，始终由系统填写。详见 [feishu-app-setup.md](feishu-app-setup.md#溯源字段)。
- 角色可写 `description`，帮助 LLM 做意图识别。
- `p2p_enabled`（默认 true）：私聊无需 @。
- `intent`：`enabled`（false 时恢复“必须用命令”）、`min_confidence`（默认 0.7）、`llm`（可选，默认 `llm.default`）。
- `context`：`enabled`、`recent_messages`（0–50，默认 10）、`include_parent`、`include_thread`、`thread_messages`、`max_chars`（默认 3000）、`name_cache_seconds`（默认 600）。

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


## 角色级提示词与进展查询

| 字段 | 说明 |
|---|---|
| `system_prompt` | 追加到抽取 system（【本角色补充指令】） |
| `guide_prompt` | 正文过短时的引导文案；未配则按必填字段自动生成 |
| `reply_style` | 一句话约束追问/引导/查询回复语气 |
| `min_content_chars` | 默认 0（关闭）；设为 8–12 且正文+上下文都短于该值时发引导、不抽取、不开 session |
| `confirm_before_write` | true 时字段齐后先预览，用户确认/改字段后再写入 |
| `receipt_title` | 可选，覆盖成功回执标题 |
| `queryable` | true 时允许进展查询 |
| `query.search_fields` | 检索用的字段 key（映射到列名 contains） |
| `query.reply_fields` | 回复里展示的字段；默认不含溯源大字段 |
| `query.max_candidates` / `max_reply` / `fallback_limit` | 候选上限、回复条数、0 命中时拉最近 N 条再 rerank |

全局：`ack_after_seconds`（LLM 超时先回「收到，正在整理…」，0=关）、`first_guide_enabled` / `first_guide_text`（每个群首次 @ 一句短引导）。

查询意图：`intent.classify` 返回 `action=query`；slash 正文命中 `intent.query_hints` 也会走查询。查询不写 session、不写表。
