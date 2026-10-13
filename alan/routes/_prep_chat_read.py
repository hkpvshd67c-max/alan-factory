from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

import alan.prep_chat as prep_chat_mod
from alan._chat_core import ChatBrief
from alan.routes._deps import ChatMessageOut, WorkspacesRootDep, read_workspace_or_404

__all__ = ["PrepChatTranscriptOut", "get_workspace_prep_chat", "prep_chat_read_router"]

prep_chat_read_router = APIRouter()


class PrepChatTranscriptOut(BaseModel):
    session_id: str
    kind: prep_chat_mod.PrepChatKind
    consultant_key: str | None = None
    engine_key: str | None = None
    title: str
    created_at: datetime
    messages: list[ChatMessageOut]
    brief: ChatBrief | None = None


_PREP_CHAT_NOT_FOUND: dict[int | str, dict[str, str]] = {
    404: {"description": "workspace not found or no archived preparation chat"},
}


@prep_chat_read_router.get("/{task_id}/prep-chat", responses=_PREP_CHAT_NOT_FOUND)
async def get_workspace_prep_chat(
    task_id: str,
    workspaces_root: WorkspacesRootDep,
) -> PrepChatTranscriptOut:
    ws = await read_workspace_or_404(task_id, workspaces_root)
    session_id = await run_in_threadpool(prep_chat_mod.find_archived_session_id, ws.path)
    if session_id is None:
        raise HTTPException(status_code=404, detail="no archived preparation chat")
    try:
        archived = await run_in_threadpool(prep_chat_mod.read_archived, ws.path, session_id)
    except prep_chat_mod.PrepChatError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session = archived.session
    return PrepChatTranscriptOut(
        session_id=session.session_id,
        kind=archived.marker.kind,
        consultant_key=archived.marker.consultant_key,
        engine_key=session.engine_key,
        title=archived.marker.title,
        created_at=session.created_at,
        messages=[
            ChatMessageOut.model_validate(m, from_attributes=True) for m in session.transcript
        ],
        brief=archived.brief,
    )
