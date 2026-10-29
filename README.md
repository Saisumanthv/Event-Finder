# Event Finder

Find events, hackathons and courses to showcase your skills or learn new ones.

Event Finder collects listings from 15 websites, then lets you ask a tool-using AI assistant, build a learning path, or browse and filter by hand. Results are split into **Free** and **Paid** sections, and every card links to the original page.

- **Frontend:** React 19 + Vite
- **Backend:** FastAPI (Python 3.12+), Google ADK and the Gemini API

## What you can do

| Control | What it does |
|---|---|
| **Ask the assistant** | Type a request such as *"I know Python and want a hackathon for my portfolio in the next two weeks"*. A tool-calling agent searches the listings (and the web if needed), then answers with picked cards, a one-line reason for each, and a collapsible list of the steps it took. |
| **Build my path** | Type a goal such as *"get into machine learning"*. You get an ordered path of courses, hackathons and events, with a sentence on why each step comes next. |
| **Browse and filter manually** | Collapsed by default. Fill in a profile (skills, interests, level, mode, location) and click **Get Event Details**, **Get Hackathon Details** or **New Courses**. With an empty profile you get everything; with a profile you get only what fits, each with a reason. The profile is saved in your browser (`localStorage`). |

Every card shows a summary, a difficulty label, prerequisites, tags, cost, mode, date and its source website. While a request runs, the loading ring shows the elapsed time in seconds.

## How it works

```
 15 websites ──► adapters (plain Python) ──► merge, de-duplicate, ids
                                                   │
                                      Gemini enriches each card
                          (summary, difficulty, prerequisites, tags)
                                                   │
                                  in-memory cache + backend/cache.json
                                                   │
        ┌──────────────────┬───────────────────────┼──────────────────┐
     /assistant        /learning-path         /recommend         /button-action
   (agent + tools)     (path planner)         (ranker)           (plain listing)
```

1. **Adapters collect the data.** Each website has a small function in `backend/sources_*.py` that returns cards in one schema. This is ordinary Python (`requests` and `BeautifulSoup`); the model is not involved. Sources are fetched in parallel, and one failing source never breaks the rest.
2. **Gemini enriches the cards** in batches of 20 by adding `summary`, `difficulty`, `prerequisites` and `tags`.
3. **A cache keeps it fast.** Items are cached in memory and saved to `backend/cache.json`. The cache is warmed at startup, shared between concurrent requests, and refreshed in the background when older than 10 minutes. Stale data is served immediately while the refresh runs.
4. **Features read from the cache.** Plain code filters, groups and sorts. The model is used only for language tasks.

### What the model does, and what it never does

| Task | Where | Model returns |
|---|---|---|
| Enrich cards | `enrich_items` | summary, difficulty, prerequisites, tags |
| Rank for a profile | `recommend_items` | item ids in order, plus a short reason each |
| Turn a sentence into filters | `parse_search_query` | a filter object (type, mode, level, cost, dates, keywords) |
| Plan a learning path | `plan_learning_path` | ordered item ids, a reason for each, an overview sentence |
| Search the web | `search_web` | structured items, from a Google-grounded search |
| Assistant | `build_assistant` | decides which tools to call, then picks item ids and writes the answer |

**The model never writes titles, dates, prices or links.** Every one of those comes from the source data. Code validates everything the model returns: unknown or duplicate ids are dropped, URLs are stripped from its text, and values outside the allowed sets are ignored. For web search, links come only from the pages Google cites, never from the model's text. If the model fails, each feature falls back to plain code (keyword matching or simple ordering) and the response is flagged `"fallback": true`, instead of returning an error.

### The assistant's tools

The assistant (`backend/agent.py` and the tools in `backend/main.py`) is a Google ADK `LlmAgent` that chooses its own tool calls in a loop:

| Tool | Purpose |
|---|---|
| `search_listings` | Filter the listings by type, mode, level, cost, dates and keywords |
| `get_details` | Look at one item's description, prerequisites and tags |
| `build_learning_path` | Call the path planner for a plan or roadmap |
| `search_the_web` | Google-grounded search, used when the listings do not cover the request |

It can pick only item ids that its tools returned. It gets at most 6 tool calls and 10 model calls per request, enforced in code.

## Data sources

15 websites are live. Each is checked against its `robots.txt` and uses a public API or page data; none needs a key.

