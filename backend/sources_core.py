from datetime import date

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0"}
LIMIT = 15
GFG_EVENTS_API = (
    "https://practiceapiorigin.geeksforgeeks.org/api/vr/events/"
    "?page_number=1&sub_type=all&type=all"
)
UNSTOP_API = (
    "https://unstop.com/api/public/opportunity/search-result"
    f"?opportunity=hackathons&per_page={LIMIT}&oppstatus=open"
)


def html_text(html: str, max_len: int = 400) -> str:
    """Plain text from an HTML fragment, cut to `max_len` characters."""
    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
    return text[:max_len]


def gfg_cost(price, promotional_price) -> str:
    """'paid' if any price is above zero, 'free' if both are zero, else unknown."""
    try:
        price = float(price or 0)
        promotional = float(promotional_price or 0)
    except (TypeError, ValueError):
        return ""
    if price > 0:
        return "paid"
    return "free" if promotional == 0 else ""


def gfg_events() -> list[dict]:
    """Upcoming and ongoing GeeksforGeeks events and contests."""
    results = requests.get(GFG_EVENTS_API, timeout=15, headers=HEADERS).json()["results"]
    items = []
    for e in results.get("upcoming", []):
        if str(e.get("end_time") or "")[:10] < date.today().isoformat():
            continue
        if e.get("type") == 3:
            link = f"https://practice.geeksforgeeks.org/contest/{e['slug']}"
        else:
            link = f"https://www.geeksforgeeks.org/event/{e['slug']}"
        items.append(
            {
                "title": e.get("title") or "",
                "subtitle": e.get("overview") or "",
                "category": e.get("type_text") or "Event",
                "description": html_text(e.get("description")),
                "date": str(e.get("start_time") or "")[:10],
                "location": "Online" if str(e.get("event_mode")) == "1" else "",
                "mode": "online" if str(e.get("event_mode")) == "1" else "",
                "tags": [],
                "cost": gfg_cost(e.get("price"), e.get("promotional_price")),
                "link": link,
            }
        )
    return [item for item in items if item["title"]][:LIMIT]


def unstop_hackathons() -> list[dict]:
    """Open hackathons from Unstop; `date` is the registration deadline."""
    data = requests.get(UNSTOP_API, timeout=15, headers=HEADERS).json()["data"]["data"]
    items = []
    for o in data:
        reg = o.get("regnRequirements") or {}
        deadline = str(reg.get("end_regn_dt") or "")[:10]
        address = o.get("address_with_country_logo")
        address = address.get("address") if isinstance(address, dict) else address
        organiser = (o.get("organisation") or {}).get("name") or ""
        skills = [s.get("skill_name") for s in o.get("required_skills") or []]
        description = html_text(o.get("details"), 300)
        if deadline:
            description = f"Registration closes {deadline}. {description}".strip()
        items.append(
            {
                "title": o.get("title") or "",
                "subtitle": organiser,
                "category": "Hackathon",
                "description": description,
                "date": deadline,
                "location": str(address or "")[:80],
                "mode": o.get("region") or "",
                "tags": [s.lower() for s in skills if s][:5],
                "cost": "paid" if o.get("isPaid") else "free",
                "link": o.get("seo_url") or "",
            }
        )
    return [item for item in items if item["title"] and item["link"]][:LIMIT]
