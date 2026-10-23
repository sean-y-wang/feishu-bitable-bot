# feishu-bitable-bot

飞书/Lark 机器人：在群里 **@机器人**（或私聊）说一句话，AI 识别要记到哪张表（也可用命令显式指定），结合聊天上下文把自然语言提取成字段，写入对应的**飞书多维表格（Bitable）**。

```
@记录机器人 /日报 今天完成了登录模块，明天联调，工时 6 小时，进度顺利
→ ✅ 已记录到「研发日报」 提交人 / 日期 / 今日完成 / 明日计划 / 工时 / 状态 …
```

## 特性（P0 MVP）

- **长连接（WebSocket）接收事件**（`lark-oapi` `ws.Client`），无需公网回调地址，可部署在内网
- **自然语言识别角色**：无需命令，LLM 识别意图；置信度低时追问确认。命令（`/日报`、`/bug`…）仍可显式指定，每个角色有自己的模板、目标表和字段
- **上下文综合**：自动带上被引用消息、所在话题与最近 N 条群消息
- **溯源字段**：`$sender_name` `$raw_text` `$context` `$chat_name` `$message_link` `$now`
- **私聊**：单聊无需 @
- **AI 字段提取**：OpenAI 兼容接口（OpenAI / 通义 / DeepSeek / Kimi / 智谱 / vLLM / Ollama…），json_schema → json_object → prompt 自动降级，解析失败带错误回传重试
- **确定性校验与归一化**：文本、数字（含“三点五”“3.5h”“两个半”）、单选/多选（同义词、全半角、编辑距离≤1）、日期（相对日期，Asia/Shanghai）、人员（@ 提及 / 群成员姓名）、链接、电话、复选框
- **缺必填字段时在话题里文字追问**，支持超时与 `/取消`
- **幂等**：`event_id` + `message_id` 去重（24h），写入带 `client_token`，并记录 `message_id → record_id`
- 429/5xx 退避重试；无表权限等错误给出可读提示
- SQLite 存储会话、去重、审计；JSON 结构化日志（敏感字段脱敏）
- Docker：非 root、只读根文件系统、无入站端口；**凭证只来自环境变量**

## 快速开始

见 [docs/quickstart.md](docs/quickstart.md)，飞书后台配置见 [docs/feishu-app-setup.md](docs/feishu-app-setup.md)，配置项见 [docs/config.md](docs/config.md)。

```bash
cp .env.example .env && cp config/roles.example.yaml config/roles.yaml
docker compose up -d --build
```

## 命令

| 命令 | 说明 |
|---|---|
| `/<角色命令> 内容` | 按角色模板提取并写入，如 `/日报`、`/bug` |
| `/取消` | 放弃当前未完成（追问中）的记录 |
| `/help` | 列出可用命令 |

## 开发

```bash
pip install -e ".[dev]"
ruff check src tests
pytest -q --cov=bitable_bot
```

所有外部调用（飞书 OpenAPI、LLM）在测试中均为 fake/mock。

## 路线图

- P1：补填/确认交互卡片（`card.action.trigger`）、fallback provider、启动时校验表结构、`/help` 自动生成增强
- P2：Redis 与多实例、配置热加载、附件字段、`/撤销`、Prometheus 指标
- P3：Web 配置界面、从多维表格字段自动生成模板

## License

[MIT](LICENSE)
