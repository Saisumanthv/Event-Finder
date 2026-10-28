"""Course source adapters: each fetch_* returns up to 15 normalized course dicts."""

import json
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

MAX_ITEMS = 15
TIMEOUT = 15
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en",
}


def _get(url: str) -> requests.Response:
    """GET a URL with the shared headers and timeout, raising on HTTP errors."""
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response


def _text(value: str | None, limit: int = 400) -> str:
    """Strip HTML and collapse whitespace, truncating to limit characters."""
    plain = BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)
    plain = re.sub(r"\s+", " ", plain)
    return plain if len(plain) <= limit else plain[: limit - 1].rstrip() + "…"


def _tags(values: list[str]) -> list[str]:
    """Return up to five unique short lowercase tags."""
    tags: list[str] = []
    for value in values:
        tag = value.strip().lower()
        if tag and len(tag) <= 30 and tag not in tags:
            tags.append(tag)
    return tags[:5]


def _item(
    title: str,
    link: str,
    subtitle: str = "",
    category: str = "",
    description: str = "",
    tags: list[str] | None = None,
    cost: str = "",
) -> dict:
    """Build a normalized course dict."""
    return {
        "title": _text(title, 200),
        "subtitle": _text(subtitle, 120),
        "category": _text(category, 60),
        "description": _text(description),
        "date": "",
        "location": "",
        "mode": "online",
        "tags": _tags(tags or []),
        "cost": cost,
        "link": link,
    }


