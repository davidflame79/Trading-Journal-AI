"""A trade's written plan and comments as short text lines, for the AI coaching prompts."""

MAX_NOTE_CHARS = 300
MAX_NOTES_PER_TRADE = 8

PLAN_LABELS = (
    ("plan_date", "written"), ("setup_type", "setup"), ("idea_source", "source"), ("timeframe", "timeframe"),
    ("higher_tf_trend", "higher-TF trend"), ("key_level", "key level"), ("volume_momentum", "volume/momentum"),
    ("entry_trigger", "trigger"), ("invalidation", "invalidation"), ("entry_price", "entry"), ("stop_price", "stop"),
    ("target_price", "target"), ("shares", "shares"),
)


def _short(text) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_NOTE_CHARS else text[:MAX_NOTE_CHARS] + "…"


def plan_line(plan) -> str:
    return " | ".join(f"{label}: {_short(plan[key])}" for key, label in PLAN_LABELS if plan[key] not in (None, ""))


def trade_note_lines(conn, account_id: int, trade_group: str) -> list[str]:
    """The plan, then the most recent comments in date order. Empty when nothing was written."""
    lines = []
    plan = conn.execute("SELECT * FROM trade_plans WHERE account_id=? AND trade_group=?",
                        (account_id, trade_group)).fetchone()
    if plan:
        lines.append(f"Plan: {plan_line(plan)}")
    comments = conn.execute(
        "SELECT note_date, body FROM trade_comments WHERE account_id=? AND trade_group=? ORDER BY note_date DESC, id DESC LIMIT ?",
        (account_id, trade_group, MAX_NOTES_PER_TRADE),
    ).fetchall()
    lines += [f"Comment {c['note_date']}: {_short(c['body'])}" for c in reversed(comments)]
    return lines
