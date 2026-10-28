# Draft-Generator-Agent

A web page with three buttons. Each click asks a Google ADK agent (backend) to read a website and return events, hackathons or courses, which the React frontend shows as cards.

## Setup

### Backend

```bash
cd backend
uv sync
copy .env.example .env     # Windows
# cp .env.example .env     # macOS/Linux
```

Edit `.env` and add a Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey).

```bash
uv run uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install && npm run dev
```

Open http://localhost:5173.

## Buttons

| Button | action sent | Source |
|---|---|---|
| Get Event Details | `event_details` | https://www.geeksforgeeks.org/events |
| Get Hackathon Details | `hackathon_details` | https://unstop.com/hackathons |
| New Courses | `new_courses` | https://www.geeksforgeeks.org/courses |

Each click sends `POST http://localhost:8000/button-action` with JSON:

```json
{ "action": "event_details", "actionLabel": "Get Event Details" }
```

An unknown `action` returns HTTP 400. The response is JSON of the form `{ "status": "success", "action", "title", "items": [...] }`, or `{ "status": "error", "items": [] }` on failure.
