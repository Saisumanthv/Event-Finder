import asyncio
import hashlib
import hmac
import html
import json
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from google.adk.tools import ToolContext
from pydantic import BaseModel, Field

from agent import (
    build_assistant,
    build_prep_map,
    enrich_items,
    parse_json_text,
    parse_search_query,
    plan_learning_path,
    recommend_items,
    run_assistant_turn,
    search_web,
)
from sources import SOURCES
import rag

REFRESH_AFTER_SECONDS = 6 * 3600  # older than this: refresh in the background, keep serving
MAX_STALE_SECONDS = 12 * 3600  # older than this: wait for a fresh copy
ENRICH_CHUNK_SIZE = 20
# Times of day for the scheduled refresh (HH:MM, comma-separated, in REFRESH_TIMEZONE).
DEFAULT_REFRESH_TIMES = "06:00,12:00,18:00"
# Secret for POST /refresh, called by an external scheduler; the endpoint is off without it.
REFRESH_TOKEN = os.environ.get("REFRESH_TOKEN", "")
# Single-session events: once their date has passed they are over, even before a refresh.
ONE_OFF_SOURCES = {"Meetup", "Luma", "HasGeek"}
# Frontend addresses allowed to call this API, comma-separated; set it when deploying.
ALLOWED_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
CACHE_FILE = Path(__file__).with_name("cache.json")


def load_cache() -> dict[str, tuple[float, list[dict]]]:
    """Items saved by an earlier run, so a restart does not re-scrape everything."""
    try:
        saved = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return {a: (v["time"], v["items"]) for a, v in saved.items() if a in SOURCES}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def save_cache() -> None:
    saved = {a: {"time": t, "items": items} for a, (t, items) in item_cache.items()}
    try:
        temp = CACHE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(saved), encoding="utf-8")
        temp.replace(CACHE_FILE)
    except OSError as e:
        print(f"could not save cache: {e!r}")


item_cache = load_cache()
inflight: dict[str, asyncio.Task] = {}


async def warm_cache() -> None:
    """Refresh stale or missing categories in the background after startup."""
    await asyncio.gather(*(cached_items(a) for a in SOURCES), return_exceptions=True)


warm_task = None
schedule_task = None
refresh_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global warm_task, schedule_task
    warm_task = asyncio.create_task(warm_cache())
    schedule_task = asyncio.create_task(run_schedule())
    yield
    schedule_task.cancel()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Listings-Updated"],
)
app.include_router(rag.router)


@app.middleware("http")
async def add_listings_updated(request: Request, call_next):
    """Tell the page how old the oldest saved listings are (seconds since 1970)."""
    response = await call_next(request)
    updated = updated_at()
    if updated is not None:
        response.headers["X-Listings-Updated"] = str(int(updated))
    return response


class ButtonAction(BaseModel):
    action: str
    actionLabel: str = ""


class Profile(BaseModel):
    skills: list[str] = []
    level: str = ""
    interests: list[str] = []
    mode_preference: str = ""
    location: str = ""


class SearchRequest(BaseModel):
    query: str = Field(max_length=300)


class AssistantRequest(BaseModel):
    message: str = Field(max_length=500)


class WebSearchRequest(BaseModel):
    query: str = Field(max_length=300)


class PathRequest(BaseModel):
    goal: str = Field(max_length=300)
    max_steps: int = Field(4, ge=2, le=6)


class RecommendRequest(BaseModel):
    profile: Profile = Profile()
    limit: int = Field(5, ge=1, le=100)
    action: str | None = None  # restrict to one source; None means all sources


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
COSTS = {"free", "paid"}
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


async def enrich_chunk(chunk: list[dict]) -> None:
    try:
        merge_enrichment(await enrich_items(chunk), chunk)
    except Exception as e:
        print(f"enrich failed: {e!r}")


async def enrich(items: list[dict]) -> None:
    """Add model-generated fields to items; a failed batch just leaves them blank."""
    items = [item for item in items if isinstance(item, dict)]
    for item in items:
        item.setdefault("summary", "")
        item.setdefault("difficulty", "")
        item.setdefault("prerequisites", [])
    chunks = [items[i : i + ENRICH_CHUNK_SIZE] for i in range(0, len(items), ENRICH_CHUNK_SIZE)]
    await asyncio.gather(*(enrich_chunk(chunk) for chunk in chunks))


CARD_DEFAULTS = {
    "title": "",
    "subtitle": "",
    "category": "",
    "description": "",
    "date": "",
    "location": "",
    "mode": "",
    "tags": [],
    "cost": "",
    "link": "",
}


ENRICHED_FIELDS = ("summary", "difficulty", "prerequisites", "tags")


def reuse_enrichment(items: list[dict], previous: list[dict]) -> list[dict]:
    """Copy the model-made fields from the last refresh onto unchanged items.

    Returns the items that still need the model: new ones, changed ones, and ones whose
    earlier enrichment failed.
    """
    old = {i["id"]: i for i in previous if isinstance(i, dict) and "id" in i}
    needs_model = []
    for item in items:
        before = old.get(item["id"])
        unchanged = (
            before
            and before.get("summary")
            and before.get("title") == item["title"]
            and before.get("description") == item["description"]
        )
        if unchanged:
            for field in ENRICHED_FIELDS:
                item[field] = before.get(field, item.get(field))
        else:
            needs_model.append(item)
    return needs_model


