"""FastAPI web server for the Song Finder tool."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from recognizer import identify_by_audio, identify_by_lyrics

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB limit

app = FastAPI(title="聽歌尋曲小工具 (Song Finder)", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class LyricsRequest(BaseModel):
    lyrics: str = Field(..., min_length=1, max_length=1000)
    gemini_api_key: str | None = None


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "has_env_gemini": bool(os.getenv("GEMINI_API_KEY", "").strip()),
        "has_env_audd": bool(os.getenv("AUDD_API_TOKEN", "").strip()),
    }


@app.post("/api/identify/lyrics")
async def api_identify_lyrics(payload: LyricsRequest) -> dict[str, object]:
    return await identify_by_lyrics(
        payload.lyrics,
        gemini_api_key=payload.gemini_api_key,
    )


@app.post("/api/identify/audio")
async def api_identify_audio(
    file: UploadFile = File(...),
    gemini_api_key: str | None = Form(default=None),
    audd_token: str | None = Form(default=None),
) -> dict[str, object]:
    raw = await file.read(MAX_AUDIO_BYTES + 1)
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="音檔過大，請上傳 25 MB 以內的音訊片段。")
    return await identify_by_audio(
        raw,
        filename=file.filename or "audio.webm",
        gemini_api_key=gemini_api_key,
        audd_token=audd_token,
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8765"))
    uvicorn.run("app:app", host=host, port=port, reload=False, proxy_headers=True)
