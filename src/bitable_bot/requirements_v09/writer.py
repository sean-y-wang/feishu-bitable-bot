"""Table-scoped idempotency and offline payload generation."""

from __future__ import annotations

from ..writer.bitable import BitableWriter, WriteError, WriteResult, friendly, record_url
from ..writer.feishu_api import FeishuAPIError
from .models import TableName, Tables
from .user_api import cell_text


class MultiTableWriter:
    def __init__(self, writer: BitableWriter, tables: Tables):
        self.writer = writer
        self.tables = tables

    def key(self, name: TableName, source: str) -> str:
        table = self.tables.get(name)
        return f"v09:{table.app_token}:{table.table_id}:{source}"

    def payload(self, name: TableName, values: dict) -> dict:
        table = self.tables.get(name)
        unknown = values.keys() - {f.key for f in table.fields}
        if unknown:
            raise ValueError(f"undeclared fields: {sorted(unknown)}")
        return BitableWriter.map_fields(table.role(name), values)

    def write(self, name: TableName, values: dict, source: str, *, dry_run=False):
        payload = self.payload(name, values)
        table = self.tables.get(name)
        if dry_run:
            return {"table_id": table.table_id, "fields": payload, "key": self.key(name, source)}
        return self.writer.write(table.role(name), values, self.key(name, source))

    def upsert_progress(self, values: dict) -> WriteResult:
        """Remote key lookup survives local DB loss; caller serializes personal sync jobs."""
        table = self.tables.progress
        role = table.role("progress")
        key = values["sync_key"]
        payload = self.payload("progress", values)
        key_field = next(f.field for f in table.fields if f.key == "sync_key")
        try:
            rows = self.writer.api.list_all_records(table.app_token, table.table_id)
            hits = [r for r in rows if cell_text(r["fields"].get(key_field)) == key]
            if len(hits) > 1:
                raise WriteError("发现重复同步键，请管理员合并重复进展后重试。")
            if hits:
                rid = hits[0]["record_id"]
                self.writer.api.update_record(table.app_token, table.table_id, rid, payload)
                return WriteResult(rid, record_url(role, rid, self.writer.base_url), duplicated=True)
            return self.write("progress", values, key)
        except FeishuAPIError as e:
            raise WriteError(friendly(e), e.code) from e
