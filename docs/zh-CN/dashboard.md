# 运行看板

看板和机器人**同进程部署**：同一个 Python 服务里多开一个内网 HTTP 端口（标准库 `http.server`，无额外依赖），与飞书长连接共享配置和运行状态。

配置分两层：

| 层 | 放哪 | 谁改 | 怎么生效 |
|---|---|---|---|
| 业务配置（prompt、引导文案、群绑定、字段说明） | 飞书「机器人配置」多维表格（`remote_config`） | 产品 / 运营 | 看板「重新加载业务配置」或 `refresh_seconds` 自动刷新 |
| 系统配置（LLM、降级、超时、阈值、密钥） | 本地 `roles.yaml` + `.env`（均 gitignore） | 运维 | 看板保存后热生效（少数项需重启，见下） |

## 启用

```bash
# .env
DASHBOARD_TOKEN=<python -c "import secrets;print(secrets.token_urlsafe(32))">
DASHBOARD_HOST=127.0.0.1   # 默认仅本机；要在办公室内网访问填本机内网 IP
DASHBOARD_PORT=8787
```

- 未设置 `DASHBOARD_TOKEN` → 看板不启动（机器人照常运行，自动刷新仍生效）。口令至少 16 位。
- 打开 `http://<host>:8787/`，输入口令（只存在当前标签页）。API 使用 `Authorization: Bearer <token>`。
- **不要暴露到公网**：看板能改密钥，等同机器人全部权限。

## 看板内容

1. **运行概况**：机器人/场景、角色数与列表、长连接状态、启动时间与运行时长、今日记录/咨询/引导次数、LLM 主模型与降级链、今日降级次数、最近 30 条报错。
2. **业务配置**：来源（飞书表格 / 本地 YAML / 本地回退）、最近一次加载时间、触发方式、结果、行数、错误。
3. **系统配置**：LLM 主模型、降级顺序、各 provider 的 model / 同厂商降级 / 超时（base_url 只读展示）；会话超时、意图置信度阈值、上下文条数、自动刷新间隔。保存时先校验，写回 `BOT_CONFIG` 指向的 YAML（旧文件备份为 `.bak`，YAML 注释不保留），再热重载。
4. **密钥**：`FEISHU_APP_SECRET` 与 provider `api_key: ${VAR}` 引用的变量，**只显示打码**（如 `sk-****a1b2`），可填新值覆盖，写入 `.env`（`BOT_ENV_FILE`）。接口永不返回明文，审计日志只记变量名。
5. **操作**：「重新加载业务配置」——重读 YAML + 拉飞书配置表并热替换；**失败保留旧配置**并在页面显示错误。

所有修改写审计日志 `data/dashboard_audit.log`（与 `BOT_DB_PATH` 同目录，JSON Lines，不含密钥值）。

## 热生效 vs 需重启

| 热生效 | 需重启进程 |
|---|---|
| 飞书表格里的业务配置 | 飞书 App ID / App Secret / `FEISHU_DOMAIN`（长连接需重连） |
| LLM 主模型、降级顺序、模型名、超时 | `DASHBOARD_*` |
| LLM 密钥（`*_API_KEY`） | `BOT_CONFIG` / `BOT_SCENARIO` / `BOT_DB_PATH` / `BOT_WORKERS` / `LOG_LEVEL` |
| 会话超时、置信度阈值、上下文条数、刷新间隔 | 角色结构（目标表、字段定义）建议重启 |
|  | LLM provider 的 `base_url`（看板只读，提交修改会被拒绝；改 `roles.yaml` 后重启） |

飞书长连接断线时 SDK 会自动重连，看板实时显示 `connected / reconnecting / disconnected`。

## 部署（Docker / Mac mini）

`docker-compose.yml` 已映射 `127.0.0.1:8787:8787`，容器内 `DASHBOARD_HOST=0.0.0.0`。办公室内网访问时把映射改成 `"<Mac mini 内网 IP>:8787:8787"`。为了让看板能保存配置，compose 以可写方式挂载 `./config` 和 `./.env`。

直接跑 Python（`python -m bitable_bot`）时，记得先加载 `.env`（`set -a; . ./.env; set +a`）。Mac mini 非 Docker 部署要在内网访问时，设置 `DASHBOARD_HOST=<Mac mini 内网 IP>`（不建议 `0.0.0.0`），并确保 `DASHBOARD_TOKEN` 足够长；`BOT_DB_PATH` 需指向进程可写的路径（如仓库内 `data/bot.db`）。

## 注意事项

- 看板保存系统配置会**重写** `roles.yaml`：YAML 注释和排版不保留，旧文件备份为 `roles.yaml.bak`。需要注释请以 `config/roles.example.yaml` 为参考。
- LLM 降级链（如 spark → qwen → deepseek）只有在对应密钥（`DASHSCOPE_API_KEY`、`DEEPSEEK_API_KEY` 等）填写后才会真正生效；可在看板「密钥」区填写，热生效。
- 密钥在看板和 API 中只显示打码值，不返回明文。
