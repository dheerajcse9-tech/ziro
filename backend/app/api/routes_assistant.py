"""Ask PlaceCrew: the staff's conversational agent (read-only tools; actions are proposals a person confirms)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent import assistant
from app.api.deps import ANY_ROLE, require_roles
from app.db.session import get_db
from app.models.entities import User

router = APIRouter(prefix="/api")


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=4000)


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    history: list[Turn] = Field(default_factory=list, max_length=12)


@router.post("/assistant/ask", tags=["assistant"])
def ask(body: AskIn, db: Session = Depends(get_db), user: User = Depends(require_roles(*ANY_ROLE))):
    return assistant.ask(db, user, body.question, [t.model_dump() for t in body.history])
