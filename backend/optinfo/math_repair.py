"""Bounded formatting-only repairs; no fetching, figure selection or rewriting."""
from .harness import canonicalize_math, repair_math_fields, safe_error_details, validate_math_format


def _texts(item: dict) -> list[str]:
    values = [item.get("title", ""), item.get("summary", ""), *item.get("content", {}).values()]
    for figure in item.get("figures", []):
        values.extend(figure.get(key, "") for key in ("caption", "selection_reason"))
    return [value for value in values if isinstance(value, str) and value]


def repair_math_only(db, *, item_ids: set[str] | None = None, limit: int = 20) -> dict:
    ids = {item["id"] for item in db.query_items() if item.get("section") == "research"}
    if item_ids is not None and item_ids - ids:
        raise ValueError("Unknown research item requested")
    result = []
    for item_id in sorted(ids if item_ids is None else item_ids)[:max(1, min(limit, 50))]:
        versions = db.item_versions(item_id)
        texts = list(dict.fromkeys(text for version in versions for text in _texts(version)))
        needed = False
        for text in texts:
            try:
                needed = needed or canonicalize_math(text) != text
                validate_math_format(text)
            except ValueError:
                needed = True
        if not needed:
            if any(version.get("math_status") == "pending" for version in versions):
                db.repair_item_math(item_id, {}, status="complete")
            result.append({"item": item_id, "status": "already_formatted"})
            continue
        # Existing images/content remain readable; the released client recognizes
        # any non-complete analysis state and displays its pending warning.
        db.repair_item_math(item_id, {}, status="pending")
        try:
            fields = {f"text_{index}": text for index, text in enumerate(texts)}
            fixed = repair_math_fields(fields)
            replacements = {original: fixed[key] for key, original in fields.items() if fixed[key] != original}
            snapshots = db.repair_item_math(item_id, replacements, status="complete")
            result.append({"item": item_id, "status": "formatted", "fields": len(replacements), "snapshots": snapshots})
        except Exception as exc:
            result.append({"item": item_id, "status": "math_pending", **safe_error_details(exc)})
    return {"status": "partial" if any(entry["status"] == "math_pending" for entry in result) else "ready", "items": result}
