import asyncio
import hashlib
import json
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google.adk.tools import ToolContext
from pydantic import BaseModel, Field

from agent import (
    build_assistant,
    enrich_items,
    parse_json_text,
    parse_search_query,
    plan_learning_path,
    recommend_items,
    run_assistant_turn,
    search_web,
)
from sources import SOURCES

CACHE_TTL_SECONDS = 600
ENRICH_CHUNK_SIZE = 20
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global warm_task
    warm_task = asyncio.create_task(warm_cache())
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
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
    await enrich(items)
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


async def cached_items(action: str) -> list[dict]:
    """Serve cached items at once; if they are stale, refresh in the background."""
    cached = item_cache.get(action)
    if cached:
        if time.time() - cached[0] >= CACHE_TTL_SECONDS:
            start_fetch(action)
        return cached[1]
    data = await start_fetch(action)
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


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
    keys = ("title", "subtitle", "category", "description", "summary")
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
