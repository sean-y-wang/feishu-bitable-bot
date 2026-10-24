# Feishu / Lark app setup

[中文](../zh-CN/feishu-setup.md) · [← README](../../README.md)

## 1. Create the app

1. Open [Feishu Open Platform](https://open.feishu.cn/app) (or [Lark](https://open.larksuite.com/app)).
2. Create an **enterprise custom app**.
3. Copy **App ID** / **App Secret** into `.env` as `FEISHU_APP_ID` / `FEISHU_APP_SECRET`.
4. **Add capabilities** → enable **Bot**.

## 2. Permissions (scopes)

| Scope | Purpose | Required |
|---|---|---|
| `im:message.group_at_msg:readonly` | Receive @-mentions in groups | **Yes** |
| `im:message:send_as_bot` | Replies, previews, receipts | **Yes** |
| `bitable:app` | Create / read Bitable rows | **Yes** |
| `im:message.p2p_msg:readonly` | Private chat without `@` | Recommended |
| `im:message.group_msg` | Group history + in-thread replies without `@` (**sensitive**, admin review) | Recommended for context |
| `im:message:readonly` or `im:message.history:readonly` | Read history / quoted messages / message position | Recommended |
| `im:chat:readonly` | Chat name (`$chat_name`) and member display names | Recommended |
| `contact:contact.base:readonly` + `contact:user.base:readonly` | Directory name for `$sender_name` | Optional |
| `im:message.reaction` (or reaction-related scope) | 「收到」emoji on @ (`react_on_mention`) | Recommended for Requirements Assistant |

Notes:

- Contact APIs also need **Data permission → Contacts range**. Missing range returns `41050`; the bot falls back to group member name, then `open_id`.
- With `im:message.group_msg`, the bot receives *all* group messages but only handles @-mentions and replies inside its own follow-up threads; everything else is ignored silently.
- Missing optional scopes degrade features; they do not crash the bot.

Bot `open_id` is auto-fetched via `GET /open-apis/bot/v3/info` (no extra scope). Override with `FEISHU_BOT_OPEN_ID` if needed.

## 3. Event subscription

1. Start this service once so the long connection is online.
2. **Events & callbacks** → **Event configuration** → **Receive events via long connection**.
3. Add event **`im.message.receive_v1`** (Receive message).
4. Card callback `card.action.trigger` on the long connection (required for Confirm / Cancel / Modify buttons; text fallback still works).

## 4. Publish

**Version management & release** → create a version and publish. Scope changes need a new version (and possibly admin approval).

## 5. Add the app to each Bitable

In every target Base: **⋯ → More → Add document app** → select this app → **Edit** permission. Without this, writes fail (e.g. `91403`).

From the Base URL, copy:

- `app_token` — the `bascn…` segment after `/base/`
- `table_id` — the `tbl…` value of `?table=`

Put them in `config/roles.yaml` under `target`. Wiki-hosted Bases expose a wiki node token in the URL — use the real bitable `app_token` instead.

### Field type mapping

| Template `type` | Bitable column type |
|---|---|
| `text` / `phone` | Text / Phone |
| `number` | Number |
| `single_select` / `multi_select` | Single / Multi select (options must already exist) |
| `date` | Date |
| `checkbox` | Checkbox |
| `user` | Person |
| `url` | URL |

## 6. Invite & try

Add the bot to a group (or open a DM). Examples:

```text
@Bot 订单导出太慢了，P1，周五前要
@Bot /bug 登录页偶发卡顿，P2
@Bot Acme API 上线了吗？
```

## Traceability placeholders

| Placeholder | Content | Suggested column |
|---|---|---|
| `$sender_name` | Display name (contacts → member name → open_id) | Text |
| `$raw_text` | Trigger message without the @bot mention | Multi-line text |
| `$context` | Context excerpt (quote / thread / recent) | Multi-line text |
| `$chat_name` | Group name; DM → `单聊` | Text |
| `$message_link` | Applink to the message (or chat) | URL |
| `$now` | Submit time | Date (include time) |

`$raw_text` / `$context` / `$chat_name` / `$message_link` / `$now` are always system-filled (not sent to the LLM schema). Large trace fields are hidden from group receipts by default.

## Progress query

No extra scope beyond `bitable:app`. Pipeline: Bitable `contains` search (≤20) → LLM rerank Top‑N; on zero hits, pull the latest `fallback_limit` rows and rerank again. Reply fields default to title / status / priority / proposer / due — not raw text or context.