def title_key(item: dict) -> str:
    return re.sub(r"[^a-z0-9]", "", str(item.get("title", "")).lower())


async def fetch_items(action: str) -> dict:
    """Collect one category from all its sources, then enrich and cache it."""
    sources = SOURCES[action]
    results = await asyncio.gather(
        *(asyncio.to_thread(fetch) for fetch in sources.values()), return_exceptions=True
    )

    items = []
    seen_titles = set()
    for name, result in zip(sources, results):
        if isinstance(result, Exception):
            print(f"source {name} failed: {result!r}")
            continue
        for raw in result:
            item = {**CARD_DEFAULTS, **raw, "source": name}
            if item["cost"] not in COSTS:
                item["cost"] = ""
            if item["link"] and title_key(item) not in seen_titles:
                seen_titles.add(title_key(item))
                items.append(item)

    if not items:
        return {"status": "error", "message": "No source returned any items", "items": []}

    data = {"status": "success", "action": action, "items": items}
    add_ids(action, data)
    previous = item_cache.get(action, (0, []))[1]
    await enrich(reuse_enrichment(items, previous))
    item_cache[action] = (time.time(), items)
    save_cache()
    return data


def start_fetch(action: str) -> asyncio.Task:
    """One shared fetch per category, however many requests ask for it at once."""
    task = inflight.get(action)
    if task is None:
        task = asyncio.create_task(fetch_items(action))
        inflight[action] = task

        def finished(done: asyncio.Task) -> None:
            inflight.pop(action, None)
            if not done.cancelled() and done.exception():
                print(f"fetch {action} failed: {done.exception()!r}")

        task.add_done_callback(finished)
    return task


def is_over(item: dict) -> bool:
    day = str(item.get("date") or "")[:10]
    return item.get("source") in ONE_OFF_SOURCES and bool(day) and day < date.today().isoformat()


def visible(items: list[dict]) -> list[dict]:
    """Drop single-session events that already happened since the last refresh."""
    return [item for item in items if not is_over(item)]


def updated_at(actions: list[str] | None = None) -> float | None:
    """When the oldest of these categories was last refreshed (seconds since 1970)."""
    times = [item_cache[a][0] for a in (actions or SOURCES) if a in item_cache]
    return min(times) if times else None


async def cached_items(action: str) -> list[dict]:
    """Serve the saved copy at once.

    Past REFRESH_AFTER_SECONDS it also starts a background refresh; past MAX_STALE_SECONDS
    the request waits for a fresh copy (and falls back to the old one if that fails).
    """
    cached = item_cache.get(action)
    if cached:
        age = time.time() - cached[0]
        if age >= MAX_STALE_SECONDS:
            try:
                await start_fetch(action)
            except Exception as e:
                print(f"refresh of {action} failed, serving the old copy: {e!r}")
            cached = item_cache.get(action) or cached
        elif age >= REFRESH_AFTER_SECONDS:
            start_fetch(action)
        return visible(cached[1])
    data = await start_fetch(action)
    items = data.get("items") if isinstance(data, dict) else None
    return visible(items) if isinstance(items, list) else []


async def refresh_all(actions: list[str]) -> None:
    """Refresh these categories now, whatever the age of the saved copy."""
    results = await asyncio.gather(*(start_fetch(a) for a in actions), return_exceptions=True)
    for action, result in zip(actions, results):
        if isinstance(result, Exception):
            print(f"scheduled refresh of {action} failed: {result!r}")


def parse_refresh_times(text: str) -> list:
    times = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            times.add(datetime.strptime(part, "%H:%M").time())
        except ValueError:
            print(f"ignoring invalid REFRESH_TIMES entry {part!r}")
    return sorted(times)


def refresh_timezone():
    name = os.environ.get("REFRESH_TIMEZONE", "Asia/Kolkata")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        print(f"unknown REFRESH_TIMEZONE {name!r}, using UTC")
        return timezone.utc


def next_run(now: datetime, times: list) -> tuple[datetime, int]:
    """The first scheduled time after `now`, and its position in the day's list."""
    for offset in (0, 1):
        day = now.date() + timedelta(days=offset)
        for index, at in enumerate(times):
            when = datetime.combine(day, at, tzinfo=now.tzinfo)
            if when > now:
                return when, index
    raise ValueError("no refresh times")


def due_actions(index: int) -> list[str]:
    """Events and hackathons change within hours; courses are refreshed once a day."""
    return [a for a in SOURCES if a != "new_courses" or index == 0]


async def run_schedule() -> None:
    times = parse_refresh_times(os.environ.get("REFRESH_TIMES", DEFAULT_REFRESH_TIMES))
    if not times:
        print("scheduled refresh is off (REFRESH_TIMES is empty)")
        return
    zone = refresh_timezone()
    print(f"scheduled refresh at {', '.join(t.strftime('%H:%M') for t in times)} ({zone})")
    while True:
        when, index = next_run(datetime.now(zone), times)
        # sleep in short steps so a long wait stays accurate if the clock changes
        while (remaining := (when - datetime.now(zone)).total_seconds()) > 0:
            await asyncio.sleep(min(remaining, 60))
        try:
            await refresh_all(due_actions(index))
        except Exception as e:
            print(f"scheduled refresh failed: {e!r}")


