# 配置说明

[English](../en/configuration.md) · [← README](../../README.zh-CN.md)

完整示例见 [`config/roles.example.yaml`](../../config/roles.example.yaml)。

凭证**不要**写进 YAML，只用 `${ENV_VAR}` 引用（支持 `${VAR:-默认值}`）。

## 环境变量

| 变量 | 说明 |
|---|---|
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 必填，应用凭证 |
| `FEISHU_DOMAIN` | `feishu`（默认）或 `lark` |
| `FEISHU_BOT_OPEN_ID` | 可选；精确去掉 @机器人 |
| `BOT_SCENARIO` | 内置场景：`requirements` → 加载 `config/roles.requirements.yaml`（需求小助手） |
| `BOT_CONFIG` | 自定义 `roles.yaml` 路径（若设置则覆盖 `BOT_SCENARIO`） |
| `BOT_DB_PATH` | SQLite 路径（默认 `data/bot.db`；镜像内 `/data/bot.db`） |
| `BOT_WORKERS` | 处理线程数，默认 `4` |
| `LOG_LEVEL` | 默认 `INFO` |
| `OPENAI_API_KEY` / `DASHSCOPE_API_KEY` / … | YAML 中引用的 LLM Key |


## 内置场景：需求小助手

推荐用专属机器人应用部署「需求小助手」，避免多场景意图路由：

```bash
cp config/roles.requirements.yaml config/roles.yaml
# .env
BOT_SCENARIO=requirements
```

该配置启用：

| 项 | 值 |
|---|---|
| `scenario` / `dedicated` / `bot_name` | `requirements` / `true` / `需求小助手` |
| `react_on_mention` / `react_emoji` | `true` / `Get`（「收到」） |
| `context.recent_messages` | `5`（@ 时取前文最多 5 条） |
| `confirm_before_write` | `true`（话题内交互卡片：确认 / 取消 / 修改） |
| `query.max_reply` + `query_docs_hint` | `2` + 引导用户自行查表 |

意图只有两种：`record`（信息收集）与 `query`（进展咨询）。通用多角色见 `config/roles.example.yaml`。

## `roles.yaml` 全局项

| 键 | 说明 |
|---|---|
| `scenario` | 场景 id，如 `requirements` |
| `dedicated` | `true` 时必须恰好一个角色；跳过角色选择 |
| `bot_name` | 显示名（帮助文案 / 回执） |
| `react_on_mention` / `react_emoji` | @ 后点表情；失败静默忽略 |
| `query_docs_hint` | 查询有结果时追加的「请自行查表」提示 |
| `timezone` | 如 `Asia/Shanghai`，影响相对日期与 `$today` / `$now` |
| `llm.default` / `llm.providers.*` | OpenAI 兼容 provider（`base_url`、`api_key`、`model`、`supports_json_schema`、`timeout`） |
| `session.timeout_minutes` / `max_followups` | 追问超时与最多轮次 |
| `chats_allowlist` | 空=不限制；否则为群 ID 白名单 |
| `audit_store_text` | 是否在 SQLite 审计表保存原文 |
| `p2p_enabled` | 私聊无需 @（默认 `true`） |
| `ack_after_seconds` | LLM 超过 N 秒先回「收到，正在整理…」；`0` 关闭 |
| `first_guide_enabled` / `first_guide_text` | 每个群首次 @ 时的一句短引导 |
| `intent.enabled` | `false` 时强制斜杠命令 |
| `intent.min_confidence` | 默认 `0.7`；低于则请用户确认角色 |
| `intent.llm` | 意图识别用的 provider（可选） |
| `intent.query_hints` | 斜杠正文命中这些词时走进展查询 |
| `context.*` | `enabled`、`recent_messages`（0–50）、`include_parent`、`include_thread`、`thread_messages`、`max_chars`、`name_cache_seconds` |

## 角色 `roles[]`

| 字段 | 说明 |
|---|---|
| `command` / `aliases` | 斜杠命令，如 `/bug`、`/日报` |
| `name` | 角色显示名（回执里用） |
| `description` | 帮助自然语言意图选中该角色 |
| `llm` | 可选，覆盖默认 provider |
| `allowed_chats` / `allowed_users` | 空=不限制 |
| `target.app_token` / `table_id` | 目标表（**示例里用占位符；真实值只放本地 `roles.yaml`**） |
| `confirm_before_write` | 写前预览确认 |
| `system_prompt` | 追加到抽取 system 提示 |
| `guide_prompt` | 内容过短时的引导文案 |
| `reply_style` | 约束追问 / 引导 / 查询回复语气 |
| `min_content_chars` | `0`=关闭；生产常用 `8`–`12` |
| `receipt_title` | 可选，覆盖成功回执标题 |
| `queryable` | 是否允许进展查询 |
| `query.search_fields` / `reply_fields` / `max_candidates` / `max_reply` / `fallback_limit` | 查询流水线参数 |
| `fields[]` | 字段定义（见下） |

### 字段

| 属性 | 说明 |
|---|---|
| `key` | 给 LLM / 查询用的标识 |
| `field` | 多维表格列名（须完全一致） |
| `type` | `text` `number` `single_select` `multi_select` `date` `checkbox` `user` `url` `phone` |
| `required` / `default` / `description` | 校验与提示 |
| `options[{value,synonyms}]` | 单选 / 多选 |
| `min` / `max` / `precision` | 数字 |
| `max_len` | 文本 |

### 溯源默认值

可用作 `default:`：

`$sender` · `$sender_name` · `$today` · `$now` · `$raw_text` · `$context` · `$chat_name` · `$message_link`

其中 `$raw_text` / `$context` / `$chat_name` / `$message_link` / `$now` 由系统强制填写，不进入 LLM JSON schema。

## 查询行为

- 意图返回 `action=query`，或斜杠正文命中 `query_hints`。
- 查询**不开** session、**不写**表。
- 流水线：对 `search_fields` 做 Bitable `contains` → LLM 重排 → Top `max_reply`；0 命中则拉最近 `fallback_limit` 条再重排。
