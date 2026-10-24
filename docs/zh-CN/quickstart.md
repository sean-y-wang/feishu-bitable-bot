# 快速开始

[English](../en/quickstart.md) · [← README](../../README.zh-CN.md)

## 1. 克隆并配置

```bash
git clone https://github.com/sean-y-wang/feishu-bitable-bot.git
cd feishu-bitable-bot
cp .env.example .env
cp config/roles.example.yaml config/roles.yaml
```

编辑 `.env`：

| 变量 | 必填 | 说明 |
|---|---|---|
| `FEISHU_APP_ID` | 是 | 飞书开放平台 → 凭证与基础信息 |
| `FEISHU_APP_SECRET` | 是 | 同上；切勿提交到 Git |
| `FEISHU_DOMAIN` | 否 | `feishu`（默认）或 `lark` |
| `OPENAI_API_KEY` / `DASHSCOPE_API_KEY` / … | 至少一个 | 需与 `roles.yaml` 里的 `${…}` 引用一致 |

编辑 `config/roles.yaml`：把占位符 `bascnXXXX` / `tblXXXX` 换成真实的 `app_token` / `table_id`，并保证 `field` 与表格列名完全一致。

## 2. 运行

**Docker（常驻推荐）：**

```bash
docker compose up -d --build
docker compose logs -f bot
```

**本地开发：**

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
set -a; . ./.env; set +a
python -m bitable_bot
```

看到长连接建立成功后，保持进程运行，再去完成开放平台配置（见 [飞书配置](feishu-setup.md)）。

## 3. 第一次 @

在已添加机器人的群里：

```text
@你的机器人 订单导出太慢了，P1，周五前要
```

或显式指定角色：

```text
@你的机器人 /bug 登录页偶发卡顿，P2
```

`p2p_enabled: true` 时，私聊无需 @。

常用命令：`/help`、`/取消`（放弃进行中的追问）。

## 下一步

- [飞书配置](feishu-setup.md) — 权限、发版、把应用加到多维表格
- [配置说明](configuration.md) — 意图、确认、查询、提示词
- [架构](architecture.md) — 模块如何协作