@app.post("/refresh", status_code=202)
async def refresh(x_refresh_token: str | None = Header(default=None)):
    """Start a refresh of every category. Meant for an external scheduler (cron-job.org)."""
    if not REFRESH_TOKEN:
        raise HTTPException(status_code=404, detail="Not found")
    if not x_refresh_token or not hmac.compare_digest(
        x_refresh_token.encode(), REFRESH_TOKEN.encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid token")
    task = asyncio.create_task(refresh_all(list(SOURCES)))
    refresh_tasks.add(task)
    task.add_done_callback(refresh_tasks.discard)
    return {"status": "started", "categories": list(SOURCES)}


@app.get("/health")
async def health():
    """A cheap check that also wakes a sleeping host; it never scrapes."""
    return {"status": "ok", "updated": {a: int(item_cache[a][0]) for a in SOURCES if a in item_cache}}


@app.post("/button-action")
async def button_action(body: ButtonAction):
    if body.action not in SOURCES:
        raise HTTPException(status_code=400, detail="Unknown action")

    try:
        items = await cached_items(body.action)
        if not items:
            return {"status": "error", "message": "No source returned any items", "items": []}
        return {"status": "success", "action": body.action, "items": items}
    except Exception as e:
        print(f"button_action failed: {e!r}")
        return {"status": "error", "message": str(e)[:300], "items": []}


def clean_reason(reason, max_words: int = 20) -> str:
    """Keep the reason to one short, link-free sentence."""
    if not isinstance(reason, str) or re.search(r"https?://|www\.", reason):
        return ""
    words = reason.split()
    return " ".join(words[:max_words])


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
    keys = ("title", "subtitle", "category", "description", "summary", "location")
    fields = [item.get(k, "") for k in keys]
    return " ".join(fields + list(item.get("tags") or [])).lower()


def has_keyword(text: str, keyword: str) -> bool:
    """True if `keyword` starts a word in `text`, so 'ai' matches 'AI' but not 'against'."""
    return re.search(r"(?<![a-z0-9])" + re.escape(keyword), text) is not None


def fallback_items(profile: Profile, items: list[dict], limit: int) -> list[dict]:
    """No-model fallback: filter by mode preference, then nearest date first."""
    terms = [t.strip().lower() for t in profile.skills + profile.interests if t.strip()]
    if terms:
        items = [i for i in items if any(has_keyword(item_text(i), t) for t in terms)]

    pref = profile.mode_preference.strip().lower()
    pool = [i for i in items if pref in str(i.get("mode", "")).lower()] if pref else items
    pool = pool or items
    ranked = sorted(pool, key=date_sort_key)[:limit]
    return [{**item, "rank": n, "reason": ""} for n, item in enumerate(ranked, 1)]


async def gather_items(actions: list[str]) -> list[dict]:
    """Items from the given sources, de-duplicated by id; a failed source is skipped."""
    results = await asyncio.gather(
        *(cached_items(action) for action in actions), return_exceptions=True
    )
    items = []
    seen_ids = set()
    for result in results:
        if isinstance(result, Exception):
            print(f"fetch failed: {result!r}")
            continue
        for item in result:
            if isinstance(item, dict) and item.get("id") not in seen_ids:
                seen_ids.add(item["id"])
                items.append(item)
    return items


TYPE_ACTIONS = {
    "event": "event_details",
    "hackathon": "hackathon_details",
    "course": "new_courses",
}
MODE_WORDS = {
    "online": ("online", "live", "self-paced", "virtual", "remote"),
    "offline": ("offline", "in-person", "in person", "onsite", "on-site"),
    "hybrid": ("hybrid",),
}
STOPWORDS = {
    "a", "an", "and", "for", "find", "me", "show", "the", "to", "in", "on", "of",
    "with", "some", "any", "get", "that", "are", "is", "i", "want", "looking",
    "into", "about", "from", "by", "at", "as", "my", "up", "do", "can", "how",
}


def clean_filters(raw: dict) -> dict:
    """Keep only valid filter keys and values from the model's reply."""
    filters = {}

    kind = str(raw.get("type", "")).strip().lower().rstrip("s")
    if kind in TYPE_ACTIONS:
        filters["type"] = kind
    mode = str(raw.get("mode", "")).strip().lower()
    if mode in MODE_WORDS:
        filters["mode"] = mode
    level = str(raw.get("level", "")).strip().lower()
    if level in DIFFICULTIES:
        filters["level"] = level
    cost = str(raw.get("cost", "")).strip().lower()
    if cost in COSTS:
        filters["cost"] = cost

    bounds = {}
    for key in ("date_from", "date_to"):
        try:
            bounds[key] = datetime.strptime(str(raw.get(key, "")), "%Y-%m-%d").date()
        except ValueError:
            pass
    if len(bounds) == 2 and bounds["date_from"] > bounds["date_to"]:
        bounds["date_from"], bounds["date_to"] = bounds["date_to"], bounds["date_from"]
    filters.update({key: d.isoformat() for key, d in bounds.items()})

    keywords = [k.lower() for k in clean_texts(raw.get("keywords"), 5, 30)]
    if keywords:
        filters["keywords"] = keywords
    return filters


def keyword_filters(query: str) -> dict:
    """No-model fallback: treat the meaningful words in the query as keywords."""
    words = re.findall(r"[a-z0-9+#.]+", query.lower())
    keywords = [w for w in words if len(w) > 1 and w not in STOPWORDS]
    return {"keywords": keywords[:5]} if keywords else {}


def matches_filters(item: dict, filters: dict) -> bool:
    """Plain-code filtering. An item whose value is unknown does not match a filter."""
    if "mode" in filters:
        mode = str(item.get("mode", "")).lower()
        if not any(word in mode for word in MODE_WORDS[filters["mode"]]):
            return False
    if "level" in filters and item.get("difficulty") != filters["level"]:
        return False
    if "cost" in filters and item.get("cost") != filters["cost"]:
        return False
    if "date_from" in filters or "date_to" in filters:
        item_date = parse_date(item.get("date"))
        if item_date is None:
            return False
        if "date_from" in filters and item_date.isoformat() < filters["date_from"]:
            return False
        if "date_to" in filters and item_date.isoformat() > filters["date_to"]:
            return False
    if "keywords" in filters:
        text = item_text(item)
        if not any(has_keyword(text, k) for k in filters["keywords"]):
            return False
    return True


@app.post("/search")
async def search(body: SearchRequest):
    query = body.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Empty query")

    today = date.today()
    fallback = False
    try:
        raw = await parse_search_query(query, f"{today.isoformat()} ({today:%A})")
        filters = clean_filters(raw)
    except Exception as e:
        print(f"search parse failed: {e!r}")
        filters = keyword_filters(query)
        fallback = True

    actions = [TYPE_ACTIONS[filters["type"]]] if "type" in filters else list(SOURCES)
    items = await gather_items(actions)
    results = [item for item in items if matches_filters(item, filters)]
    return {"filters": filters, "results": results, "fallback": fallback}


ACTION_TYPES = {action: kind for kind, action in TYPE_ACTIONS.items()}
TYPE_ORDER = {"course": 0, "hackathon": 1, "event": 2}
LEVEL_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}


