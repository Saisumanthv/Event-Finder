"""Event source adapters: tech conferences, meetups and community events."""

import json
import re
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 15
MAX_ITEMS = 15

CONFS_BASE = "https://raw.githubusercontent.com/tech-conferences/conference-data/main/conferences"
CONFS_TOPICS = ["general", "javascript", "python", "data", "devops", "security", "typescript", "java"]
MEETUP_URL = "https://www.meetup.com/find/?keywords=technology&source=EVENTS"
LUMA_URL = "https://luma.com/bengaluru"
HASGEEK_URL = "https://hasgeek.com/"


def _get(url: str) -> requests.Response:
    """GET a URL with a browser-like User-Agent and raise on HTTP errors."""
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp


def _clean(text: str | None, limit: int = 400) -> str:
    """Strip HTML and markdown noise, collapse whitespace and trim to limit."""
    text = BeautifulSoup(text or "", "html.parser").get_text(" ")
    text = re.sub(r"[*_#>`]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _next_data(html: str) -> dict:
    """Parse the __NEXT_DATA__ JSON blob from a Next.js page."""
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise ValueError("__NEXT_DATA__ not found")
    return json.loads(match.group(1))


def _is_upcoming(day: str) -> bool:
    """True when a YYYY-MM-DD date is today or later."""
    return day >= date.today().isoformat()


def _tags(*values: str | None) -> list[str]:
    """Lowercase, dedupe and cap tag candidates at five."""
    out: list[str] = []
    for value in values:
        tag = (value or "").strip().lower()
        if tag and len(tag) <= 30 and tag not in out:
            out.append(tag)
    return out[:5]


def _item(
    title: str,
    link: str,
    *,
    subtitle: str = "",
    category: str = "",
    description: str = "",
    day: str = "",
    location: str = "",
    mode: str = "",
    tags: list[str] | None = None,
    cost: str = "",
) -> dict:
    """Build an event dict with the shared schema."""
    return {
        "title": _clean(title, 200),
        "subtitle": _clean(subtitle, 120),
        "category": category,
        "description": _clean(description),
        "date": day,
        "location": _clean(location, 120),
        "mode": mode,
        "tags": tags or [],
        "cost": cost,
        "link": link,
    }


def fetch_confs_tech() -> list[dict]:
    """Upcoming conferences from the open confs.tech JSON data on GitHub."""
    year = date.today().year
    seen: set[str] = set()
    rows: list[tuple[str, str, dict]] = []
    for topic in CONFS_TOPICS:
        for y in (year, year + 1):
            resp = requests.get(
                f"{CONFS_BASE}/{y}/{topic}.json", headers={"User-Agent": UA}, timeout=TIMEOUT
            )
            if resp.status_code != 200:
                continue
            for conf in resp.json():
                start = conf.get("startDate", "")
                end = conf.get("endDate") or start
                url = conf.get("url", "")
                if not (conf.get("name") and url and start) or not _is_upcoming(start) or url in seen:
                    continue
                seen.add(url)
                rows.append((start, topic, conf))
    rows.sort(key=lambda r: r[0])
    items = []
    for start, topic, conf in rows[:MAX_ITEMS]:
        city = ", ".join(p for p in (conf.get("city"), conf.get("country")) if p)
        online = bool(conf.get("online"))
        place = "Online" if online and not city else city
        end = conf.get("endDate") or start
        desc = f"{topic.title()} conference in {place or 'TBA'}, {start} to {end}."
        if conf.get("cfpUrl") and conf.get("cfpEndDate") and _is_upcoming(conf["cfpEndDate"]):
            desc += f" Call for papers open until {conf['cfpEndDate']}."
        items.append(
            _item(
                conf["name"],
                conf["url"],
                category="Conference",
                description=desc,
                day=start,
                location=place,
                mode="hybrid" if online and city else "online" if online else "offline",
                tags=_tags(topic, "conference", conf.get("country")),
            )
        )
    return items


def fetch_meetup() -> list[dict]:
    """Upcoming tech events from the Apollo state embedded in Meetup's find page."""
    state = _next_data(_get(MEETUP_URL).text)["props"]["pageProps"]["__APOLLO_STATE__"]
    modes = {"PHYSICAL": "offline", "ONLINE": "online", "HYBRID": "hybrid"}
    items = []
    for value in state.values():
        if value.get("__typename") != "Event" or not value.get("title"):
            continue
        link = value.get("eventUrl") or ""
        day = (value.get("dateTime") or "")[:10]
        if not link or not day or not _is_upcoming(day):
            continue
        group = state.get(value.get("group", {}).get("__ref", ""), {})
        venue = value.get("venue") or {}
        mode = modes.get(value.get("eventType") or "", "")
        fee = value.get("feeSettings")
        cost = "free" if fee is None else "paid" if (fee.get("amount") or 0) > 0 else "free"
        items.append(
            _item(
                value["title"],
                link,
                subtitle=group.get("name", ""),
                category="Meetup",
                description=value.get("description", ""),
                day=day,
                location="Online" if mode == "online" else venue.get("city") or venue.get("name") or "",
                mode=mode,
                tags=_tags("meetup", group.get("urlname")),
                cost=cost,
            )
        )
    items.sort(key=lambda i: i["date"])
    return items[:MAX_ITEMS]


def _luma_blurb(calendar: dict) -> str:
    """Calendar blurb for a Luma event, ignoring auto-generated placeholders."""
    blurb = calendar.get("description_short") or ""
    return "" if calendar.get("is_personal") or blurb.startswith("Events calendar of") else blurb


def fetch_luma() -> list[dict]:
    """Upcoming Bengaluru events from the Next.js data on a Luma discover page."""
    data = _next_data(_get(LUMA_URL).text)["props"]["pageProps"]["initialData"]["data"]
    items = []
    for entry in data.get("events", []):
        event = entry.get("event", {})
        day = (entry.get("start_at") or event.get("start_at") or "")[:10]
        slug = event.get("url")
        if not (event.get("name") and slug and day) or not _is_upcoming(day):
            continue
        info = entry.get("ticket_info") or {}
        geo = event.get("geo_address_info") or {}
        online = event.get("location_type") == "online"
        hosts = [h.get("name") for h in entry.get("hosts") or [] if h.get("name")]
        calendar = entry.get("calendar") or {}
        items.append(
            _item(
                event["name"],
                f"https://luma.com/{slug}",
                subtitle=", ".join(hosts[:2]),
                category="Meetup",
                description=_luma_blurb(calendar),
                day=day,
                location="Online" if online else geo.get("city_state") or geo.get("city") or "",
                mode="online" if online else "offline" if event.get("location_type") else "",
                tags=_tags("luma", geo.get("city")),
                cost="" if "is_free" not in info else "free" if info["is_free"] else "paid",
            )
        )
    items.sort(key=lambda i: i["date"])
    return items[:MAX_ITEMS]


def fetch_hasgeek() -> list[dict]:
    """Upcoming events parsed from the HTML cards on the HasGeek home page."""
    soup = BeautifulSoup(_get(HASGEEK_URL).text, "html.parser")
    items = []
    for card in soup.select("ul.upcoming a.card"):
        href = card.get("href", "").strip()
        label = card.select_one(".card__body [aria-label]")
        title = card.select_one(".card__body__title")
        if not (href and title and label) or "cfp" in href:
            continue
        match = re.match(r"(\d{1,2})(?:\D+\d{1,2})?\s+([A-Za-z]{3})\s+(\d{4})", label["aria-label"])
        if not match:
            continue
        day = datetime.strptime(" ".join(match.groups()), "%d %b %Y").date().isoformat()
        if not _is_upcoming(day):
            continue
        bold = title.select_one(".text-bold")
        light = title.select_one(".mui--text-light")
        place = card.select_one(".card__body__location")
        organiser = card.select_one(".user + span, .mui--text-subhead.text-bold")
        location = place.get_text(" ", strip=True) if place else ""
        items.append(
            _item(
                (bold.get_text(" ", strip=True) if bold else title.get_text(" ", strip=True)).rstrip(":"),
                "https://hasgeek.com" + href,
                subtitle=organiser.get_text(" ", strip=True) if organiser else "",
                category="Meetup",
                description=light.get_text(" ", strip=True) if light else "",
                day=day,
                location=location,
                mode="hybrid" if "online" in location.lower() else "offline",
                tags=_tags("hasgeek", href.strip("/").split("/")[0]),
            )
        )
    return items[:MAX_ITEMS]


SOURCES = {
    "Confs.tech": fetch_confs_tech,
    "Meetup": fetch_meetup,
    "Luma": fetch_luma,
    "HasGeek": fetch_hasgeek,
}
