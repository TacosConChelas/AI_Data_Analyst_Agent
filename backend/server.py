"""
FastAPI server for the AI Data Analyst Agent.

State model:
  - DataStore (shared)      → loaded DataFrames + RAG index; persists via uploads/
  - SessionManager (cookie) → per-session active file + conversation history
  - DataAnalystAgent        → stateless reasoning engine (LLM client)

Endpoints:
  POST   /api/upload                 – upload CSV / Excel (loads into the store)
  POST   /api/chat                   – send message, receive structured response
  GET    /api/files                  – list shared files (active flag per session)
  POST   /api/switch-file/{filename} – change this session's active file
  GET    /api/data/{filename}        – paged/searchable view of a dataset
  DELETE /api/files/{filename}       – remove file from store and disk (global)
  GET    /api/charts/{filename}      – serve a generated chart image
  GET    /api/health                 – health check

Static files (frontend) are served from /
"""
from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Ensure backend package is importable when run from any cwd
sys.path.insert(0, str(Path(__file__).parent))

from agent import DataAnalystAgent  # noqa: E402
from datastore import DataStore     # noqa: E402
from session import Session, SessionManager  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Directories
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
CHARTS_DIR = BASE_DIR / "charts"
FRONTEND_DIR = BASE_DIR / "frontend"

UPLOAD_DIR.mkdir(exist_ok=True)
CHARTS_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB upload cap
SESSION_COOKIE = "sid"

# ─────────────────────────────────────────────────────────────────────────────
# Shared state
# ─────────────────────────────────────────────────────────────────────────────
datastore       = DataStore()
session_manager = SessionManager()
agent           = DataAnalystAgent(charts_dir=CHARTS_DIR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Datasets persist across restarts: rebuild the store from disk on startup.
    datastore.load_from_disk(UPLOAD_DIR)
    yield


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(title="AI Data Analyst Agent", version="2.0.0", lifespan=lifespan)

# Same-origin app: only the local UI needs to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Session dependency – reads/creates the `sid` cookie
# ─────────────────────────────────────────────────────────────────────────────
def get_session(request: Request, response: Response) -> Session:
    sid = request.cookies.get(SESSION_COOKIE)
    sid, session = session_manager.get_or_create(sid)
    response.set_cookie(
        SESSION_COOKIE, sid,
        httponly=True, samesite="lax", max_age=30 * 24 * 3600,
    )
    return session


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic models
# ─────────────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    file: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# API routes  (must be registered BEFORE the static-files catch-all)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    allowed = {".csv", ".xlsx", ".xls"}

    # Strip any path components — never trust the client-supplied filename
    safe_name = Path(file.filename or "").name
    if not safe_name:
        raise HTTPException(400, detail="Invalid or missing filename.")

    suffix = Path(safe_name).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(
            400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {', '.join(allowed)}",
        )

    # Stream to disk with a hard size cap so a huge upload can't exhaust memory/disk
    dest = UPLOAD_DIR / safe_name
    size = 0
    try:
        with open(dest, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413,
                        detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).",
                    )
                f.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise

    try:
        info = datastore.load_file(str(dest))
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, detail=str(exc)) from exc

    session.set_active_file(info["filename"], datastore)
    return {"success": True, **info}


@app.post("/api/chat")
async def chat(req: ChatRequest, session: Session = Depends(get_session)):
    if req.file:
        session.set_active_file(req.file, datastore)

    df = datastore.get_df(session.active_file)
    try:
        return agent.chat(
            req.message,
            df=df,
            filename=session.active_file if df is not None else None,
            history=session.conversation_history,
            rag=datastore.rag,
        )
    except Exception as exc:
        raise HTTPException(500, detail=str(exc)) from exc


@app.get("/api/files")
async def get_files(session: Session = Depends(get_session)):
    files = datastore.list_files()
    for f in files:
        f["active"] = f["filename"] == session.active_file
    return {"files": files}


@app.post("/api/switch-file/{filename}")
async def switch_file(filename: str, session: Session = Depends(get_session)):
    if not session.set_active_file(filename, datastore):
        raise HTTPException(404, detail=f"File '{filename}' not found.")
    return {"success": True, "active_file": filename}


@app.get("/api/data/{filename}")
async def get_data(filename: str, search: str = ""):
    safe = Path(filename).name
    df = datastore.get_df(safe)
    if df is None:
        raise HTTPException(404, detail=f"File '{safe}' not loaded.")

    total = len(df)

    # Optional full-text search across all columns
    if search.strip():
        mask = df.astype(str).apply(
            lambda col: col.str.contains(search, case=False, na=False)
        ).any(axis=1)
        df = df[mask]

    matched = len(df)
    chunk   = df.head(50)           # always show max 50 rows

    return {
        "columns": list(df.columns),
        "data":    json.loads(chunk.to_json(orient="records", date_format="iso")),
        "total":   total,
        "matched": matched,
        "showing": len(chunk),
    }


@app.delete("/api/files/{filename}")
async def delete_file(filename: str, session: Session = Depends(get_session)):
    safe = Path(filename).name
    removed = datastore.delete_file(safe)
    if not removed:
        raise HTTPException(404, detail=f"File '{safe}' not found.")
    (UPLOAD_DIR / safe).unlink(missing_ok=True)
    if session.active_file == safe:
        session.active_file = None
        session.conversation_history = []
    return {"success": True, "filename": safe, "active_file": session.active_file}


@app.get("/api/charts/{filename}")
async def get_chart(filename: str):
    # Basic path traversal protection
    safe = Path(filename).name
    chart_path = CHARTS_DIR / safe
    if not chart_path.exists():
        raise HTTPException(404, detail="Chart not found.")
    return FileResponse(chart_path, media_type="image/png")


# ─────────────────────────────────────────────────────────────────────────────
# Static frontend (must come LAST)
# ─────────────────────────────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    port = 8000
    print(f"\n  AI Data Analyst Agent")
    print(f"  Local:   http://localhost:{port}  (bound to 127.0.0.1 — local only)\n")

    uvicorn.run("server:app", host="127.0.0.1", port=port, reload=True)
