"""Session management: stores conversation history and order state per session."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models import ChatMessage, Order


class Session:
    """Represents an active conversation session."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.history: list[ChatMessage] = []
        self.order: Order = Order()
        self.created_at: datetime = datetime.now(timezone.utc)
        self.last_active: datetime = self.created_at

    def add_message(self, role: str, content: str) -> None:
        self.history.append(ChatMessage(role=role, content=content))
        self.last_active = datetime.now(timezone.utc)

    def get_history(self) -> list[dict]:
        return [{"role": m.role, "content": m.content} for m in self.history]


class SessionService:
    """In-memory session store (suitable for single-instance deployments)."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str) -> Session:
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(session_id)
        return self._sessions[session_id]

    def create_new(self) -> Session:
        session_id = str(uuid.uuid4())
        session = Session(session_id)
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def reset_order(self, session_id: str) -> bool:
        session = self.get(session_id)
        if session:
            session.order = Order()
            return True
        return False


session_service = SessionService()