def item_type(item: dict) -> str:
    """course, hackathon or event, taken from the source encoded in the item id."""
    return ACTION_TYPES.get(str(item.get("id", "")).split("-")[0], "")


def is_past(item: dict) -> bool:
    parsed = parse_date(item.get("date"))
    return parsed is not None and parsed < date.today()


def build_path(raw: list, items: list[dict], max_steps: int) -> list[dict]:
    """Keep valid, unique ids in the model's order and merge in the source data.

    A dated step that would fall before an earlier dated step is dropped, so the
    path never goes backwards in time.
    """
    by_id = {item["id"]: item for item in items}
    seen = set()
    latest = None
    steps = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        item = by_id.get(entry["id"])
        if item is None or entry["id"] in seen:
            continue
        when = parse_date(item.get("date"))
        if when and latest and when < latest:
            continue
        seen.add(entry["id"])
        if when:
            latest = max(latest, when) if latest else when
        steps.append((item, clean_reason(entry.get("why_next"), 25)))
        if len(steps) == max_steps:
            break

    return [
        {**item, "type": item_type(item), "step": n, "why_next": why}
        for n, (item, why) in enumerate(steps, 1)
    ]


def fallback_path(goal: str, items: list[dict], max_steps: int) -> list[dict]:
    """No-model fallback: goal keywords, then courses, hackathons and events in turn."""
    terms = keyword_filters(goal).get("keywords", [])
    if terms:
        items = [i for i in items if any(has_keyword(item_text(i), t) for t in terms)]

    def order(item):
        level = LEVEL_ORDER.get(item.get("difficulty"), 1)
        return (TYPE_ORDER.get(item_type(item), 3), level, date_sort_key(item))

    ranked = sorted(items, key=order)[:max_steps]
    return [
        {**item, "type": item_type(item), "step": n, "why_next": ""}
        for n, item in enumerate(ranked, 1)
    ]


@app.post("/learning-path")
async def learning_path(body: PathRequest):
    goal = body.goal.strip()
    if not goal:
        raise HTTPException(status_code=400, detail="Empty goal")

    items = [i for i in await gather_items(list(SOURCES)) if not is_past(i)]
    if not items:
        return {"overview": "", "path": [], "fallback": False, "message": "No items available"}

    try:
        with_types = [{**i, "type": item_type(i)} for i in items]
        plan = await plan_learning_path(
            goal, with_types, body.max_steps, date.today().isoformat()
        )
        path = build_path(plan["path"], items, body.max_steps)
        if path or not plan["path"]:
            # an empty path from the model means "nothing fits", not a failure
            return {
                "overview": clean_reason(plan.get("overview"), 30) if path else "",
                "path": path,
                "fallback": False,
            }
        print("learning-path: model returned no valid ids")
    except Exception as e:
        print(f"learning-path failed: {e!r}")

    return {"overview": "", "path": fallback_path(goal, items, body.max_steps), "fallback": True}


@app.post("/recommend")
async def recommend(body: RecommendRequest):
    if body.action and body.action not in SOURCES:
        raise HTTPException(status_code=400, detail="Unknown action")
    actions = [body.action] if body.action else list(SOURCES)

    items = await gather_items(actions)

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


def site_name(value) -> str:
    """Lower-case website name without a leading 'www.'."""
    text = str(value or "").strip().lower()
    try:
        host = urlparse(text if "//" in text else "//" + text).netloc
    except ValueError:
        return ""
    return host.removeprefix("www.")


def resolve_url(uri: str) -> str:
    """Follow a search-result redirect to the real page address; keep `uri` on failure."""
    try:
        with requests.get(uri, timeout=8, stream=True, headers={"User-Agent": "Mozilla/5.0"}) as r:
            return r.url
    except requests.RequestException:
        return uri


