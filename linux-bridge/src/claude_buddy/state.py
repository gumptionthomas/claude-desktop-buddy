import time
from dataclasses import dataclass


@dataclass
class _Session:
    running: bool = False
    waiting: bool = False
    last_seen: float = 0.0
    project: str = ""


def _line(tool: str, detail: str, project: str = "") -> str:
    core = f"{tool}: {detail}" if detail else tool
    return f"[{project}] {core}" if project else core


class SessionStore:
    def __init__(self, stale_after: float = 300.0, max_entries: int = 6,
                 clock=time.monotonic):
        self._sessions: dict[str, _Session] = {}
        self._stale_after = stale_after
        self._max_entries = max_entries
        self._clock = clock
        self._recent: list[str] = []   # newest first
        self._completed = False

    def _touch(self, sid: str) -> _Session:
        s = self._sessions.get(sid)
        if s is None:
            s = _Session()
            self._sessions[sid] = s
        s.last_seen = self._clock()
        return s

    def session_start(self, sid: str) -> None:
        self._touch(sid)

    def prompt_submit(self, sid: str) -> None:
        s = self._touch(sid)
        s.running = True
        s.waiting = False

    def post_tool(self, sid: str, tool: str, detail: str = "",
                  project: str = "") -> None:
        s = self._touch(sid)
        s.running = True
        s.waiting = False
        if project:
            s.project = project
        self._recent.insert(0, _line(tool, detail, project))
        del self._recent[self._max_entries:]

    def notification(self, sid: str, project: str = "") -> None:
        s = self._touch(sid)
        s.waiting = True
        if project:
            s.project = project

    def stop(self, sid: str) -> None:
        s = self._touch(sid)
        s.running = False
        s.waiting = False
        self._completed = True

    def session_end(self, sid: str) -> None:
        self._sessions.pop(sid, None)

    def sweep(self) -> None:
        now = self._clock()
        stale = [k for k, s in self._sessions.items()
                 if now - s.last_seen > self._stale_after]
        for k in stale:
            del self._sessions[k]

    def snapshot(self) -> dict:
        running = sum(1 for s in self._sessions.values() if s.running)
        completed = self._completed
        self._completed = False
        waiters = [s for s in self._sessions.values() if s.waiting]
        waiting = len(waiters)
        if waiters:
            # Name the most-recently-waiting session's project so you know
            # where to go; count when more than one needs you.
            recent = max(waiters, key=lambda s: s.last_seen)
            proj = recent.project
            if waiting == 1:
                msg = f"{proj}: needs you" if proj else "needs you"
            else:
                msg = f"{waiting} waiting: {proj}" if proj else f"{waiting} waiting"
        elif self._recent:
            msg = self._recent[0]
        else:
            msg = "working" if running else "idle"
        return {
            "total": len(self._sessions),
            "running": running,
            "waiting": waiting,
            "msg": msg,
            "entries": list(self._recent),
            "completed": completed,
        }
