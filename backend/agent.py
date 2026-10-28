from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from google.adk.agents import LlmAgent


HEADERS = {"User-Agent": "Mozilla/5.0"}
GFG_EVENTS_API = (
    "https://practiceapiorigin.geeksforgeeks.org/api/vr/events/"
    "?page_number=1&sub_type=all&type=all"
)
UNSTOP_API = (
    "https://unstop.com/api/public/opportunity/search-result"
    "?opportunity=hackathons&per_page=10&oppstatus=open"
)


def _gfg_events() -> str:
    results = requests.get(GFG_EVENTS_API, timeout=10, headers=HEADERS).json()["results"]
    lines = []
    for e in results.get("upcoming", [])[:10]:
        if e.get("type") == 3:
            link = f"https://practice.geeksforgeeks.org/contest/{e['slug']}"
        else:
            link = f"https://www.geeksforgeeks.org/event/{e['slug']}"
        lines.append(
            f"title: {e.get('title')} | overview: {e.get('overview')} | "
            f"date: {e.get('date')} {e.get('time')} | mode: {e.get('event_mode')} | "
            f"price: {e.get('price')} | link: {link}"
        )
    return "\n".join(lines) or "No upcoming events found."


def _unstop_hackathons() -> str:
    items = requests.get(UNSTOP_API, timeout=10, headers=HEADERS).json()["data"]["data"]
    lines = []
    for o in items[:10]:
        reg = o.get("regnRequirements") or {}
        lines.append(
            f"title: {o.get('title')} | organiser: {(o.get('organisation') or {}).get('name')} | "
            f"registration ends: {reg.get('end_regn_dt')} | {reg.get('remain_days')} | "
            f"team size: {reg.get('min_team_size')}-{reg.get('max_team_size')} | "
            f"link: {o.get('seo_url')}"
        )
    return "\n".join(lines) or "No open hackathons found."


def fetch_page(url: str) -> str:
    """Download a web page and return its visible text followed by the links on it.

    Every returned item carries its own `link` (or a LINKS section), which is the
    only source of item URLs.
    """
    try:
        if url.rstrip("/") == "https://www.geeksforgeeks.org/events":
            return _gfg_events()
        if url.rstrip("/") == "https://unstop.com/hackathons":
            return _unstop_hackathons()

        response = requests.get(url, timeout=10, headers=HEADERS)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()

        links = {}
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"])
            if href.startswith("http") and href not in links:
                links[href] = a.get_text(" ", strip=True)[:80]
        link_lines = "\n".join(f"{text} -> {href}" for href, text in links.items())

        text = soup.get_text(separator=" ", strip=True)[:6000]
        return f"{text}\n\nLINKS:\n{link_lines[:6000]}"
    except Exception as e:
        return f"Error fetching page: {e}"


INSTRUCTION = (
    "You receive an action and a URL. Call fetch_page on the URL. "
    "Extract 3-5 items from the page. "
    "Respond with ONLY valid JSON, no markdown, no extra text, in this shape: "
    "{status: 'success', action, title, items: [...]}. "
    "Each item has: title, subtitle, category, description, date, location, mode, "
    "tags (max 3, array), link. "
    "Use an empty string for anything you can't find. "
    "Each item's link MUST be the specific URL of that item, copied exactly from the "
    "tool output (a `link:` value, or the matching entry in LINKS). "
    "Never use the page URL you were given, never invent or guess a URL, and "
    "never use category or navigation links; use an empty string if there is none. "
    "If anything fails, respond with {status: 'error', items: []}."
)

root_agent = LlmAgent(
    name="web_scraper_agent",
    model="gemini-3.5-flash",
    instruction=INSTRUCTION,
    tools=[fetch_page],
)