def same_site(a: str, b: str) -> bool:
    """True if two site names are equal or one is a subdomain of the other."""
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def title_overlap(title: str, page: dict) -> int:
    """How many title words appear in the page address, ignoring the site's own name."""
    address = page["url"].lower().replace(site_name(page["domain"]), "")
    words = set(re.findall(r"[a-z0-9]{3,}", title.lower()))
    return sum(1 for w in words if w in address)


def build_web_cards(items: list, pages: list[dict]) -> list[dict]:
    """Turn the model's web results into cards; links come only from cited pages."""
    cards = []
    seen = set()
    for raw in items:
        if not isinstance(raw, dict) or not str(raw.get("title", "")).strip():
            continue
        domain = site_name(raw.get("domain"))
        candidates = [p for p in pages if same_site(site_name(p["domain"]), domain)]
        if not candidates:
            continue
        title = str(raw["title"]).strip()[:150]
        page = max(candidates, key=lambda p: title_overlap(title, p))
        when = parse_date(raw.get("date"))
        # a page sharing no words with the title is probably a listing, not this item
        if title_overlap(title, page) == 0:
            continue
        if (when and when < date.today()) or page["url"] in seen:
            continue
        seen.add(page["url"])

        description = str(raw.get("description") or "").strip()[:300]
        mode = str(raw.get("mode", "")).strip().lower()
        cost = str(raw.get("cost", "")).strip().lower()
        kind = str(raw.get("type", "")).strip().lower()
        cards.append(
            {
                **CARD_DEFAULTS,
                "id": "web-" + hashlib.sha1(page["url"].encode()).hexdigest()[:8],
                "title": title,
                "category": kind.capitalize() if kind in TYPE_ACTIONS else "",
                "description": "" if URL_PATTERN.search(description) else description,
                "date": when.isoformat() if when else "",
                "location": "" if URL_PATTERN.search(str(raw.get("location", ""))) else str(raw.get("location") or "")[:80],
                "mode": mode if mode in MODE_WORDS else "",
                "cost": cost if cost in COSTS else "",
                "link": page["url"],
                "source": site_name(page["domain"]),
            }
        )
    return cards


@app.post("/web-search")
async def web_search(body: WebSearchRequest):
    query = body.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Empty query")

    try:
        items, pages = await search_web(query, date.today().isoformat())
        urls = await asyncio.gather(*(asyncio.to_thread(resolve_url, p["uri"]) for p in pages))
        for page, url in zip(pages, urls):
            page["url"] = url
        return {"results": build_web_cards(items, pages)}
    except Exception as e:
        print(f"web-search failed: {e!r}")
        return {"results": [], "message": "Web search is unavailable right now."}


def compact_item(item: dict) -> dict:
    """What the assistant sees of an item: no link, short description."""
    return {
        "id": item["id"],
        "type": item_type(item) or str(item.get("category", "")).lower(),
        "title": item.get("title", ""),
        "category": item.get("category", ""),
        "date": item.get("date", ""),
        "mode": item.get("mode", ""),
        "cost": item.get("cost", ""),
        "difficulty": item.get("difficulty", ""),
        "source": item.get("source", ""),
        "description": (item.get("description") or "")[:200],
    }


def remember(tool_context: ToolContext, items: list[dict]) -> None:
    """Record the items a tool surfaced so only they can be picked in the answer."""
    tool_context.state[f"items_{tool_context.function_call_id}"] = {i["id"]: i for i in items}


async def search_listings(
    tool_context: ToolContext,
    listing_type: str = "",
    mode: str = "",
    level: str = "",
    cost: str = "",
    date_from: str = "",
    date_to: str = "",
    keywords: str = "",
) -> dict:
    """Search the current event, hackathon and course listings with filters.

    Args:
        listing_type: "event", "hackathon" or "course"; empty searches all three.
        mode: "online", "offline" or "hybrid"; empty for any.
        level: "beginner", "intermediate" or "advanced"; empty for any.
        cost: "free" or "paid"; empty for any.
        date_from: earliest date as YYYY-MM-DD; empty for none.
        date_to: latest date as YYYY-MM-DD; empty for none.
        keywords: comma-separated topic words such as "python, machine learning".

    Returns:
        The total number of matches and up to 15 of them, soonest first.
    """
    try:
        words = [w.strip() for w in keywords.split(",") if w.strip()]
        filters = clean_filters(
            {
                "type": listing_type,
                "mode": mode,
                "level": level,
                "cost": cost,
                "date_from": date_from,
                "date_to": date_to,
                "keywords": words,
            }
        )
        actions = [TYPE_ACTIONS[filters["type"]]] if "type" in filters else list(SOURCES)
        items = await gather_items(actions)
        found = sorted((i for i in items if matches_filters(i, filters)), key=date_sort_key)
        shown = found[:15]
        remember(tool_context, shown)
        applied = ", ".join(f"{k}={v}" for k, v in filters.items()) or "no filters"
        return {
            "summary": f"{len(found)} listings found ({applied})",
            "total": len(found),
            "items": [compact_item(i) for i in shown],
        }
    except Exception as e:
        print(f"search_listings failed: {e!r}")
        return {"summary": "listing search failed", "total": 0, "items": []}


