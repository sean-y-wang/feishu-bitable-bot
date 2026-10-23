# 飞书开放平台应用配置

1. 在 [飞书开放平台](https://open.feishu.cn/app) 创建**企业自建应用**，在「凭证与基础信息」拿到 `App ID` / `App Secret`，写入 `.env` 的 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`。
2. 「添加应用能力」→ 开启 **机器人**。
3. 「权限管理」开通以下 scope：

| scope（权限名） | 用途 | 必需 |
|---|---|---|
| `im:message.group_at_msg:readonly`（接收群聊中@机器人消息事件） | 接收群里 @机器人 的消息 | 是 |
| `im:message:send_as_bot`（以应用的身份发消息） | 回复（追问、确认角色、回执） | 是 |
| `bitable:app`（查看、评论、编辑和管理多维表格） | 新增多维表格记录 | 是 |
| `im:message.p2p_msg:readonly`（读取用户发给机器人的单聊消息） | 私聊机器人无需 @ 即触发 | 建议 |
| `im:message.group_msg`（获取群组中所有消息，**敏感权限**，需管理员审核） | ① 读取群历史（最近 N 条/话题/被引用消息）作为上下文；② 话题内不 @ 的补充回答也能收到 | 建议（不开则无群上下文） |
| `im:message:readonly`（获取单聊、群组消息）或 `im:message.history:readonly`（获取单聊、群组历史消息） | 调用 `GET /im/v1/messages`、`GET /im/v1/messages/{id}`（历史、引用消息、消息链接位置） | 建议 |
| `im:chat:readonly`（获取群组信息） | 群名（`$chat_name`）与群成员姓名（按名字匹配人员、`$sender_name` 兜底） | 建议 |
| `contact:contact.base:readonly`（获取通讯录基本信息）+ `contact:user.base:readonly`（获取用户基本信息） | 通讯录姓名（`$sender_name`） | 可选（不开时用群成员名，再退回 open_id） |

> 通讯录接口还受「权限管理 → 数据权限 → 通讯录权限范围」限制：把需要使用的部门/全员加入范围，否则返回 41050，机器人会自动降级为群成员名。
> 开通 `im:message.group_msg` 后机器人会收到群里**所有**消息：服务只处理 @机器人 的消息和正在追问/确认的话题里的回复，其它消息直接丢弃，不回复、不记录。
> 所有可选权限缺失时都会降级而不报错（上下文为空、群名用 chat_id、姓名用 open_id）。

机器人自身 open_id 通过 `GET /open-apis/bot/v3/info` 自动获取（无需额外权限），用于精确判断是否被 @；也可在 `.env` 中用 `FEISHU_BOT_OPEN_ID` 指定。

4. 「事件与回调」→「事件配置」→ 订阅方式选 **使用长连接接收事件**（需先启动一次本服务建立连接后才能保存），添加事件 **接收消息 `im.message.receive_v1`**。
   - P1 才会用到卡片回调 `card.action.trigger`（「回调配置」中同样选长连接），MVP 不需要。
5. 「版本管理与发布」→ 创建版本并发布（权限变更需重新发版，企业管理员审核通过后生效）。
6. 每张目标多维表格：右上角「…」→「更多」→「添加文档应用」，添加本应用并给 **可编辑** 权限；否则写入会报 91403 等无权限错误。
7. 把机器人拉进需要使用的群；私聊直接在飞书搜索机器人即可（`p2p_enabled: true`）。
8. 从多维表格 URL 获取 `app_token`（`/base/` 后的 `bascn...`）和 `table_id`（`?table=tbl...`），填进 `config/roles.yaml` 的 `target`；`field` 必须与表中列名**完全一致**，类型对应：

| 模板 type | 多维表格字段类型 |
|---|---|
| text / phone | 多行文本 / 电话号码 |
| number | 数字 |
| single_select / multi_select | 单选 / 多选（选项需预先建好，机器人不会新建选项） |
| date | 日期 |
| checkbox | 复选框 |
| user | 人员 |
| url | 超链接 |

> 注意：知识库（Wiki）中的多维表格 URL 里的 token 是 wiki node token，需要换成真正的 bitable `app_token`。

## 使用方式（自然语言 / 命令）

- 群里 @机器人 + 自然语言即可，例如「@机器人 AIHub 货架页加个导出按钮，挺急的」。LLM 先结合上下文识别角色，置信度 ≥ `intent.min_confidence`（默认 0.7）直接提取写入；否则在话题中回复候选角色，用户回「是」/角色名/命令确认（最多 `session.max_followups` 次）。
- 斜杠命令（`/提需求 ...`）仍然有效，优先级最高，跳过意图识别。
- 回复/引用某条消息再 @机器人（甚至只发 `@机器人 /提需求`），被引用消息、所在话题和最近 N 条群消息会一起交给 LLM 综合。
- 私聊机器人不需要 @。

## 溯源字段

| 占位符 | 内容 | 建议字段类型 |
|---|---|---|
| `$sender_name` | 发送者姓名（通讯录 → 群成员名 → open_id） | 文本 |
| `$raw_text` | 触发消息原文（去掉 @机器人） | 多行文本 |
| `$context` | 上下文摘录（引用消息/话题/最近消息，受 `context.max_chars` 限制） | 多行文本 |
| `$chat_name` | 群名；私聊为「单聊」 | 文本 |
| `$message_link` | 触发消息的 AppLink（有 position 时定位到消息，否则打开该群） | 超链接 |
| `$now` | 提交时间 | 日期（格式选含时分的样式） |

除 `$sender_name` / `$sender` / `$today` 外，这些字段由系统强制填写，不交给 LLM，回执里也不展示原文和上下文。
