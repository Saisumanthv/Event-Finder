import json
import uuid

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from agent import root_agent

APP_NAME = "draft_generator_agent"
USER_ID = "user"

ACTION_URLS = {
    "event_details": "https://www.geeksforgeeks.org/events",
    "hackathon_details": "https://unstop.com/hackathons",
    "new_courses": "https://www.geeksforgeeks.org/courses",
}

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


async def run_agent(action: str, url: str) -> str:
    session_service = InMemorySessionService()
    session_id = str(uuid.uuid4())
    await session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id
    )
    runner = Runner(
        agent=root_agent, app_name=APP_NAME, session_service=session_service
    )
    message = types.Content(
        role="user", parts=[types.Part(text=f"action: {action}, url: {url}")]
    )

    final_text = ""
    async for event in runner.run_async(
        user_id=USER_ID, session_id=session_id, new_message=message
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = "".join(p.text or "" for p in event.content.parts)
    return final_text


@app.post("/button-action")
async def button_action(body: ButtonAction):
    url = ACTION_URLS.get(body.action)
    if not url:
        raise HTTPException(status_code=400, detail="Unknown action")

    try:
        text = await run_agent(body.action, url)
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        return json.loads(text)
    except Exception as e:
        print(f"button_action failed: {e!r}")
        return {"status": "error", "message": str(e)[:300], "items": []}