async def get_details(tool_context: ToolContext, item_id: str) -> dict:
    """Get the full details of one listing by the id returned from a search.

    Args:
        item_id: the id of a listing.

    Returns:
        Its description, summary, prerequisites, tags and location.
    """
    try:
        items = await gather_items(list(SOURCES))
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None:
            return {"summary": "no listing with that id"}
        remember(tool_context, [item])
        return {
            **compact_item(item),
            "summary": f"details of {item['title'][:40]}",
            "description": (item.get("description") or "")[:400],
            "ai_summary": item.get("summary", ""),
            "prerequisites": item.get("prerequisites", []),
            "tags": item.get("tags", []),
            "location": item.get("location", ""),
        }
    except Exception as e:
        print(f"get_details failed: {e!r}")
        return {"summary": "details lookup failed"}


async def build_learning_path(tool_context: ToolContext, goal: str, max_steps: int = 4) -> dict:
    """Build an ordered learning path of courses, hackathons and events for a goal.

    Args:
        goal: what the user wants to achieve, for example "get into machine learning".
        max_steps: how many steps at most, between 2 and 6.

    Returns:
        The overview and the ordered steps, each with its id and why it comes next.
    """
    try:
        max_steps = max(2, min(int(max_steps), 6))
        items = [i for i in await gather_items(list(SOURCES)) if not is_past(i)]
        with_types = [{**i, "type": item_type(i)} for i in items]
        plan = await plan_learning_path(goal, with_types, max_steps, date.today().isoformat())
        path = build_path(plan["path"], items, max_steps)
        remember(tool_context, path)
        return {
            "summary": f"learning path with {len(path)} steps",
            "overview": clean_reason(plan.get("overview"), 30),
            "steps": [
                {
                    "step": s["step"],
                    "id": s["id"],
                    "type": s["type"],
                    "title": s["title"],
                    "why_next": s["why_next"],
                }
                for s in path
            ],
        }
    except Exception as e:
        print(f"build_learning_path failed: {e!r}")
        return {"summary": "path planning failed", "steps": []}


async def search_the_web(tool_context: ToolContext, query: str) -> dict:
    """Search the whole web for events, hackathons or courses.

    Slow and less accurate: use it only when the listings clearly do not cover the
    request.

    Args:
        query: a short search query.

    Returns:
        Web results found, each with an id, type, title, date and cost when known.
    """
    try:
        items, pages = await search_web(query, date.today().isoformat())
        urls = await asyncio.gather(*(asyncio.to_thread(resolve_url, p["uri"]) for p in pages))
        for page, url in zip(pages, urls):
            page["url"] = url
        cards = build_web_cards(items, pages)
        remember(tool_context, cards)
        return {
            "summary": f"{len(cards)} results found on the web",
            "items": [compact_item(c) for c in cards],
        }
    except Exception as e:
        print(f"search_the_web failed: {e!r}")
        return {"summary": "web search failed", "items": []}


assistant_agent = build_assistant([search_listings, get_details, build_learning_path, search_the_web])

URL_TOKEN = re.compile(r"\S*(?:https?://|www\.)\S*", re.IGNORECASE)


def clean_answer(text) -> str:
    """The assistant's sentences with any URL-like token removed."""
    if not isinstance(text, str):
        return ""
    return " ".join(URL_TOKEN.sub("", text).split())[:600]


def build_assistant_reply(final_text: str, items: dict) -> tuple[str, list[dict]]:
    """Answer text plus the picked cards; only ids the tools returned can be picked."""
    try:
        data = parse_json_text(final_text)
    except ValueError:
        return clean_answer(final_text), []
    answer = clean_answer(data.get("answer")) if isinstance(data, dict) else ""
    picks = data.get("picks") if isinstance(data, dict) else None

    cards = []
    seen = set()
    for pick in picks if isinstance(picks, list) else []:
        pick_id = pick.get("id") if isinstance(pick, dict) else None
        if not isinstance(pick_id, str) or pick_id not in items or pick_id in seen:
            continue
        seen.add(pick_id)
        cards.append({**items[pick_id], "reason": clean_reason(pick.get("why"), 25)})
        if len(cards) == 12:
            break
    return answer, cards


def describe_step(step: dict) -> dict:
    args = ", ".join(f"{k}: {v}" for k, v in step["args"].items() if v not in ("", None, []))
    return {"tool": step["tool"], "detail": args, "result": step["result"]}


@app.post("/assistant")
async def assistant(body: AssistantRequest):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Empty message")

    today = date.today()
    prompt = f"today: {today.isoformat()} ({today:%A})\nrequest: {json.dumps(message)}"
    try:
        final_text, steps, items = await asyncio.wait_for(
            run_assistant_turn(assistant_agent, prompt), timeout=240
        )
        answer, cards = build_assistant_reply(final_text, items)
        if answer or cards:
            return {
                "answer": answer,
                "steps": [describe_step(s) for s in steps],
                "cards": cards,
                "fallback": False,
            }
        print("assistant: empty reply")
    except Exception as e:
        print(f"assistant failed: {e!r}")

    terms = keyword_filters(message)
    found = [i for i in await gather_items(list(SOURCES)) if matches_filters(i, terms)]
    cards = [{**i, "reason": ""} for i in sorted(found, key=date_sort_key)[:12]]
    return {
        "answer": "The assistant is unavailable right now, so these are plain keyword matches.",
        "steps": [],
        "cards": cards,
        "fallback": True,
    }


