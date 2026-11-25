"""Unit and live integration tests for Song Finder."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app
from recognizer import (
    SongCandidate,
    build_platform_links,
    format_one_plus_four,
    identify_by_audio,
    identify_by_lyrics,
)


def test_format_one_plus_four_dedup_and_cap() -> None:
    candidates = [
        SongCandidate(title="告白氣球", artist="周杰倫", confidence=95, source="LRCLIB"),
        SongCandidate(title="告白氣球 (Live)", artist="周杰倫", confidence=80, source="iTunes"),
        SongCandidate(title="晴天", artist="周杰倫", confidence=75, source="Deezer"),
        SongCandidate(title="稻香", artist="周杰倫", confidence=70, source="Deezer"),
        SongCandidate(title="七里香", artist="周杰倫", confidence=65, source="iTunes"),
        SongCandidate(title="擱淺", artist="周杰倫", confidence=60, source="iTunes"),
        SongCandidate(title="夜曲", artist="周杰倫", confidence=55, source="iTunes"),
    ]
    res = format_one_plus_four(candidates, engines_used=["LRCLIB", "iTunes"], mode="lyrics")
    assert res["best_match"] is not None
    assert res["best_match"]["title"] == "告白氣球"
    assert len(res["possible_matches"]) == 4
    titles = [item["title"] for item in res["possible_matches"]]
    assert titles == ["晴天", "稻香", "七里香", "擱淺"]
    for item in [res["best_match"], *res["possible_matches"]]:
        assert "youtube" in item["links"]
        assert "spotify" in item["links"]
        assert "apple_music" in item["links"]


def test_build_platform_links_contains_required_platforms() -> None:
    links = build_platform_links("Lemon", "Kenshi Yonezu", apple_music="https://music.apple.com/test")
    assert "youtube.com" in links["youtube"]
    assert "music.youtube.com" in links["youtube_music"]
    assert "open.spotify.com" in links["spotify"]
    assert links["apple_music"] == "https://music.apple.com/test"


def test_api_health_and_static_index() -> None:
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    home = client.get("/")
    assert home.status_code == 200
    assert "聽歌尋曲小工具" in home.text


def test_live_lyrics_identification_returns_one_plus_four() -> None:
    res = asyncio.run(
        identify_by_lyrics("never gonna give you up never gonna let you down")
    )
    assert res["best_match"] is not None
    assert "never gonna give you up" in res["best_match"]["title"].lower()
    assert len(res["possible_matches"]) == 4
    assert res["best_match"]["links"]["youtube"]


def test_live_cjk_lyrics_identification_returns_one_plus_four() -> None:
    res_jp = asyncio.run(identify_by_lyrics("夢ならばどれほどよかったでしょう"))
    assert res_jp["best_match"] is not None
    assert "lemon" in res_jp["best_match"]["title"].lower()
    assert len(res_jp["possible_matches"]) == 4

    res_zh = asyncio.run(identify_by_lyrics("我們說好不分離 要一直一直在一起"))
    assert res_zh["best_match"] is not None
    assert "時間煮雨" in res_zh["best_match"]["title"]
    assert len(res_zh["possible_matches"]) == 4


def test_live_audio_identification_via_preview_clip() -> None:
    # Fetch a real 30s preview clip from iTunes to verify Shazam audio pipeline end-to-end
    with httpx.Client(timeout=15.0) as client:
        search = client.get(
            "https://itunes.apple.com/search",
            params={"term": "Never Gonna Give You Up Rick Astley", "entity": "song", "limit": 1},
        ).json()
        preview_url = search["results"][0]["previewUrl"]
        audio_bytes = client.get(preview_url).content

    res = asyncio.run(identify_by_audio(audio_bytes, "preview.m4a"))
    assert res["best_match"] is not None
    assert "never gonna give you up" in res["best_match"]["title"].lower()
    assert len(res["possible_matches"]) == 4
    assert res["best_match"]["links"]["youtube"]
