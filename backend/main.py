import asyncio
import hashlib
import re
import time
from datetime import date, datetime

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent import enrich_items, parse_json_text, recommend_items, root_agent, run_agent_text

ACTION_URLS = {
    "event_details": "https://www.geeksforgeeks.org/events",
    "hackathon_details": "https://unstop.com/hackathons",
    "new_courses": "https://www.geeksforgeeks.org/courses",
}

CACHE_TTL_SECONDS = 600
item_cache: dict[str, tuple[float, list[dict]]] = {}

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ButtonAction(BaseModel):
    action: str
    actionLabel: str = ""


class Profile(BaseModel):
    skills: list[str] = []
    level: str = ""
    interests: list[str] = []
    mode_preference: str = ""
    location: str = ""


class RecommendRequest(BaseModel):
    profile: Profile = Profile()
    limit: int = Field(5, ge=1, le=20)
    action: str | None = None  # restrict to one source; None means all sources


async def run_agent(action: str, url: str) -> str:
    return await run_agent_text(root_agent, f"action: {action}, url: {url}")


def add_ids(action: str, data) -> None:
    """Give every item a stable id derived from its link (or title if it has none)."""
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return
    for item in items:
        if isinstance(item, dict):
            key = item.get("link") or item.get("title") or ""
            item["id"] = f"{action}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"


DIFFICULTIES = {"beginner", "intermediate", "advanced"}
URL_PATTERN = re.compile(r"https?://|www\.", re.IGNORECASE)


def clean_texts(values, max_items: int, max_len: int) -> list[str]:
    """Keep short, unique, link-free strings."""
    if not isinstance(values, list):
        return []
    cleaned = []
    for value in values:
        if not isinstance(value, str) or URL_PATTERN.search(value):
            continue
        value = value.strip()[:max_len]
        if value and value.lower() not in (c.lower() for c in cleaned):
            cleaned.append(value)
    return cleaned[:max_items]


def merge_enrichment(raw: list, items: list[dict]) -> None:
    """Merge the model's generated fields into the matching items, in place.

    Only summary, difficulty, prerequisites and tags are ever written; title, date
    and link always stay as scraped.
    """
    by_id = {item["id"]: item for item in items}
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        item = by_id.get(entry["id"])
        if item is None:
            continue

        summary = entry.get("summary")
        if isinstance(summary, str) and not URL_PATTERN.search(summary):
            item["summary"] = " ".join(summary.split())[:200]

        difficulty = str(entry.get("difficulty", "")).strip().lower()
        if difficulty in DIFFICULTIES:
            item["difficulty"] = difficulty

        item["prerequisites"] = clean_texts(entry.get("prerequisites"), 3, 60)

        tags = clean_texts(entry.get("tags"), 5, 30)
        if tags:
            item["tags"] = tags


async def enrich(items: list[dict]) -> None:
    """Add model-generated fields to items; a failure just leaves them blank."""
    items = [item for item in items if isinstance(item, dict)]
    for item in items:
        item.setdefault("summary", "")
        item.setdefault("difficulty", "")
        item.setdefault("prerequisites", [])
    try:
        merge_enrichment(await enrich_items(items), items)
    except Exception as e:
        print(f"enrich failed: {e!r}")


async def fetch_items(action: str) -> dict:
    """Scrape one source through the agent; returns the parsed result with item ids."""
    text = await run_agent(action, ACTION_URLS[action])
    data = parse_json_text(text)
    add_ids(action, data)
    items = data.get("items") if isinstance(data, dict) else None
    if isinstance(items, list) and items:
        await enrich(items)
        item_cache[action] = (time.time(), items)
    return data


async def cached_items(action: str) -> list[dict]:
    cached = item_cache.get(action)
    if cached and time.time() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    data = await fetch_items(action)
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


@app.post("/button-action")
async def button_action(body: ButtonAction):
    if body.action not in ACTION_URLS:
        raise HTTPException(status_code=400, detail="Unknown action")

    try:
        return await fetch_items(body.action)
    except Exception as e:
        print(f"button_action failed: {e!r}")
        return {"status": "error", "message": str(e)[:300], "items": []}