class PrepMapRequest(BaseModel):
    title: str = Field(max_length=200)
    subtitle: str = Field("", max_length=200)
    category: str = Field("", max_length=100)
    description: str = Field("", max_length=1000)
    summary: str = Field("", max_length=400)
    difficulty: str = Field("", max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=10)
    prerequisites: list[str] = Field(default_factory=list, max_length=10)
    approach: str = Field("fundamentals", pattern="^(fundamentals|vibe)$")


PREP_CACHE_SIZE = 400
PREP_CACHE_FILE = Path(__file__).with_name("prep_cache.json")


def load_prep_cache() -> dict[str, dict]:
    """Prep maps saved by an earlier run, so a restart does not regenerate them."""
    try:
        saved = json.loads(PREP_CACHE_FILE.read_text(encoding="utf-8"))
        return {k: v for k, v in saved.items() if isinstance(v, dict) and v.get("skills")}
    except (OSError, ValueError, AttributeError):
        return {}


prep_cache: dict[str, dict] = load_prep_cache()
prep_inflight: dict[str, asyncio.Task] = {}


def remember_prep(key: str, result: dict) -> None:
    """Keep a finished prep map in memory and on disk, dropping the oldest when full."""
    while len(prep_cache) >= PREP_CACHE_SIZE:
        prep_cache.pop(next(iter(prep_cache)))
    prep_cache[key] = result
    try:
        temp = PREP_CACHE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(prep_cache), encoding="utf-8")
        temp.replace(PREP_CACHE_FILE)
    except OSError as e:
        print(f"could not save prep cache: {e!r}")


def clean_line(value, max_len: int) -> str:
    """One short line of plain text; empty if it is not text or contains a link."""
    if not isinstance(value, str) or URL_PATTERN.search(value):
        return ""
    return " ".join(value.split())[:max_len]


def clean_prep_map(raw: dict) -> dict:
    """Keep only well-formed, link-free skills, topics and subtopics, within size limits."""
    skills = []
    for skill in raw.get("skills", [])[:8]:
        if not isinstance(skill, dict):
            continue
        raw_topics = skill.get("topics")
        topics = []
        for topic in (raw_topics if isinstance(raw_topics, list) else [])[:6]:
            if not isinstance(topic, dict):
                continue
            name = clean_line(topic.get("name"), 80)
            if name:
                topics.append({"name": name, "subtopics": clean_texts(topic.get("subtopics"), 6, 80)})
        name = clean_line(skill.get("name"), 80)
        if name and topics:
            skills.append({"name": name, "why": clean_reason(skill.get("why"), 25), "topics": topics})

    basis = raw.get("basis") if raw.get("basis") in ("listing", "general") else "general"
    fit_note = clean_line(raw.get("fit_note"), 300) if raw.get("fit") == "limited" else ""
    return {
        "overview": clean_line(raw.get("overview"), 400),
        "basis": basis,
        "fit_note": fit_note,
        "skills": skills,
    }


def fallback_prep_map(item: dict) -> dict:
    """No-model fallback: just the skills the listing itself names."""
    names = clean_texts(item["prerequisites"] + item["tags"], 8, 80)
    return {
        "overview": "The AI prep map is unavailable, so this only lists the skills named in the listing.",
        "basis": "listing",
        "fit_note": "",
        "skills": [{"name": n, "why": "", "topics": []} for n in names],
        "fallback": True,
    }


async def generate_prep_map(item: dict, key: str) -> dict:
    try:
        hackathon = {k: v for k, v in item.items() if k != "approach"}
        raw = await asyncio.wait_for(build_prep_map(hackathon, item["approach"]), timeout=120)
        cleaned = clean_prep_map(raw)
        if cleaned["skills"]:
            result = {**cleaned, "fallback": False}
            remember_prep(key, result)
            return result
        print("prep-map: the model returned no usable skills")
    except Exception as e:
        print(f"prep-map failed: {e!r}")
    return fallback_prep_map(item)


@app.post("/prep-map")
async def prep_map(body: PrepMapRequest):
    item = body.model_dump()
    key = hashlib.sha1(json.dumps(item, sort_keys=True).encode()).hexdigest()
    if key in prep_cache:
        return prep_cache[key]

    # Requests for the same map (a background prefetch and a click) share one model call.
    task = prep_inflight.get(key)
    if task is None:
        task = asyncio.create_task(generate_prep_map(item, key))
        prep_inflight[key] = task
        task.add_done_callback(lambda _done: prep_inflight.pop(key, None))
    return await asyncio.shield(task)


# --- YouTube video suggestions for a skill (on demand, never part of the prep map) ---------------

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
YOUTUBE_DAILY_LIMIT = int(os.environ.get("YOUTUBE_DAILY_LIMIT", "90"))  # YouTube allows 100 searches a day
VIDEO_CACHE_SECONDS = 7 * 24 * 3600  # YouTube's policy asks that stored API data is refreshed within 30 days
VIDEO_CACHE_SIZE = 500
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")

video_cache: dict[str, tuple[float, list[dict]]] = {}
video_inflight: dict[str, asyncio.Task] = {}
video_usage = {"day": "", "count": 0}
video_blocked_until = 0.0


class VideoRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=80)
    context: str = Field("", max_length=80)  # the skill, used only when there are no subtopics
    subtopics: list[str] = Field(default_factory=list, max_length=6)


def youtube_day() -> str:
    """YouTube's quota resets at midnight Pacific time."""
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


