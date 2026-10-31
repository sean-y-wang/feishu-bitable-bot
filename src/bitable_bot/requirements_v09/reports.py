"""Durable local confirmation drafts and a scheduler/outbox interface; no draft Bitable."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

from ..config import ConfigError, load_config
from ..writer import FeishuAPIError
from .user_api import UserAPI, cell_text


class Member(BaseModel):
    open_id: str
    name: str


class ProgressEntry(BaseModel):
    author: str
    day: date
    task: str = "未关联任务"
    module: str = "未映射模块"
    summary: str
    risk: str = ""
    review: bool = False
    agent: str = "其他"


class ReportInput(BaseModel):
    members: list[Member]
    progress: list[ProgressEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_members(self):
        ids = [m.open_id for m in self.members]
        if any(not oid for oid in ids) or len(ids) != len(set(ids)):
            raise ValueError("汇报成员 open_id 必须非空且唯一")
        return self


def entries_from_records(rows, table) -> list[ProgressEntry]:
    cols = {f.key: f.field for f in table.fields}
    entries = []
    for row in rows:
        fields = row.get("fields") or {}

        def value(key, fields=fields):
            return cell_text(fields.get(cols.get(key, "")))

        for person in fields.get(cols["author"]) or []:
            entries.append(
                ProgressEntry(
                    author=person.get("id", person.get("open_id", "")),
                    day=value("day"),
                    task=value("task") or "未关联任务",
                    module=value("module") or "未映射模块",
                    summary=value("summary"),
                    risk=value("risk"),
                    review=bool(fields.get(cols["review"])),
                    agent=value("agent"),
                )
            )
    return entries


def due_kinds(now: datetime, timezone="Asia/Shanghai") -> list[str]:
    if now.tzinfo is None:
        raise ValueError("调度时间必须带时区")
    local = now.astimezone(ZoneInfo(timezone))
    due = []
    if local.weekday() < 5 and local.time() >= time(9):
        due.append("daily")
    if local.weekday() == 4 and local.time() >= time(18):
        due.append("weekly")
    return due


def period(kind: str, today: date) -> tuple[date, date]:
    if kind == "weekly":
        return today - timedelta(days=today.weekday()), today
    if kind != "daily":
        raise ValueError("unknown report kind")
    end = today - timedelta(days=1)
    start = end
    while start.weekday() >= 5:
        start -= timedelta(days=1)
    return start, end


def personal_body(member: Member, rows: list[ProgressEntry], start: date, end: date) -> str:
    lines = [f"{member.name}（{start} 至 {end}）"]
    if not rows:
        lines.append("暂无进展记录，请补充或确认本期无进展。")
    for row in sorted(rows, key=lambda r: (r.day, r.module, r.task, r.agent)):
        suffix = "；待复核" if row.review else ""
        risk = f"；风险：{row.risk}" if row.risk else ""
        lines.append(f"- {row.day}｜{row.module}｜{row.task}｜{row.summary}（{row.agent}）{suffix}{risk}")
    return "\n".join(lines)


def confirmation_card(draft: dict) -> dict:
    name = "日报" if draft["kind"] == "daily" else "周报"
    note = "上期未确认，已重新拟定。\n" if draft["redrafted"] else ""
    value = {"kind": "report_confirm", "report_id": draft["id"], "revision": draft["revision"]}
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": f"请确认个人{name}"}},
        "elements": [
            {"tag": "div", "text": {"tag": "plain_text", "content": note + draft["body"]}},
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "只有本人可确认；需修改时先更新进展，再重新生成。"}],
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "type": "primary",
                        "text": {"tag": "plain_text", "content": "确认"},
                        "value": value,
                    }
                ],
            },
        ],
    }


class ReportStore:
    def __init__(self, path=":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute("""CREATE TABLE IF NOT EXISTS report_drafts (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL, day TEXT NOT NULL,
            body TEXT NOT NULL, revision TEXT NOT NULL, status TEXT NOT NULL, redrafted INTEGER NOT NULL
        )""")

    def draft(self, owner, kind, day, body):
        rid = hashlib.sha256(f"{owner}:{kind}:{day}".encode()).hexdigest()[:24]
        revision = hashlib.sha256(body.encode()).hexdigest()[:16]
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                old = self.db.execute("SELECT * FROM report_drafts WHERE id=?", (rid,)).fetchone()
                if old and (old["status"] == "confirmed" or old["revision"] == revision):
                    result = dict(old)
                else:
                    previous = self.db.execute(
                        "SELECT id FROM report_drafts WHERE owner=? AND kind=? AND day<? AND status='pending'",
                        (owner, kind, day),
                    ).fetchall()
                    self.db.execute(
                        "UPDATE report_drafts SET status='superseded' WHERE owner=? AND kind=? AND day<? AND status='pending'",
                        (owner, kind, day),
                    )
                    redrafted = bool(previous) or bool(old and old["redrafted"])
                    self.db.execute(
                        "INSERT OR REPLACE INTO report_drafts VALUES (?,?,?,?,?,?,?,?)",
                        (rid, owner, kind, day, body, revision, "pending", int(redrafted)),
                    )
                    result = dict(self.db.execute("SELECT * FROM report_drafts WHERE id=?", (rid,)).fetchone())
                self.db.execute("COMMIT")
                return result
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def confirm(self, report_id: str, revision: str, operator: str, today: date) -> bool:
        if not operator:
            return False
        with self.lock:
            # Daily confirmations expire the next calendar day, even before next scheduler tick.
            cur = self.db.execute(
                """UPDATE report_drafts SET status='confirmed'
                WHERE id=? AND owner=? AND revision=? AND status='pending'
                AND (kind!='daily' OR day=?)""",
                (report_id, operator, revision, today.isoformat()),
            )
            return cur.rowcount == 1

    def confirmed_owners(self, kind: str, day: date) -> set[str]:
        with self.lock:
            rows = self.db.execute(
                "SELECT owner FROM report_drafts WHERE kind=? AND day=? AND status='confirmed'", (kind, day.isoformat())
            ).fetchall()
        return {r["owner"] for r in rows}

    def close(self):
        self.db.close()


def manager_report(members: list[Member], entries: list[ProgressEntry], today: date, confirmed: set[str]) -> dict:
    start, end = period("weekly", today)
    rows = [p for p in entries if start <= p.day <= end]
    missing = [m.name for m in members if m.open_id not in confirmed]
    names = {m.open_id: m.name for m in members}
    risks = [f"{names.get(p.author, p.author)}｜{p.task}：{p.risk or '进展待复核'}" for p in rows if p.risk or p.review]
    # Preserve entries by people outside the roster as well; no top-N truncation.
    all_members = [
        *members,
        *(Member(open_id=oid, name=oid) for oid in sorted({p.author for p in rows} - names.keys())),
    ]
    body = "\n\n".join(personal_body(m, [p for p in rows if p.author == m.open_id], start, end) for m in all_members)
    return {
        "period": f"{start}/{end}",
        "missing": missing,
        "risks": risks,
        "body": f"跨人周报（{start} 至 {end}）\n缺报/未确认：{'、'.join(missing) or '无'}\n"
        + "风险：\n"
        + ("\n".join(risks) or "无")
        + "\n\n"
        + body,
    }


def scheduled_payloads(data: ReportInput, now: datetime, store: ReportStore, timezone="Asia/Shanghai") -> dict:
    today = now.astimezone(ZoneInfo(timezone)).date()
    kinds = due_kinds(now, timezone)
    out = []
    for kind in kinds:
        start, end = period(kind, today)
        for member in data.members:
            rows = [r for r in data.progress if r.author == member.open_id and start <= r.day <= end]
            draft = store.draft(member.open_id, kind, today.isoformat(), personal_body(member, rows, start, end))
            if draft["status"] == "pending":
                out.append(
                    {
                        "id": draft["id"],
                        "revision": draft["revision"],
                        "recipient": member.open_id,
                        "kind": kind,
                        "delivery": "scheduled",
                        "allow_interrupt": kind == "daily",
                        "card": confirmation_card(draft),
                    }
                )
    manager = (
        manager_report(data.members, data.progress, today, store.confirmed_owners("weekly", today))
        if "weekly" in kinds
        else None
    )
    return {"confirmations": out, "manager": manager}


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成日报/周报确认及管理者周报 outbox（不自动发送）")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="离线 JSON: members + progress，使用稳定 open_id")
    source.add_argument("--config", type=Path, help="读取真实进展表；需本人 OAuth 及 --members")
    parser.add_argument("--members", type=Path, help="成员清单 JSON: [{open_id, name}]")
    parser.add_argument("--now", help="带时区的 ISO 时间；默认当前时间")
    parser.add_argument("--timezone")
    parser.add_argument("--db", type=Path, default=Path("data/reports.db"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        timezone = args.timezone or "Asia/Shanghai"
        if args.input:
            data = ReportInput.model_validate_json(args.input.read_text())
        else:
            if not args.members:
                raise ValueError("读取真实进展表需 --members 成员名单")
            cfg = load_config(args.config)
            if not cfg.tables:
                raise ValueError("汇报需要 0.9 四表配置")
            timezone = args.timezone or cfg.timezone
            api = UserAPI(os.environ.get("FEISHU_USER_ACCESS_TOKEN", ""))
            api.identity()
            table = cfg.tables.progress
            entries = entries_from_records(api.list_all_records(table.app_token, table.table_id), table)
            data = ReportInput(members=json.loads(args.members.read_text()), progress=entries)
        now = datetime.fromisoformat(args.now) if args.now else datetime.now(ZoneInfo(timezone))
        store = ReportStore() if args.dry_run else ReportStore(args.db)
        try:
            result = scheduled_payloads(data, now, store, timezone)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            store.close()
        return 0
    except (ValueError, OSError, ConfigError, FeishuAPIError) as e:
        print(f"requirements-reports: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