| Category | Websites |
|---|---|
| Events | GeeksforGeeks, Confs.tech, Meetup, Luma, HasGeek |
| Hackathons | Unstop, Devpost, MLH, Devfolio, HackerEarth, lablab.ai |
| Courses | GeeksforGeeks, Coursera, Microsoft Learn, edX, Class Central |

These were evaluated and **not** added, and nothing tries to get around their blocks:

| Site | Why |
|---|---|
| Eventbrite | `robots.txt` disallows all automated access |
| GDG, Commudle, Townscript, Hack2skill, DoraHacks, freeCodeCamp, NPTEL | JavaScript-only pages with no usable data or public API |
| Kaggle (competitions and Learn) | Blocks bots behind a CAPTCHA |
| Udemy | Blocked by Cloudflare |
| Google Cloud Skills Boost | `robots.txt` unreadable and a JavaScript-only catalog |

### Cost and the Free / Paid sections

`cost` is `free` or `paid` only when the source states it (for example Unstop's `isPaid` flag, or a price on a course card). Items with no stated price go into a third **Price not listed** group, so they are never wrongly shown as free or paid. Meetup events with no fee information are treated as free, which is an inference.

## Setup

**Requirements:** Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js and a Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey).

### Backend

```bash
cd backend
uv sync
copy .env.example .env     # Windows
# cp .env.example .env     # macOS/Linux
```

Open `.env` and set:

```
GOOGLE_API_KEY=your-key-here
```

Then start the server:

```bash
uv run uvicorn main:app --reload --port 8000
```

The first start scrapes and enriches all sources, which takes about a minute. The result is saved to `backend/cache.json`, so later starts are fast. Delete that file to force a full refresh.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Locally the frontend calls the backend at `http://127.0.0.1:8000`, and the backend accepts requests from `http://localhost:5173`. Both can be changed for deployment (see below).

## Deployment (Render)

Deploy the backend first, because the frontend needs its address.

**1. Backend: Web Service.** Root Directory `backend/`.

| Setting | Value |
|---|---|
| Build Command | `pip install uv && uv sync --frozen` |
| Start Command | `uv run uvicorn main:app --host 0.0.0.0 --port $PORT` |
| `GOOGLE_API_KEY` | your Gemini key |
| `PYTHON_VERSION` | `3.12.8` |

**2. Frontend: Static Site.** Root Directory `frontend`.

| Setting | Value |
|---|---|
| Build Command | `npm install && npm run build` |
| Publish Directory | `dist` |
| `VITE_API_URL` | the backend's URL, such as `https://your-backend.onrender.com` |

`VITE_API_URL` is baked into the build, so after changing it you must redeploy the frontend.

**3. Back on the backend,** add the environment variable `ALLOWED_ORIGINS` set to the frontend's URL, such as `https://your-frontend.onrender.com`. Without it the browser blocks every request (CORS). Use several comma-separated addresses if you have more than one.

The disk on Render is temporary, so `cache.json` is lost on each deploy or restart and the next start takes about a minute and makes about 10 to 15 Gemini calls. Free instances also sleep when idle.

## API

All endpoints are `POST` with a JSON body. Items share one schema: `id`, `title`, `subtitle`, `category`, `description`, `date`, `location`, `mode`, `tags`, `cost`, `link`, `source`, `summary`, `difficulty`, `prerequisites`.

| Endpoint | Request | Response |
|---|---|---|
| `/assistant` | `{ "message": "..." }` (up to 500 characters) | `{ "answer", "steps": [{ "tool", "detail", "result" }], "cards": [...with "reason"], "fallback" }` |
| `/learning-path` | `{ "goal": "...", "max_steps": 4 }` (2 to 6) | `{ "overview", "path": [...with "step", "type", "why_next"], "fallback" }` |
| `/recommend` | `{ "profile": { "skills", "level", "interests", "mode_preference", "location" }, "limit": 5, "action": "event_details" }` (`action` optional, `limit` 1 to 100) | `{ "recommendations": [...with "rank", "reason"], "fallback" }` |
| `/button-action` | `{ "action": "event_details" }` (or `hackathon_details`, `new_courses`) | `{ "status": "success", "action", "items": [...] }`, or `{ "status": "error", "message", "items": [] }` |
| `/search` | `{ "query": "free weekend hackathons, online" }` | `{ "filters": {...}, "results": [...], "fallback" }` |
| `/web-search` | `{ "query": "..." }` | `{ "results": [...] }`, or an empty list with a `message` |