def fetch_geeksforgeeks() -> list[dict]:
    """GeeksforGeeks course cards parsed from the server-rendered /courses page."""
    base = "https://www.geeksforgeeks.org/courses"
    soup = BeautifulSoup(_get(base).text, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for anchor in soup.select('a[href*="/courses/"]'):
        link = urljoin(base + "/", anchor["href"]).split("?")[0].split("#")[0]
        path = link.removeprefix("https://www.geeksforgeeks.org")
        if not path.startswith("/courses/") or path.startswith("/courses/category/"):
            continue
        if link in seen:
            continue
        text = anchor.get_text(" ", strip=True)
        match = re.search(
            r"(?:\d[\d.]*k?\+ interested Geeks\s+)?(?:\d\.\d\s+)?(?P<title>.+?)\s+"
            r"(?P<level>Beginner to Advanced|Beginner|Intermediate and Advanced|Intermediate|Advanced)",
            text,
        )
        if not match:
            continue
        seen.add(link)
        title = match["title"]
        price = re.search(r"₹\s*([\d,]+)", text)
        free = re.search(r"\bfree\b", text, re.I)
        cost = "paid" if price and price.group(1) != "0" else "free" if free else ""
        items.append(
            _item(
                title,
                link,
                subtitle=f"{match['level']} · GeeksforGeeks",
                tags=[match["level"]] + re.split(r"\s*[-&,/]\s*", title)[1:3],
                cost=cost,
            )
        )
        if len(items) == MAX_ITEMS:
            break
    return items


def fetch_coursera() -> list[dict]:
    """Coursera product cards from the public, robots-allowed /courses page."""
    base = "https://www.coursera.org"
    soup = BeautifulSoup(_get(f"{base}/courses").text, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for card in soup.select(".cds-ProductCard-content"):
        anchor = card.select_one("a.cds-CommonCard-titleLink")
        heading = card.select_one("h3")
        if not anchor or not heading:
            continue
        link = urljoin(base, anchor["href"])
        if link in seen:
            continue
        seen.add(link)
        partner = card.select_one(".cds-ProductCard-partnerNames")
        skills_node = card.select_one(".cds-ProductCard-body p")
        skills = [s.strip() for s in skills_node.get_text().split(":", 1)[-1].split(",")] if skills_node else []
        kind = link.split("/")[3].replace("-", " ").rstrip("s") if link.count("/") > 3 else ""
        level = re.search(r"(Beginner|Intermediate|Advanced|Mixed)", card.get_text(" ", strip=True))
        subtitle = " · ".join(p for p in (partner.get_text(strip=True) if partner else "", level.group(1) if level else "", kind) if p)
        items.append(
            _item(
                heading.get_text(strip=True),
                link,
                subtitle=subtitle,
                description=("Skills you'll gain: " + ", ".join(skills)) if skills else "",
                tags=skills,
            )
        )
        if len(items) == MAX_ITEMS:
            break
    return items


def fetch_microsoft_learn() -> list[dict]:
    """Microsoft Learn learning paths from the public catalog API, most popular first."""
    data = _get("https://learn.microsoft.com/api/catalog/?type=learningPaths&locale=en-us").json()
    paths = sorted(data.get("learningPaths", []), key=lambda p: -p.get("popularity", 0))
    items: list[dict] = []
    for path in paths:
        link = (path.get("url") or "").split("?")[0]
        if not path.get("title") or not link:
            continue
        levels = ", ".join(level.title() for level in path.get("levels", []))
        minutes = path.get("duration_in_minutes")
        duration = f"{minutes // 60}h {minutes % 60}m" if minutes else ""
        subtitle = " · ".join(p for p in ("Microsoft Learn", levels, duration) if p)
        products = [p.replace("-", " ") for p in path.get("products", [])]
        items.append(
            _item(
                path["title"],
                link,
                subtitle=subtitle,
                category=products[0] if products else "",
                description=path.get("summary", ""),
                tags=products + path.get("roles", []),
                cost="free",
            )
        )
        if len(items) == MAX_ITEMS:
            break
    return items


def fetch_edx() -> list[dict]:
    """edX course cards parsed from the server-rendered /search page."""
    base = "https://www.edx.org"
    soup = BeautifulSoup(_get(f"{base}/search").text, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for anchor in soup.select('a[href^="/learn/"]'):
        parts = [p for p in anchor.stripped_strings]
        link = urljoin(base, anchor["href"])
        slug = anchor["href"].strip("/").split("/")
        if len(parts) < 2 or link in seen or len(slug) < 3:
            continue
        seen.add(link)
        subject = slug[1].replace("-", " ")
        provider = parts[1] if len(parts) > 2 else ""
        items.append(
            _item(
                parts[0],
                link,
                subtitle=" · ".join(p for p in (provider, "edX") if p),
                category=subject.title(),
                tags=[subject, provider],
            )
        )
        if len(items) == MAX_ITEMS:
            break
    return items


def fetch_class_central() -> list[dict]:
    """Class Central course listing parsed from the Computer Science subject page."""
    base = "https://www.classcentral.com"
    soup = BeautifulSoup(_get(f"{base}/subject/cs").text, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for anchor in soup.select("a.course-name[data-track-props]"):
        try:
            props = json.loads(anchor["data-track-props"])
        except ValueError:
            continue
        link = urljoin(base, anchor["href"]).split("?")[0]
        title = anchor.get_text(" ", strip=True)
        if not title or link in seen:
            continue
        seen.add(link)
        provider = props.get("course_provider") or ""
        who = props.get("course_institution") or provider
        level = props.get("course_level") or ""
        subtitle = " · ".join(p for p in (who, provider if who != provider else "", level.title()) if p)
        subject = props.get("course_subject") or ""
        items.append(
            _item(
                title,
                link,
                subtitle=subtitle,
                category=subject,
                tags=[subject, provider, level, props.get("course_format") or ""],
                cost="free" if props.get("course_is_free") else "paid",
            )
        )
        if len(items) == MAX_ITEMS:
            break
    return items


SOURCES = {
    "GeeksforGeeks Courses": fetch_geeksforgeeks,
    "Coursera": fetch_coursera,
    "Microsoft Learn": fetch_microsoft_learn,
    "edX": fetch_edx,
    "Class Central": fetch_class_central,
}
