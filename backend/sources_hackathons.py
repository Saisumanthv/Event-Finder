import html
import json
import re
from datetime import date, datetime, timezone

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
}
MAX_ITEMS = 15
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _get(url: str, **kwargs) -> requests.Response:
    """GET a URL with a browser-like User-Agent and a 15s timeout."""
    response = requests.get(url, headers=HEADERS, timeout=15, **kwargs)
    response.raise_for_status()
    return response


def _clean(text: str | None, limit: int = 400) -> str:
    """Strip HTML and extra whitespace, then truncate to limit characters."""
    if not text:
        return ""
    text = BeautifulSoup(html.unescape(str(text)), "html.parser").get_text(" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _iso_day(value: str | None) -> str:
    """Return the YYYY-MM-DD part of an ISO timestamp, or an empty string."""
    match = re.match(r"\d{4}-\d{2}-\d{2}", value or "")
    return match.group(0) if match else ""


def _is_finished(end_day: str) -> bool:
    """Whether an end date (YYYY-MM-DD) is before today."""
    return bool(end_day) and end_day < date.today().isoformat()


def _item(title, link, *, subtitle="", category="Hackathon", description="", day="",
          location="", mode="", tags=None, cost="") -> dict:
    """Build a hackathon dict with the shared schema."""
    return {
        "title": _clean(title, 200),
        "subtitle": _clean(subtitle, 120),
        "category": category,
        "description": _clean(description),
        "date": day,
        "location": _clean(location, 120),
        "mode": mode,
        "tags": [t.strip().lower() for t in (tags or []) if t and t.strip()][:5],
        "cost": cost,
        "link": link,
    }


def _usable(item: dict) -> bool:
    """Whether an item has the required title and link."""
    return bool(item["title"] and item["link"].startswith("http"))


def _parse_range(text: str) -> tuple[str, str]:
    """Parse a Devpost range like 'Oct 10 - 28, 2026' into (start, end) dates."""
    parts = [p.strip() for p in text.split(" - ")]
    right = re.match(r"(?:([A-Za-z]{3})[a-z]* )?(\d{1,2})(?:, (\d{4}))?", parts[-1])
    if not right:
        return "", ""
    left = re.match(r"([A-Za-z]{3})[a-z]* (\d{1,2})(?:, (\d{4}))?", parts[0])
    year = int(right.group(3) or (left.group(3) if left else 0) or date.today().year)
    end_month = MONTHS.get((right.group(1) or (left.group(1) if left else "")).lower())
    if not end_month:
        return "", ""
    end = date(year, end_month, int(right.group(2)))
    if not left:
        return end.isoformat(), end.isoformat()
    start_month = MONTHS.get(left.group(1).lower(), end_month)
    start_year = int(left.group(3)) if left.group(3) else (year - 1 if start_month > end_month else year)
    return date(start_year, start_month, int(left.group(2))).isoformat(), end.isoformat()


def fetch_devpost() -> list[dict]:
    """Open and upcoming hackathons from Devpost's public JSON API."""
    items = []
    for page in (1, 2):
        data = _get(
            "https://devpost.com/api/hackathons",
            params={"status[]": ["open", "upcoming"], "order_by": "deadline", "page": page},
        ).json()
        for h in data.get("hackathons", []):
            if h.get("invite_only"):
                continue
            start, end = _parse_range(h.get("submission_period_dates") or "")
            if _is_finished(end):
                continue
            where = (h.get("displayed_location") or {}).get("location") or ""
            themes = [t["name"] for t in h.get("themes", [])]
            prize = _clean((h.get("prize_amount") or "").replace("<span data-currency-value>", ""), 40)
            description = (
                f"Submission period {h.get('submission_period_dates')}. "
                f"Themes: {', '.join(themes) or 'open'}. Prizes: {prize or 'n/a'}. "
                f"{h.get('registrations_count', 0)} participants registered."
            )
            item = _item(
                h.get("title"), h.get("url", ""),
                subtitle=h.get("organization_name") or "Devpost",
                category=themes[0] if themes else "Hackathon",
                description=description, day=start, location=where,
                mode="online" if where.lower() == "online" else "offline" if where else "",
                tags=themes,
            )
            if _usable(item):
                items.append(item)
        if len(items) >= MAX_ITEMS:
            break
    return items[:MAX_ITEMS]


def fetch_mlh() -> list[dict]:
    """Upcoming Major League Hacking events parsed from schema.org microdata."""
    today = date.today()
    season = today.year + 1 if today.month >= 8 else today.year
    soup = BeautifulSoup(_get(f"https://mlh.io/seasons/{season}/events").text, "html.parser")
    modes = {"OnlineEventAttendanceMode": "online", "OfflineEventAttendanceMode": "offline",
             "MixedEventAttendanceMode": "hybrid"}
    items = []
    for card in soup.select('a[itemtype="https://schema.org/Event"]'):
        def meta(prop: str) -> str:
            tag = card.find("meta", itemprop=prop)
            return tag["content"] if tag and tag.get("content") else ""

        start, end = _iso_day(meta("startDate")), _iso_day(meta("endDate"))
        if _is_finished(end or start):
            continue
        mode = modes.get(meta("eventAttendanceMode").rsplit("/", 1)[-1], "")
        name = card.select_one("h4")
        place = card.select_one('[itemprop="location"] [itemprop="name"]')
        location = "Online" if mode == "online" else (place.get_text("", strip=True) if place else "")
        free = meta("isAccessibleForFree")
        title = name.get_text(strip=True) if name else ""
        item = _item(
            title, meta("url") or card["href"].split("?")[0],
            subtitle="Major League Hacking",
            description=f"{title} is an MLH-sanctioned student hackathon running {start} to {end}.",
            day=start, location=location, mode=mode, tags=["mlh", "student"],
            cost="free" if free == "true" else "paid" if free == "false" else "",
        )
        if _usable(item):
            items.append(item)
    items.sort(key=lambda i: i["date"])
    return items[:MAX_ITEMS]


def fetch_devfolio() -> list[dict]:
    """Open and upcoming hackathons from Devfolio's embedded Next.js data."""
    page = _get("https://devfolio.co/hackathons").text
    raw = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not raw:
        raise ValueError("Devfolio: __NEXT_DATA__ not found")
    queries = json.loads(raw.group(1))["props"]["pageProps"]["dehydratedState"]["queries"]
    data = next(q["state"]["data"] for q in queries if "open_hackathons" in (q["state"]["data"] or {}))
    now = datetime.now(timezone.utc).isoformat()
    items = []
    for h in data.get("open_hackathons", []) + data.get("upcoming_hackathons", []):
        settings = h.get("settings") or {}
        reg_end = settings.get("reg_ends_at") or ""
        if (reg_end and reg_end < now) or _is_finished(_iso_day(h.get("ends_at"))):
            continue
        themes = [t["theme"]["name"] for t in h.get("themes", []) if t.get("theme")]
        online = bool(h.get("is_online"))
        description = (
            f"Runs {_iso_day(h.get('starts_at'))} to {_iso_day(h.get('ends_at'))}. "
            f"Registration closes {_iso_day(reg_end) or 'TBA'}. "
            f"Themes: {', '.join(themes) or 'open'}. {h.get('participants_count', 0)} participants."
        )
        item = _item(
            h.get("name"), f"https://{h['slug']}.devfolio.co/",
            subtitle="Devfolio", category=next((t for t in themes if t != "No Restrictions"), "Hackathon"),
            description=description, day=_iso_day(h.get("starts_at")),
            location="Online" if online else "", mode="online" if online else "offline",
            tags=[t for t in themes if t != "No Restrictions"],
        )
        if _usable(item):
            items.append(item)
    items.sort(key=lambda i: i["date"])
    return items[:MAX_ITEMS]


def fetch_hackerearth() -> list[dict]:
    """Upcoming HackerEarth hackathons from its public events feed."""
    events = _get("https://www.hackerearth.com/chrome-extension/events/").json().get("response", [])
    items = []
    for e in events:
        blob = f"{e.get('title')} {e.get('url')} {e.get('challenge_type')}".lower()
        end = _iso_day(e.get("end_tz"))
        if "hack" not in blob or _is_finished(end):
            continue
        start = _iso_day(e.get("start_tz"))
        description = (
            f"{_clean(e.get('description'), 300)} Runs {e.get('start_timestamp')} "
            f"to {e.get('end_timestamp')}."
        )
        item = _item(
            e.get("title"), e.get("url", ""), subtitle="HackerEarth",
            description=description, day=start, tags=["hackerearth"],
        )
        if _usable(item):
            items.append(item)
    return items[:MAX_ITEMS]


def fetch_lablab() -> list[dict]:
    """Current and upcoming lablab.ai hackathons from the listing's embedded payload."""
    page = _get("https://lablab.ai/ai-hackathons").text
    backslash = chr(92)
    pattern = r'self\.__next_f\.push\(\[1,"((?:[^"' + backslash * 2 + r']|' + backslash * 2 + r'.)*)"\]\)'
    stream = "".join(json.loads(f'"{chunk}"') for chunk in re.findall(pattern, page))
    decoder = json.JSONDecoder()
    modes = {"ONLINE": "online", "ONSITE": "offline", "HYBRID": "hybrid"}
    items, seen = [], set()
    for match in re.finditer(r'\{"id":"[a-z0-9]{20,30}","name":', stream):
        try:
            e, _ = decoder.raw_decode(stream[match.start():])
        except ValueError:
            continue
        slug = e.get("slug")
        if not slug or "startAt" not in e or slug in seen or e.get("type") != "HACKATHON":
            continue
        seen.add(slug)
        if _is_finished(_iso_day(e.get("endAt"))):
            continue
        techs = [t.get("name", "") for t in e.get("technologyList") or [] if isinstance(t, dict)]
        description = _clean(e.get("subtitle") or e.get("description"), 330)
        description = f"{description} Runs {_iso_day(e['startAt'])} to {_iso_day(e['endAt'])}."
        mode = modes.get(e.get("eventType"), "")
        item = _item(
            e.get("name"), f"https://lablab.ai/ai-hackathons/{slug}",
            subtitle="lablab.ai", category="AI", description=description,
            day=_iso_day(e["startAt"]), location="Online" if mode == "online" else "",
            mode=mode, tags=["ai", *techs],
            cost="paid" if e.get("paymentAmount") else "",
        )
        if _usable(item):
            items.append(item)
    items.sort(key=lambda i: i["date"])
    return items[:MAX_ITEMS]


SOURCES = {
    "Devpost": fetch_devpost,
    "MLH": fetch_mlh,
    "Devfolio": fetch_devfolio,
    "HackerEarth": fetch_hackerearth,
    "lablab.ai": fetch_lablab,
}
