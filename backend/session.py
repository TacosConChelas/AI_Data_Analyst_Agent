"""
Per-session conversational state.

A Session holds only what must be private to a client/tab: the active file and
the conversation history. Datasets themselves are shared (see datastore.py).

Sessions are identified by an HTTP cookie and kept in memory — they are
intentionally ephemeral (lost on restart). Only datasets persist.
"""
from __future__ import annotations

import uuid


class Session:
    def __init__(self):
        self.active_file: str | None = None
        self.conversation_history: list[dict] = []

    def set_active_file(self, filename: str, datastore) -> bool:
        """Point this session at *filename*. Resets history ONLY when the file
        actually changes (the frontend sends the active file on every chat turn,
        so clearing unconditionally would wipe conversation memory each message).
        Returns False if the file is not in the shared store."""
        if not datastore.has(filename):
            return False
        if filename != self.active_file:
            self.active_file = filename
            self.conversation_history = []
        return True


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, sid: str | None) -> tuple[str, Session]:
        """Return (sid, Session). Reuses a client-provided sid so the cookie
        stays stable across restarts; generates a new one only when absent."""
        if not sid:
            sid = uuid.uuid4().hex
        if sid not in self._sessions:
            self._sessions[sid] = Session()
        return sid, self._sessions[sid]

    def count(self) -> int:
        return len(self._sessions)