def clean_reason(reason) -> str:
    """Keep the reason to one short, link-free sentence."""
    if not isinstance(reason, str) or re.search(r"https?://|www\.", reason):
        return ""
    words = reason.split()
    return " ".join(words[:20])


def merge_recommendations(recs: list, items: list[dict], limit: int) -> list[dict]:
    """Keep valid, unique ids, sort by rank and merge in the original item data."""
    by_id = {item["id"]: item for item in items}
    seen = set()
    valid = []
    for position, rec in enumerate(recs):
        if not isinstance(rec, dict):
            continue
        rec_id = rec.get("id")
        if not isinstance(rec_id, str) or rec_id not in by_id or rec_id in seen:
            continue
        seen.add(rec_id)
        rank = rec.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool):
            rank = position + 1
        valid.append((rank, position, rec_id, clean_reason(rec.get("reason"))))

    valid.sort()
    return [
        {**by_id[rec_id], "rank": n, "reason": reason}
        for n, (_, _, rec_id, reason) in enumerate(valid[:limit], 1)
    ]


def parse_date(text) -> date | None:
    text = str(text or "")
    iso = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if iso:
        return datetime.strptime(iso.group(), "%Y-%m-%d").date()
    for pattern in (r"[A-Za-z]+ \d{1,2},? \d{4}", r"\d{1,2} [A-Za-z]+,? \d{4}"):
        match = re.search(pattern, text)
        if not match:
            continue
        candidate = match.group().replace(",", "")
        for fmt in ("%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                pass
    return None


def date_sort_key(item: dict):
    """Upcoming dates first (soonest first), then undated, then past."""
    parsed = parse_date(item.get("date"))
    if parsed is None:
        return (1, 0)
    if parsed < date.today():
        return (2, -parsed.toordinal())
    return (0, parsed.toordinal())


def item_text(item: dict) -> str:
    fields = [item.get(k, "") for k in ("title", "subtitle", "category", "description")]
    return " ".join(fields + list(item.get("tags") or [])).lower()


def fallback_items(profile: Profile, items: list[dict], limit: int) -> list[dict]:
    """No-model fallback: filter by mode preference, then nearest date first."""
    terms = [t.strip().lower() for t in profile.skills + profile.interests if t.strip()]
    if terms:
        items = [i for i in items if any(t in item_text(i) for t in terms)]

    pref = profile.mode_preference.strip().lower()
    pool = [i for i in items if pref in str(i.get("mode", "")).lower()] if pref else items
    pool = pool or items
    ranked = sorted(pool, key=date_sort_key)[:limit]
    return [{**item, "rank": n, "reason": ""} for n, item in enumerate(ranked, 1)]


@app.post("/recommend")
async def recommend(body: RecommendRequest):
    if body.action and body.action not in ACTION_URLS:
        raise HTTPException(status_code=400, detail="Unknown action")
    actions = [body.action] if body.action else list(ACTION_URLS)

    results = await asyncio.gather(
        *(cached_items(action) for action in actions), return_exceptions=True
    )
    items = []
    seen_ids = set()
    for result in results:
        if isinstance(result, Exception):
            print(f"recommend fetch failed: {result!r}")
            continue
        for item in result:
            if isinstance(item, dict) and item.get("id") not in seen_ids:
                seen_ids.add(item["id"])
                items.append(item)

    if not items:
        return {"recommendations": [], "fallback": False, "message": "No items available"}

    try:
        recs = await recommend_items(body.profile.model_dump(), items, body.limit)
        merged = merge_recommendations(recs, items, body.limit)
        if merged or not recs:
            # an empty list from the model means "nothing matches", not a failure
            return {"recommendations": merged, "fallback": False}
        print("recommend: model returned no valid ids")
    except Exception as e:
        print(f"recommend failed: {e!r}")

    return {
        "recommendations": fallback_items(body.profile, items, body.limit),
        "fallback": True,
    }
