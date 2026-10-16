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
from .harness import cleanup_session, is_configured, safe_error_details


class GenerationBusy(RuntimeError):
    pass


class PartialEditionStateError(RuntimeError):
    pass

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
        raise GenerationBusy("Another Optinfo generation is already running") from None
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
    parser.add_argument("command", choices=["generate", "major-events", "cleanup", "check", "collect", "repair-papers"])
    parser.add_argument("--date", default=now_shanghai().date().isoformat())
    parser.add_argument("--item-id", action="append", help="Existing paper id to repair; repeat for multiple papers")
    parser.add_argument("--limit", type=int, default=20, help="Maximum existing papers to repair (1–50)")
    parser.add_argument("--force", action="store_true", help="Recheck already upgraded papers")
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
        if args.command == "repair-papers":
            from .pipeline import repair_papers
            result = repair_papers(db, db.preferences_for_date(day, DEFAULT_TOPIC_IDS),
                                   item_ids=set(args.item_id) if args.item_id else None, limit=args.limit, force=args.force)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "generate" and db.edition_exists(day):
            print(json.dumps({"date": day, "status": "already_generated"}))
            return 0
        from .pipeline import generate_edition
        topic_ids = db.preferences_for_date(day, DEFAULT_TOPIC_IDS)
        known_ids = {item["id"] for item in db.query_items()} | db.reviewed_ids(day)
        generation_args = {"major_only": args.command == "major-events", "exclude_ids": known_ids}
        previous = db.get_edition(day) if args.command == "generate" else None
        if previous and previous["status"] == "partial":
            retry_ids = {entry["item"] for entry in previous["metadata"].get("diagnostics", [])
                         if entry.get("item") and entry.get("status") in {"model_failed", "retry_missing", "retry_deferred", "evidence_pending"}}
            if not retry_ids:
                # Do not silently replace an unknown older partial state with a
                # newly selected batch or falsely mark unresolved work complete.
                raise PartialEditionStateError("Partial edition has no recorded retry scope")
            generation_args["retry_ids"] = retry_ids
        result = generate_edition(day, topic_ids, **generation_args)
        db.upsert_companies(result["companies"])
        if args.command == "major-events":
            db.upsert_items(result["items"], briefing_date=day)
        else:
            # Successful partial snapshots are reused without additional model calls.
            merged = {item["id"]: item for item in db.query_items(day=day)}
            merged.update({item["id"]: item for item in result["items"]})
            result["items"] = sorted(merged.values(), key=lambda item: (item["section"], -item["importance"]))
            if previous:
                # Keep the audit of earlier successful/filtered items, replacing
                # only diagnostics for sources or items observed in this retry.
                updates = {(entry.get("item"), entry.get("source")) for entry in result["diagnostics"]}
                resolved_elsewhere = known_ids & generation_args.get("retry_ids", set())
                retained = [entry for entry in previous["metadata"].get("diagnostics", [])
                            if (entry.get("item"), entry.get("source")) not in updates and entry.get("item") not in resolved_elsewhere]
                result["diagnostics"] = retained + result["diagnostics"]
            db.save_edition(day, result["items"], generated_at=result["generated_at"], status=result["status"], metadata={"topics": topic_ids, "diagnostics": result["diagnostics"]})
        db.mark_reviewed(day, result.get("reviewed_ids", []))
        failures = [d for d in result["diagnostics"] if d["status"] in {"model_failed", "retry_missing", "retry_deferred", "evidence_pending"}]
        # Only fixed operational fields, never raw model text or exception payloads.
        safe_failures = [{key: entry[key] for key in ("item", "status", "reason", "error", "stage", "exception_type", "finish_reason", "attempts") if key in entry} for entry in failures]
        source_errors = [{key: entry[key] for key in ("source", "status", "error") if key in entry}
                         for entry in result["diagnostics"] if entry["status"] == "failed"]
        print(json.dumps({"date": day, "status": result["status"], "items": len(result["items"]), "source_failures": sum(d["status"] == "failed" for d in result["diagnostics"]),
                          "source_errors": source_errors, "model_failures": safe_failures,
                          "reviewed": len(result.get("reviewed_ids", []))}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Do not leak exception payloads from network or SDK transports.
        print(json.dumps({"status": "failed", **safe_error_details(exc)}), file=sys.stderr)
        sys.exit(1)
