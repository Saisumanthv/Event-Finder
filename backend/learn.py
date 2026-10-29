"""Learn with AI: explanations, examples, code and a mini project for the subtopics a learner picks."""

import asyncio
import hashlib
import json
import re
from collections import OrderedDict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent import build_lesson

router = APIRouter()

LESSON_CACHE_SIZE = 300
lesson_cache: "OrderedDict[str, dict]" = OrderedDict()
lesson_inflight: dict[str, asyncio.Task] = {}

URL_TOKEN = re.compile(r"\S*(?:https?://|www\.)\S*", re.IGNORECASE)
LANGUAGE = re.compile(r"^[a-z0-9+#]{1,15}$")


class LearnRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=100)
    skill: str = Field("", max_length=100)
    hackathon: str = Field("", max_length=200)
    subtopics: list[str] = Field(min_length=1, max_length=6)


def text_line(value, max_len: int) -> str:
    """Plain text on one line with any link removed; empty if it is not text."""
    if not isinstance(value, str):
        return ""
    return " ".join(URL_TOKEN.sub("", value).split())[:max_len]


def clean_list(value, count: int, max_len: int) -> list[str]:
    items = value if isinstance(value, list) else []
    return [t for t in (text_line(i, max_len) for i in items) if t][:count]


def clean_code(value) -> dict | None:
    """A code snippet is shown as plain text in a code block, so its line breaks are kept."""
    if not isinstance(value, dict) or not isinstance(value.get("snippet"), str):
        return None
    snippet = value["snippet"].strip()
    snippet = re.sub(r"^```[\w+#-]*\n?|\n?```$", "", snippet).strip("\n")
    lines = snippet.splitlines()[:40]
    snippet = "\n".join(line.rstrip()[:160] for line in lines)
    if not snippet.strip():
        return None
    language = str(value.get("language", "")).strip().lower()
    return {"language": language if LANGUAGE.match(language) else "text", "snippet": snippet}


def clean_lesson(raw: dict, subtopics: list[str]) -> dict:
    """Keep well-formed lessons only, labelled with the subtopic names the learner picked."""
    lessons = []
    for index, entry in enumerate(raw.get("lessons", [])[: len(subtopics)]):
        if not isinstance(entry, dict):
            continue
        explanation = text_line(entry.get("explanation"), 900)
        if not explanation:
            continue
        lessons.append(
            {
                "subtopic": subtopics[index],
                "explanation": explanation,
                "examples": clean_list(entry.get("examples"), 3, 250),
                "code": clean_code(entry.get("code")),
            }
        )

    project = raw.get("mini_project")
    mini_project = None
    if isinstance(project, dict):
        title, idea = text_line(project.get("title"), 80), text_line(project.get("idea"), 400)
        if title and idea:
            mini_project = {"title": title, "idea": idea, "steps": clean_list(project.get("steps"), 5, 150)}
    return {"lessons": lessons, "mini_project": mini_project}


async def generate(body: LearnRequest, subtopics: list[str], key: str) -> dict:
    raw = await asyncio.wait_for(build_lesson(body.topic, body.skill, body.hackathon, subtopics), timeout=120)
    result = clean_lesson(raw, subtopics)
    if not result["lessons"]:
        raise ValueError("the model returned no usable lessons")
    while len(lesson_cache) >= LESSON_CACHE_SIZE:
        lesson_cache.popitem(last=False)
    lesson_cache[key] = result
    return result


@router.post("/learn")
async def learn(body: LearnRequest):
    subtopics = [t for t in (text_line(s, 100) for s in body.subtopics) if t]
    if not subtopics:
        raise HTTPException(status_code=400, detail="Pick at least one subtopic")
    key = hashlib.sha1(
        json.dumps([body.topic, body.skill, body.hackathon, subtopics]).encode()
    ).hexdigest()
    if key in lesson_cache:
        lesson_cache.move_to_end(key)
        return lesson_cache[key]

    # The same request made twice at once shares one model call.
    task = lesson_inflight.get(key)
    if task is None:
        task = asyncio.create_task(generate(body, subtopics, key))
        lesson_inflight[key] = task
        task.add_done_callback(lambda _done: lesson_inflight.pop(key, None))
    try:
        return await asyncio.shield(task)
    except Exception as e:
        print(f"learn failed: {e!r}")
        raise HTTPException(status_code=502, detail="The AI could not write this lesson. Please try again.")