An unknown `action` or an empty text field returns HTTP 400. `/search` and `/web-search` are not used by the current page (the assistant covers both) but still work.

Example:

```bash
curl -X POST http://127.0.0.1:8000/assistant \
  -H "Content-Type: application/json" \
  -d '{"message":"free online courses to learn MERN stack"}'
```

## Project structure

```
backend/
  main.py               API endpoints, caching, filtering, validation, assistant tools
  agent.py              Gemini agents, prompts and the assistant runner
  sources.py            registry: which adapters feed each category
  sources_core.py       GeeksforGeeks events and Unstop adapters
  sources_events.py     Confs.tech, Meetup, Luma, HasGeek
  sources_hackathons.py Devpost, MLH, Devfolio, HackerEarth, lablab.ai
  sources_courses.py    GeeksforGeeks, Coursera, Microsoft Learn, edX, Class Central
  cache.json            generated cache (git-ignored)
  pyproject.toml, uv.lock, .env.example
frontend/
  src/components/
    WebhookDraft.jsx    main page and all requests
    SearchBox.jsx       text box used by the assistant and the path builder
    ProfileForm.jsx     profile fields, saved in localStorage
    Card.jsx            one result card
    CardSections.jsx    Free / Paid / Price not listed grouping
    Loader.jsx          spinner with elapsed seconds
  src/config.js         backend URLs
  src/styles/WebhookDraft.css
```

## Configuration

| Setting | Where | Default |
|---|---|---|
| Gemini model | `MODEL` in `backend/agent.py` | `gemini-3.5-flash` |
| Assistant tool-call limit | `MAX_TOOL_CALLS` in `backend/agent.py` | 6 |
| Cache lifetime before background refresh | `CACHE_TTL_SECONDS` in `backend/main.py` | 600 (10 minutes) |
| Cards enriched per model call | `ENRICH_CHUNK_SIZE` in `backend/main.py` | 20 |
| Items taken per website | `LIMIT` in `sources_core.py`, `MAX_ITEMS` in the other `sources_*.py` files | 15 |
| Assistant time limit | `timeout` in the `/assistant` endpoint | 240 seconds |
| Frontend addresses allowed to call the API | `ALLOWED_ORIGINS` environment variable | `http://localhost:5173` |
| Backend address used by the frontend | `VITE_API_URL` at build time | `http://127.0.0.1:8000` |

## Adding a source

1. Write a function in the right `sources_*.py` that returns a list of dicts with the keys `title`, `subtitle`, `category`, `description`, `date` (`YYYY-MM-DD` or empty), `location`, `mode` (`online`, `offline`, `hybrid` or empty), `tags`, `cost` (`free`, `paid` or empty) and `link` (the specific item page).
2. Add it to that file's `SOURCES` dictionary. Nothing else changes: enrichment, caching, filtering and the assistant pick it up automatically.

## Limitations and notes

- **Slow first load.** A cold start takes about a minute. Assistant requests take roughly 15 seconds when the listings are enough, and about a minute when they use web search.
- **Model cost.** Enrichment, the assistant and web search all call Gemini. A refresh of all categories makes roughly 10 to 15 calls, and one assistant request typically makes several more.
- **Generated fields are estimates.** `difficulty`, `summary`, `prerequisites` and `tags` come from the model and can be wrong. For example, a course the site calls "Beginner to Advanced" may be labelled "intermediate".
- **Web search is less reliable.** Results come from Google's cited pages, so dates and prices can be missing or off, and links are checked only loosely against the title. The page marks them as coming from the web.
- **Fragile adapters.** Meetup, Luma, HasGeek, Devfolio, Class Central, edX and MLH read page structure or undocumented endpoints, so a site redesign can break them. A broken adapter is skipped and logged; the rest keep working.
- **Source quirks.** Luma shows Bengaluru only, and Meetup's results follow the server's location. Devpost's `date` is the start date, which has already passed for events that are currently open. For Unstop, `date` is the registration deadline. HackerEarth and lablab.ai currently list only a couple of events.
- **Terms of service.** The adapters follow each site's `robots.txt`, but sites such as Coursera, edX, Class Central and Meetup may restrict scraping in their terms. lablab.ai's `robots.txt` asks that its content not be used for AI training. Check them before using this beyond personal use.
- **No automated tests are included in the repository.**
