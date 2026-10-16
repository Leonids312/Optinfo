"""Scheduled entry points. Daily editions commit atomically and are immutable."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date
import json
import os
from pathlib import Path
import sys

from .catalog import COMPANIES, DEFAULT_TOPIC_IDS
from .config import Settings, now_shanghai
from .db import Database
from .harness import cleanup_session, is_configured

@contextmanager
def generation_lock(path: Path):
    """OS-owned lock: released on crash; no stale PID-file deadlock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        stream.close()
        raise RuntimeError("Another Optinfo generation is already running") from None
    try:
        yield
    finally:
        stream.close()

def cleanup(db: Database, settings: Settings) -> int:
    count = db.expire_chats(now_shanghai())
    # Deferred cleanup also covers interruption between SQL deletion and removal.
    for session_id in db.pending_chat_cleanup():
        cleanup_session(settings.chat_dir / session_id)
        db.complete_chat_cleanup(session_id)
    return count

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Optinfo daily and major-event jobs")
    parser.add_argument("command", choices=["generate", "major-events", "cleanup", "check", "collect"])
    parser.add_argument("--date", default=now_shanghai().date().isoformat())
    args = parser.parse_args(argv)
    day = date.fromisoformat(args.date).isoformat()
    settings = Settings.from_env()
    db = Database(settings.db_path).init()
    db.upsert_companies(COMPANIES, overwrite=False)
    if args.command == "check":
        print(json.dumps({"harness_configured": is_configured(), "date": day, "edition_exists": db.edition_exists(day)}, ensure_ascii=False))
        return 0
    if args.command == "cleanup":
        print(json.dumps({"deleted_sessions": cleanup(db, settings)}))
        return 0
    if args.command == "collect":
        from .sources import collect
        items, diagnostics = collect(day)
        print(json.dumps({"count": len(items), "sources": diagnostics, "titles": [{"title": i["title"], "section": i["section"], "url": i["source_url"], "published_at": i["published_at"]} for i in items]}, ensure_ascii=False))
        return 0
    with generation_lock(settings.db_path.parent / "generation.lock"):
        if args.command == "generate" and db.edition_exists(day):
            print(json.dumps({"date": day, "status": "already_generated"}))
            return 0
        from .pipeline import generate_edition
        topic_ids = db.preferences_for_date(day, DEFAULT_TOPIC_IDS)
        known_ids = {item["id"] for item in db.query_items()} | db.reviewed_ids(day)
        result = generate_edition(day, topic_ids, major_only=args.command == "major-events", exclude_ids=known_ids)
        db.upsert_companies(result["companies"])
        if args.command == "major-events":
            db.upsert_items(result["items"], briefing_date=day)
        else:
            # Successful partial snapshots are reused without additional model calls.
            merged = {item["id"]: item for item in db.query_items(day=day)}
            merged.update({item["id"]: item for item in result["items"]})
            result["items"] = sorted(merged.values(), key=lambda item: (item["section"], -item["importance"]))
            db.save_edition(day, result["items"], generated_at=result["generated_at"], status=result["status"], metadata={"topics": topic_ids, "diagnostics": result["diagnostics"]})
        db.mark_reviewed(day, result.get("reviewed_ids", []))
        print(json.dumps({"date": day, "status": result["status"], "items": len(result["items"]), "source_failures": sum(d["status"] == "failed" for d in result["diagnostics"])}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Do not leak exception payloads from network or SDK transports.
        print(json.dumps({"status": "failed", "error": type(exc).__name__}), file=sys.stderr)
        sys.exit(1)
