# 飞书开放平台应用配置

1. 在 [飞书开放平台](https://open.feishu.cn/app) 创建**企业自建应用**，在「凭证与基础信息」拿到 `App ID` / `App Secret`，写入 `.env` 的 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`。
2. 「添加应用能力」→ 开启 **机器人**。
3. 「权限管理」开通以下 scope：

| scope | 用途 | 必需 |
|---|---|---|
| `im:message.group_at_msg:readonly` | 接收群里 @机器人 的消息 | 是 |
| `im:message:send_as_bot` | 以机器人身份回复（追问、回执） | 是 |
| `bitable:app` | 新增多维表格记录 | 是 |
| `im:message.p2p_msg:readonly` | 私聊使用 | 可选 |
| `im:chat.members:read`（或 `im:chat:readonly`） | 按姓名匹配群成员填人员字段 | 可选（不开时只能 @ 人） |

4. 「事件与回调」→「事件配置」→ 订阅方式选 **使用长连接接收事件**（需先启动一次本服务建立连接后才能保存），添加事件 **接收消息 `im.message.receive_v1`**。
   - P1 才会用到卡片回调 `card.action.trigger`（「回调配置」中同样选长连接），MVP 不需要。
5. 「版本管理与发布」→ 创建版本并发布（权限变更需重新发版，企业管理员审核通过后生效）。
6. 每张目标多维表格：右上角「…」→「更多」→「添加文档应用」，添加本应用并给 **可编辑** 权限；否则写入会报 91403 等无权限错误。
7. 把机器人拉进需要使用的群。
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
