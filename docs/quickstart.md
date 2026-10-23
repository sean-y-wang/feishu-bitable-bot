# 快速开始

```bash
cp .env.example .env                       # 填 FEISHU_APP_ID / FEISHU_APP_SECRET / LLM Key
cp config/roles.example.yaml config/roles.yaml   # 填 app_token / table_id / 字段
docker compose up -d --build
docker compose logs -f bot
```

本地开发：

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
set -a; . ./.env; set +a; python -m bitable_bot
```

在群里：`@机器人 /日报 今天完成了登录模块，明天联调，工时 6 小时`。
缺必填字段时机器人会在话题里追问，直接在话题里回复即可；`/取消` 放弃，`/help` 查看命令。
