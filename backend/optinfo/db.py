"""Small durable SQLite store. Edition snapshots preserve their original ranking/content."""
from contextlib import contextmanager
from datetime import date as Date, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from .config import SHANGHAI, now_shanghai


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _day(value: str) -> str:
    return Date.fromisoformat(value).isoformat()


def _item(value: dict, day: str) -> dict:
    result = dict(id="", kind="paper", section="research", title="", summary="", published_at="",
                  briefing_date=day, source_url="", source_name="", doi="", journal="",
                  company_ids=[], tags=[], importance=0, content={}, figures=[], sources=[],
                  evidence_level="", bookmarked=False)
    result.update(value)
    if not result["id"] or not result["title"]:
        raise ValueError("Items require stable id and title")
    result["briefing_date"] = day
    result["bookmarked"] = False
    content = dict(background="", approach="", results="", assessment="")
    content.update(result.get("content") or {})
    result["content"] = content
    return result


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS editions (
                    date TEXT PRIMARY KEY, generated_at TEXT NOT NULL, status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY, data_json TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS edition_items (
                    date TEXT NOT NULL REFERENCES editions(date), item_id TEXT NOT NULL REFERENCES items(id),
                    position INTEGER NOT NULL, data_json TEXT NOT NULL, PRIMARY KEY(date,item_id));
                CREATE INDEX IF NOT EXISTS edition_item_lookup ON edition_items(item_id,date);
                CREATE TABLE IF NOT EXISTS item_bookmarks (
                    item_id TEXT PRIMARY KEY REFERENCES items(id), created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS companies (id TEXT PRIMARY KEY, data_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS company_bookmarks (
                    company_id TEXT PRIMARY KEY REFERENCES companies(id), created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS preferences (
                    effective_date TEXT PRIMARY KEY, topic_ids_json TEXT NOT NULL, saved_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY, item_id TEXT UNIQUE NOT NULL REFERENCES items(id),
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chat_cleanup (session_id TEXT PRIMARY KEY, requested_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chat_leases (
                    session_id TEXT PRIMARY KEY REFERENCES chat_sessions(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL);
            """)
        return self

    def edition_exists(self, day: str) -> bool:
        with self.connection() as conn:
            row = conn.execute("SELECT status FROM editions WHERE date=?", (_day(day),)).fetchone()
        return bool(row and row["status"] == "ready")

    def save_edition(self, day: str, items: list[dict], generated_at: str | None = None,
                     status: str = "ready", metadata: dict | None = None) -> bool:
        day = _day(day)
        generated_at = generated_at or now_shanghai().isoformat()
        prepared = [_item(item, day) for item in items]
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM editions WHERE date=?", (day,)).fetchone()
            if row and row["status"] == "ready":
                return False
            conn.execute("INSERT INTO editions VALUES(?,?,?,?) ON CONFLICT(date) DO UPDATE SET generated_at=excluded.generated_at,status=excluded.status,metadata_json=excluded.metadata_json",
                         (day, generated_at, status, _json(metadata or {})))
            # A retry can replace incomplete summaries/ranking, but retains separately
            # collected major events. Completed daily editions above are immutable.
            preserved = []
            incoming_ids = {item["id"] for item in prepared}
            if row:
                for old in conn.execute("SELECT data_json FROM edition_items WHERE date=?", (day,)):
                    value = json.loads(old["data_json"])
                    if value.get("kind") == "major" and value["id"] not in incoming_ids:
                        preserved.append(value)
                conn.execute("DELETE FROM edition_items WHERE date=?", (day,))
            prepared.extend(preserved)
            for position, item in enumerate(prepared):
                self._persist_item(conn, item, generated_at)
                conn.execute("INSERT INTO edition_items VALUES(?,?,?,?) ON CONFLICT(date,item_id) DO NOTHING",
                             (day, item["id"], position, _json(item)))
        return True

    @staticmethod
    def _persist_item(conn, item, at):
        conn.execute("INSERT INTO items VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json,updated_at=excluded.updated_at",
                     (item["id"], _json(item), at))

    def upsert_items(self, items: list[dict], briefing_date: str | None = None):
        at = now_shanghai().isoformat()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for value in items:
                day = _day(briefing_date or value.get("briefing_date") or now_shanghai().date().isoformat())
                item = _item(value, day)
                conn.execute("INSERT OR IGNORE INTO editions VALUES(?,?,?,?)", (day, at, "events_only", "{}"))
                self._persist_item(conn, item, at)
                conn.execute("INSERT INTO edition_items VALUES(?,?,?,?) ON CONFLICT(date,item_id) DO NOTHING",
                             (day, item["id"], 1000000, _json(item)))

    def upsert_companies(self, companies: list[dict], overwrite: bool = True):
        with self.connection() as conn:
            for company in companies:
                if not company.get("id") or not company.get("name"):
                    raise ValueError("Companies require id and name")
                value = dict(name_en="", logo_url="", website="", description="", businesses=[],
                             listed=None, listing="", compensation={"masters": None, "phd": None,
                             "currency": "CNY", "basis": "暂无可靠公开数据", "as_of": "", "sources": []}, sources=[], bookmarked=False)
                value.update(company)
                value["bookmarked"] = False
                conflict = "DO UPDATE SET data_json=excluded.data_json" if overwrite else "DO NOTHING"
                conn.execute("INSERT INTO companies VALUES(?,?) ON CONFLICT(id) " + conflict, (value["id"], _json(value)))

    def available_dates(self) -> list[str]:
        with self.connection() as conn:
            return [row[0] for row in conn.execute("SELECT date FROM editions ORDER BY date DESC")]

    def latest_date(self) -> str | None:
        dates = self.available_dates()
        return dates[0] if dates else None

    def preference_state(self, today: str, default_ids: list[str] | None = None) -> dict:
        _day(today)
        active = self.preferences_for_date(today, default_ids)
        with self.connection() as conn:
            pending = conn.execute("SELECT effective_date,topic_ids_json FROM preferences WHERE effective_date>? ORDER BY effective_date DESC LIMIT 1", (today,)).fetchone()
        return {"active_topics": active, "pending_topics": json.loads(pending["topic_ids_json"]) if pending else [],
                "effective_date": pending["effective_date"] if pending else None}

    def preferences_for_date(self, day: str, default_ids: list[str] | None = None) -> list[str]:
        with self.connection() as conn:
            row = conn.execute("SELECT topic_ids_json FROM preferences WHERE effective_date<=? ORDER BY effective_date DESC LIMIT 1", (_day(day),)).fetchone()
        return json.loads(row[0]) if row else list(default_ids or [])

    topics_for_date = preferences_for_date

    def save_preferences(self, topic_ids: list[str], now: datetime, default_ids: list[str] | None = None):
        now = now.astimezone(SHANGHAI)
        effective = (now.date() + timedelta(days=1)).isoformat()
        with self.connection() as conn:
            conn.execute("INSERT INTO preferences VALUES(?,?,?) ON CONFLICT(effective_date) DO UPDATE SET topic_ids_json=excluded.topic_ids_json,saved_at=excluded.saved_at",
                         (effective, _json(list(dict.fromkeys(topic_ids))), now.isoformat()))
        return self.preference_state(now.date().isoformat(), default_ids)

    def get_item(self, item_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT i.data_json, b.item_id AS bookmark FROM items i LEFT JOIN item_bookmarks b ON b.item_id=i.id WHERE i.id=?", (item_id,)).fetchone()
        if not row:
            return None
        result = json.loads(row["data_json"])
        result["bookmarked"] = row["bookmark"] is not None
        return result

    def query_items(self, day: str | None = None, q: str = "", section: str = "", journal: str = "", company: str = "", bookmarked: bool = False) -> list[dict]:
        params = []
        where = []
        if day:
            where.append("ei.date=?")
            params.append(_day(day))
        if bookmarked:
            where.append("b.item_id IS NOT NULL")
        sql = "SELECT ei.data_json,ei.date,b.item_id AS bookmark FROM edition_items ei LEFT JOIN item_bookmarks b ON b.item_id=ei.item_id"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ei.date DESC,ei.position ASC,ei.item_id ASC"
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        result, seen = [], set()
        for row in rows:
            item = json.loads(row["data_json"])
            if item["id"] in seen:
                continue
            if section and item.get("section") != section:
                continue
            if journal and item.get("journal", "").casefold() != journal.casefold():
                continue
            if company and company not in item.get("company_ids", []):
                continue
            if q and q.casefold() not in " ".join([item.get("title", ""), item.get("summary", ""), item.get("journal", ""), *item.get("tags", []), *item.get("content", {}).values()]).casefold():
                continue
            seen.add(item["id"])
            item["briefing_date"] = row["date"]
            item["bookmarked"] = row["bookmark"] is not None
            result.append(item)
        return result

    def briefing(self, day: str, historical: bool = False, **filters) -> dict:
        with self.connection() as conn:
            edition = conn.execute("SELECT generated_at,status FROM editions WHERE date=?", (_day(day),)).fetchone()
        return {"date": day, "generated_at": edition["generated_at"] if edition else None,
                "status": "history" if historical else edition["status"] if edition else "not_generated",
                "items": self.query_items(None if historical else day, **filters), "available_dates": self.available_dates()}

    def set_item_bookmark(self, item_id: str, bookmarked: bool):
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
                return False
            if bookmarked:
                conn.execute("INSERT OR IGNORE INTO item_bookmarks VALUES(?,?)", (item_id, now_shanghai().isoformat()))
            else:
                conn.execute("DELETE FROM item_bookmarks WHERE item_id=?", (item_id,))
        return True

    def list_companies(self, q: str = "", bookmarked: bool = False) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute("SELECT c.data_json,b.company_id AS bookmark FROM companies c LEFT JOIN company_bookmarks b ON b.company_id=c.id ORDER BY c.id").fetchall()
        result = []
        for row in rows:
            company = json.loads(row["data_json"])
            company["bookmarked"] = row["bookmark"] is not None
            if bookmarked and not company["bookmarked"]:
                continue
            if q and q.casefold() not in _json(company).casefold():
                continue
            result.append(company)
        return result

    def get_company(self, company_id: str):
        return next((item for item in self.list_companies() if item["id"] == company_id), None)

    def set_company_bookmark(self, company_id: str, bookmarked: bool):
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM companies WHERE id=?", (company_id,)).fetchone():
                return False
            if bookmarked:
                conn.execute("INSERT OR IGNORE INTO company_bookmarks VALUES(?,?)", (company_id, now_shanghai().isoformat()))
            else:
                conn.execute("DELETE FROM company_bookmarks WHERE company_id=?", (company_id,))
        return True

    def expire_chats(self, now: datetime, exclude_ids: set[str] | None = None) -> int:
        with self.connection() as conn:
            at = now.astimezone(SHANGHAI).isoformat()
            rows = conn.execute("SELECT s.id FROM chat_sessions s LEFT JOIN chat_leases l ON l.session_id=s.id WHERE s.expires_at<=? AND (l.expires_at IS NULL OR l.expires_at<=?)", (at, at)).fetchall()
            rows = [row for row in rows if row["id"] not in (exclude_ids or set())]
            for row in rows:
                conn.execute("INSERT OR IGNORE INTO chat_cleanup VALUES(?,?)", (row["id"], now.isoformat()))
                conn.execute("DELETE FROM chat_sessions WHERE id=?", (row["id"],))
        return len(rows)

    def pending_chat_cleanup(self) -> list[str]:
        with self.connection() as conn:
            return [row[0] for row in conn.execute("SELECT session_id FROM chat_cleanup")]

    def queue_chat_cleanup(self, session_id: str, now: datetime):
        with self.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO chat_cleanup VALUES(?,?)", (session_id, now.isoformat()))

    def complete_chat_cleanup(self, session_id: str):
        with self.connection() as conn:
            conn.execute("DELETE FROM chat_cleanup WHERE session_id=?", (session_id,))

    def get_chat(self, item_id: str) -> dict:
        with self.connection() as conn:
            session = conn.execute("SELECT * FROM chat_sessions WHERE item_id=?", (item_id,)).fetchone()
            if not session:
                return {"session_id": None, "expires_at": None, "messages": [], "status": "empty"}
            messages = [dict(row) for row in conn.execute("SELECT role,content,created_at FROM chat_messages WHERE session_id=? ORDER BY id", (session["id"],))]
        return {"session_id": session["id"], "expires_at": session["expires_at"], "messages": messages, "status": "ready"}

    def ensure_chat_session(self, item_id: str, session_id: str, now: datetime):
        """Register before the SDK runs so crash leftovers have a durable expiry."""
        now = now.astimezone(SHANGHAI)
        with self.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO chat_sessions VALUES(?,?,?,?)", (session_id, item_id, now.isoformat(), (now + timedelta(days=7)).isoformat()))
        return self.get_chat(item_id)

    def acquire_chat_lease(self, session_id: str, now: datetime):
        # Shared with an optional maintenance CLI. If the process crashes, its
        # lease expires automatically instead of blocking seven-day deletion.
        with self.connection() as conn:
            conn.execute("INSERT INTO chat_leases VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET expires_at=excluded.expires_at",
                         (session_id, (now.astimezone(SHANGHAI) + timedelta(minutes=15)).isoformat()))

    def release_chat_lease(self, session_id: str):
        with self.connection() as conn:
            conn.execute("DELETE FROM chat_leases WHERE session_id=?", (session_id,))

    def delete_chat_session(self, session_id: str, now: datetime):
        with self.connection() as conn:
            conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
            conn.execute("INSERT OR IGNORE INTO chat_cleanup VALUES(?,?)", (session_id, now.isoformat()))

    def append_chat_exchange(self, item_id: str, session_id: str, question: str, answer: str, now: datetime):
        now = now.astimezone(SHANGHAI)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO chat_sessions VALUES(?,?,?,?)", (session_id, item_id, now.isoformat(), (now + timedelta(days=7)).isoformat()))
            actual = conn.execute("SELECT id FROM chat_sessions WHERE item_id=?", (item_id,)).fetchone()["id"]
            if actual != session_id:
                raise ValueError("Concurrent chat session changed")
            conn.executemany("INSERT INTO chat_messages(session_id,role,content,created_at) VALUES(?,?,?,?)", [(session_id, "user", question, now.isoformat()), (session_id, "assistant", answer, now.isoformat())])
        return self.get_chat(item_id)
