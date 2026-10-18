"""
Chatbot endpoint. Runs a tool-call loop against Gemini:

  POST /api/chatbot/message      → send message, receive reply
  GET  /api/chatbot/session/{id} → fetch history
  DEL  /api/chatbot/session/{id} → clear history

Session history is kept in an in-memory dict for v1 (24h TTL, 1000 sessions LRU).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException

from core import chatbot_tools, llm
from core.config import settings
from core.access import COST_VIEW_ROLES, actor, patient_scope
from schemas.chatbot import ChatRequest, ChatResponse, ToolCallLog

logger = logging.getLogger("chatbot.router")

router = APIRouter(prefix="/chatbot", tags=["chatbot"])

# Which framing the assistant leads with, decided by the signed-in account - not
# by the request, which anyone can edit. Roles that own the budget lead with
# cost; everyone else leads with the patient. The same roles as the cost
# screens, so the assistant never leads with figures its cost tools then refuse.
_COST_FIRST_ROLES = set(COST_VIEW_ROLES)


def _audience(user: dict) -> str:
    return "insurer" if user["role"] in _COST_FIRST_ROLES else "case_manager"

_MAX_SESSIONS = 1000
_SESSION_TTL_SECONDS = 60 * 60 * 24
_MAX_HISTORY_MESSAGES = 20
_MAX_TOOL_ITERATIONS = 5

_SESSIONS: "OrderedDict[str, dict]" = OrderedDict()


def _evict_expired() -> None:
    now = time.monotonic()
    stale = [sid for sid, s in _SESSIONS.items() if now - s["touched"] > _SESSION_TTL_SECONDS]
    for sid in stale:
        _SESSIONS.pop(sid, None)
    while len(_SESSIONS) > _MAX_SESSIONS:
        _SESSIONS.popitem(last=False)


def _touch(session_id: str, owner: str) -> dict:
    """A conversation belongs to the account that started it. Its messages can
    carry patient details, so another account presenting the same id gets
    "not found", exactly as if it had never existed."""
    session = _SESSIONS.get(session_id)
    if session is None:
        session = {"messages": [], "touched": time.monotonic(), "owner": owner}
        _SESSIONS[session_id] = session
    elif session.get("owner") != owner:
        raise HTTPException(status_code=404, detail="Session not found.")
    else:
        session["touched"] = time.monotonic()
        _SESSIONS.move_to_end(session_id)
    return session


def _trim(messages: list[dict]) -> list[dict]:
    """Keep about the last _MAX_HISTORY_MESSAGES, but only ever cut just before
    one of the user's questions. Cutting inside a tool round (a tool result
    without the request it answers) makes Gemini reject the whole call. The
    current question and its tool round are always kept whole."""
    if len(messages) <= _MAX_HISTORY_MESSAGES:
        return messages
    questions = [i for i, m in enumerate(messages) if m["role"] == "user"]
    start = len(messages) - _MAX_HISTORY_MESSAGES
    later = [i for i in questions if i >= start]
    start = later[0] if later else (questions[-1] if questions else 0)
    return messages[start:]


@router.post("/message", response_model=ChatResponse)
async def post_message(req: ChatRequest, user: dict = Depends(actor)) -> ChatResponse:
    if not settings.chatbot_enabled:
        raise HTTPException(status_code=503, detail="Chatbot is disabled.")

    session_id = req.session_id or uuid.uuid4().hex
    _evict_expired()
    session = _touch(session_id, user["id"])

    latest_user = req.messages[-1]
    if latest_user.role != "user":
        raise HTTPException(status_code=400, detail="Last message must have role='user'.")

    if not session["messages"]:
        for m in req.messages:
            session["messages"].append({"role": m.role, "content": m.content})
    else:
        session["messages"].append({"role": "user", "content": latest_user.content})

    # Every tool call below runs as this user, over this user's patients.
    ctx = chatbot_tools.ToolContext(user=user, scope=await patient_scope(user))
    try:
        system_instruction = await chatbot_tools.build_system_instruction(_audience(user), ctx)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Snapshot build failed")
        system_instruction = chatbot_tools._BASE_SYSTEM_PROMPT

    tool_calls_log: list[ToolCallLog] = []
    conversation = list(session["messages"])

    reply_text = ""
    error: str | None = None

    try:
        for _ in range(_MAX_TOOL_ITERATIONS):
            response = await llm.chat(
                messages=_trim(conversation),
                tools=[chatbot_tools.TOOL_SET],
                system_instruction=system_instruction,
            )

            function_calls = response["function_calls"]

            if not function_calls:
                reply_text = response["text"] or "(No response.)"
                break

            conversation.append({
                "role": "assistant",
                "content": response["text"] or "",
                "function_calls": function_calls,
                "model_content": response.get("model_content"),
            })

            for call in function_calls:
                result, err = await chatbot_tools.dispatch_tool(call["name"], call["args"], ctx)
                tool_calls_log.append(ToolCallLog(
                    name=call["name"],
                    args=call["args"],
                    ok=err is None,
                    error=err,
                ))
                conversation.append({
                    "role": "function",
                    "name": call["name"],
                    "response": result,
                })
        else:
            reply_text = (
                "I gathered several pieces of data but couldn't finish the answer within the "
                "tool-call limit. Try rephrasing or asking one thing at a time."
            )
    except RuntimeError as exc:
        logger.error("Chatbot config error: %s", exc)
        reply_text = (
            "The assistant isn't configured yet — the backend is missing `GOOGLE_API_KEY`. "
            "Add it to `Backend/.env` and restart the server."
        )
        error = str(exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Chatbot request failed")
        reply_text = "Sorry — I hit an error reaching the assistant. Please try again in a moment."
        error = f"{type(exc).__name__}: {exc}"

    session["messages"].append({"role": "assistant", "content": reply_text})
    session["touched"] = time.monotonic()

    return ChatResponse(
        reply=reply_text,
        session_id=session_id,
        tool_calls=tool_calls_log,
        error=error,
    )


@router.get("/session/{session_id}")
async def get_session(session_id: str, user: dict = Depends(actor)) -> dict:
    session = _SESSIONS.get(session_id)
    if session is None or session.get("owner") != user["id"]:
        raise HTTPException(status_code=404, detail="Session not found.")
    _SESSIONS.move_to_end(session_id)
    session["touched"] = time.monotonic()
    visible = [m for m in session["messages"] if m.get("role") in ("user", "assistant") and m.get("content")]
    return {"session_id": session_id, "messages": visible}


@router.delete("/session/{session_id}")
async def clear_session(session_id: str, user: dict = Depends(actor)) -> dict:
    session = _SESSIONS.get(session_id)
    existed = session is not None and session.get("owner") == user["id"]
    if existed:
        _SESSIONS.pop(session_id, None)
    return {"cleared": existed, "session_id": session_id}