VIDEO_STOPWORDS = {"and", "the", "for", "with", "using", "basics", "intro", "tutorial", "from", "your", "into"}
MIN_VIDEO_SECONDS = 240  # drops Shorts and clips


def keywords_of(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9+#]+", text.lower())
    return [w for w in words if len(w) >= 3 and w not in VIDEO_STOPWORDS]


def video_seconds(iso: str) -> int:
    parts = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    d, h, m, sec = (int(x or 0) for x in parts.groups()) if parts else (0, 0, 0, 0)
    return ((d * 24 + h) * 60 + m) * 60 + sec


def mentions(text: str, word: str) -> bool:
    return word[:5] in text  # prefix match, so "visualization" also finds "visualizing"


def youtube_get(endpoint: str, params: dict) -> dict:
    response = requests.get(
        f"https://www.googleapis.com/youtube/v3/{endpoint}", params={**params, "key": YOUTUBE_API_KEY}, timeout=10
    )
    if response.status_code != 200:
        # Never include the request URL in an error: it contains the key.
        raise RuntimeError(f"YouTube answered {response.status_code}: {response.text[:200]}")
    return response.json()


def search_youtube(topic: str, subtopics: list[str], query: str) -> list[dict]:
    """The two best videos for a topic. Titles, channels and links come from YouTube, never from the model.

    One search brings back 10 candidates; code then drops Shorts and hashtag spam and keeps
    the ones whose title or description actually mention the topic, so an unrelated
    popular video does not win just because it ranked first.
    """
    found = youtube_get(
        "search",
        {"part": "snippet", "type": "video", "maxResults": 10, "q": query, "safeSearch": "strict", "relevanceLanguage": "en"},
    )
    candidates = {}
    for entry in found.get("items", []):
        video_id = (entry.get("id") or {}).get("videoId", "")
        if VIDEO_ID_PATTERN.match(video_id):
            candidates[video_id] = entry.get("snippet") or {}
    if not candidates:
        return []

    # One cheap call (1 unit, separate from the 100-search limit) for duration and views.
    details = youtube_get("videos", {"part": "contentDetails,statistics", "id": ",".join(candidates)})
    topic_words = list(dict.fromkeys(keywords_of(topic)))
    sub_words = list(dict.fromkeys(w for s in subtopics for w in keywords_of(s)))
    need = max(1, len(topic_words) // 2)

    ranked = []
    for video in details.get("items", []):
        snippet = candidates.get(video.get("id"))
        if snippet is None:
            continue
        title = html.unescape(str(snippet.get("title", "")))
        text = f"{title} {html.unescape(str(snippet.get('description', '')))}".lower()
        if video_seconds((video.get("contentDetails") or {}).get("duration", "")) < MIN_VIDEO_SECONDS:
            continue
        if title.count("#") >= 3:
            continue
        topic_hits = sum(mentions(text, w) for w in topic_words)
        if topic_hits < need:
            continue
        sub_hits = sum(mentions(text, w) for w in sub_words)
        views = int((video.get("statistics") or {}).get("viewCount", 0) or 0)
        ranked.append((topic_hits * 2 + sub_hits, views, video["id"], snippet, title))

    ranked.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return [
        {
            "title": title[:150],
            "channel": html.unescape(str(snippet.get("channelTitle", "")))[:80],
            "url": f"https://www.youtube.com/watch?v={video_id}",
        }
        for _score, _views, video_id, snippet, title in ranked[:2]
    ]


@app.post("/videos")
async def videos(body: VideoRequest):
    global video_blocked_until
    if not YOUTUBE_API_KEY:
        return {"available": False, "videos": [], "reason": "not_configured"}

    topic = " ".join(body.topic.split())
    # The topic's own subtopics steer the search; the skill name alone can pull in a neighbouring topic.
    subtopics = [" ".join(t.split())[:60] for t in body.subtopics[:2] if t.strip()]
    extra = subtopics or [" ".join(body.context.split())]
    query = " ".join([topic, *extra, "tutorial"]).replace("  ", " ")
    key = query.lower()
    cached = video_cache.get(key)
    if cached and time.time() - cached[0] < VIDEO_CACHE_SECONDS:
        return {"available": True, "videos": cached[1]}
    if time.time() < video_blocked_until:
        return {"available": False, "videos": [], "reason": "quota"}

    today = youtube_day()
    if video_usage["day"] != today:
        video_usage.update(day=today, count=0)
    if video_usage["count"] >= YOUTUBE_DAILY_LIMIT:
        return {"available": False, "videos": [], "reason": "quota"}

    task = video_inflight.get(key)
    if task is None:
        video_usage["count"] += 1
        task = asyncio.create_task(asyncio.to_thread(search_youtube, topic, subtopics, query))
        video_inflight[key] = task
        task.add_done_callback(lambda _done: video_inflight.pop(key, None))
    try:
        found = await asyncio.shield(task)
    except Exception as e:
        print(f"videos failed: {e!r}")
        if "quotaExceeded" in str(e) or "dailyLimitExceeded" in str(e):
            video_blocked_until = time.time() + 3600
            return {"available": False, "videos": [], "reason": "quota"}
        return {"available": False, "videos": [], "reason": "error"}

    while len(video_cache) >= VIDEO_CACHE_SIZE:
        video_cache.pop(next(iter(video_cache)))
    video_cache[key] = (time.time(), found)
    return {"available": True, "videos": found}
