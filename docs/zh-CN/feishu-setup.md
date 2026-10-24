# 飞书 / Lark 应用配置

[English](../en/feishu-setup.md) · [← README](../../README.zh-CN.md)

## 1. 创建应用

1. 打开 [飞书开放平台](https://open.feishu.cn/app)（国际版见 [Lark](https://open.larksuite.com/app)）。
2. 创建**企业自建应用**。
3. 在「凭证与基础信息」拿到 **App ID** / **App Secret**，写入 `.env` 的 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`。
4. 「添加应用能力」→ 开启 **机器人**。

## 2. 权限（scopes）

| scope | 用途 | 必需 |
|---|---|---|
| `im:message.group_at_msg:readonly` | 接收群里 @机器人 的消息 | **是** |
| `im:message:send_as_bot` | 回复、预览、回执 | **是** |
| `bitable:app` | 新增多维表格记录 / 检索 | **是** |
| `im:message.p2p_msg:readonly` | 私聊无需 @ | 建议 |
| `im:message.group_msg` | 群历史 + 话题内不 @ 的补充回答（**敏感权限**，需管理员审核） | 建议（上下文） |
| `im:message:readonly` 或 `im:message.history:readonly` | 读历史、引用消息、消息位置 | 建议 |
| `im:chat:readonly` | 群名（`$chat_name`）与群成员姓名 | 建议 |
| `contact:contact.base:readonly` + `contact:user.base:readonly` | 通讯录姓名（`$sender_name`） | 可选 |

说明：

- 通讯录接口还受「数据权限 → 通讯录权限范围」限制；缺范围会返回 `41050`，机器人会降级为群成员名，再退回 `open_id`。
- 开通 `im:message.group_msg` 后会收到群内全部消息，但服务只处理 @机器人 的消息以及自己发起的追问话题里的回复，其它消息静默忽略。
- 可选权限缺失只降级功能，不会让服务崩溃。

机器人自身 `open_id` 通过 `GET /open-apis/bot/v3/info` 自动获取（无需额外权限）；也可用 `FEISHU_BOT_OPEN_ID` 覆盖。

## 3. 事件订阅

1. 先启动一次本服务，让长连接在线。
2. 「事件与回调」→「事件配置」→ 订阅方式选 **使用长连接接收事件**。
3. 添加事件 **接收消息 `im.message.receive_v1`**。
4. 卡片回调 `card.action.trigger` 留给后续更丰富的卡片动作；当前 MVP 回执卡片不依赖它。

## 4. 发布

「版本管理与发布」→ 创建版本并发布。权限变更需重新发版（可能需要管理员审批）。

## 5. 把应用加到每张多维表格

每张目标表：右上角 **⋯ → 更多 → 添加文档应用** → 选本应用 → **可编辑**。否则写入会失败（如 `91403`）。

从多维表格 URL 取：

- `app_token`：`/base/` 后的 `bascn…`
- `table_id`：`?table=` 的 `tbl…`

写入 `config/roles.yaml` 的 `target`。知识库（Wiki）里的链接往往是 wiki node token，请换成真正的 bitable `app_token`。

### 字段类型对应

| 模板 `type` | 多维表格列类型 |
|---|---|
| `text` / `phone` | 多行文本 / 电话号码 |
| `number` | 数字 |
| `single_select` / `multi_select` | 单选 / 多选（选项需预先建好） |
| `date` | 日期 |
| `checkbox` | 复选框 |
| `user` | 人员 |
| `url` | 超链接 |

## 6. 拉群试用

把机器人拉进群（或直接私聊）。例如：

```text
@机器人 订单导出太慢了，P1，周五前要
@机器人 /bug 登录页偶发卡顿，P2
@机器人 Acme API 上线了吗？
```

## 溯源字段

| 占位符 | 内容 | 建议列类型 |
|---|---|---|
| `$sender_name` | 发送者姓名（通讯录 → 群成员名 → open_id） | 文本 |
| `$raw_text` | 触发消息原文（去掉 @机器人） | 多行文本 |
| `$context` | 上下文摘录（引用 / 话题 / 最近消息） | 多行文本 |
| `$chat_name` | 群名；私聊为「单聊」 | 文本 |
| `$message_link` | 消息 AppLink（能定位则定位，否则打开会话） | 超链接 |
| `$now` | 提交时间 | 日期（含时分） |

`$raw_text` / `$context` / `$chat_name` / `$message_link` / `$now` 由系统强制填写，不进入 LLM schema。溯源大字段默认不出现在群回执里。

## 进展查询

不新增 scope，与写入共用 `bitable:app`。流水线：Bitable `contains` 检索（≤20）→ LLM 重排 Top‑N；0 命中则拉最近 `fallback_limit` 条再重排。回复默认含标题 / 状态 / 优先级 / 提出人 / 期望上线，不含原文与上下文。
