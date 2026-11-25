"""Song & music recognition engine supporting audio clips and lyric snippets."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx
from shazamio import Shazam

# Ensure ffmpeg/ffprobe are on PATH (uses system ffmpeg in Docker, static-ffmpeg locally).
if not shutil.which("ffmpeg"):
    try:
        import static_ffmpeg

        static_ffmpeg.add_paths()
    except Exception:
        pass

HTTP_TIMEOUT = httpx.Timeout(12.0, connect=6.0)
USER_AGENT = "SongFinderLocalTool/1.0 (+https://localhost)"


@dataclass
class SongCandidate:
    """Normalized song identification result."""

    title: str
    artist: str
    album: str | None = None
    cover_url: str | None = None
    preview_url: str | None = None
    match_reason: str = ""
    lyric_snippet: str | None = None
    confidence: int = 50
    source: str = "Unknown"
    links: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "cover_url": self.cover_url,
            "preview_url": self.preview_url,
            "match_reason": self.match_reason,
            "lyric_snippet": self.lyric_snippet,
            "confidence": max(1, min(99, int(self.confidence))),
            "source": self.source,
            "links": self.links,
        }


def _normalize_text(text: str) -> str:
    """Normalize text for fuzzy matching and deduplication."""
    cleaned = text.lower().strip()
    # Remove common parenthetical tags like (feat. ...), (2022 Remaster), [Remastered], etc.
    cleaned = re.sub(
        r"\([^)]*(?:feat\.|ft\.|with |remaster|live|acoustic|version|ver\.|radio edit|deluxe|anniversary)[^)]*\)",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"\[[^\]]*(?:feat\.|ft\.|remaster|live|official|lyrics|audio|mv)[^\]]*\]",
        "",
        cleaned,
    )
    cleaned = re.sub(r"[^\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _dedup_key(title: str, artist: str) -> tuple[str, str]:
    t = _normalize_text(title)
    a = _normalize_text(artist)
    # Keep only primary artist before comma / & / feat for stronger dedup
    a = re.split(r"\b(feat|ft|and)\b|[,&/×]", a)[0].strip() or a
    return (t, a)


def build_platform_links(
    title: str,
    artist: str,
    *,
    apple_music: str | None = None,
    spotify: str | None = None,
    shazam: str | None = None,
    deezer: str | None = None,
    youtube: str | None = None,
) -> dict[str, str]:
    """Build direct and search-fallback links across major music platforms."""
    query = quote_plus(f"{title} {artist}".strip())
    links: dict[str, str] = {
        "youtube": youtube or f"https://www.youtube.com/results?search_query={query}",
        "youtube_music": f"https://music.youtube.com/search?q={query}",
        "spotify": spotify or f"https://open.spotify.com/search/{quote_plus(f'{title} {artist}'.strip())}",
    }
    if apple_music:
        links["apple_music"] = apple_music
    else:
        links["apple_music"] = f"https://music.apple.com/search?term={query}"
    if shazam:
        links["shazam"] = shazam
    if deezer:
        links["deezer"] = deezer
    return links


def _merge_candidate(existing: SongCandidate, incoming: SongCandidate) -> SongCandidate:
    """Merge metadata from two sources for the same song."""
    if incoming.confidence > existing.confidence:
        existing.confidence = incoming.confidence
        if incoming.match_reason:
            existing.match_reason = incoming.match_reason
        if existing.source != incoming.source:
            existing.source = f"{existing.source} + {incoming.source}"
    existing.album = existing.album or incoming.album
    existing.cover_url = existing.cover_url or incoming.cover_url
    existing.preview_url = existing.preview_url or incoming.preview_url
    existing.lyric_snippet = existing.lyric_snippet or incoming.lyric_snippet
    for k, v in incoming.links.items():
        if v and (k not in existing.links or "search" in existing.links.get(k, "")):
            existing.links[k] = v
    return existing


def format_one_plus_four(
    candidates: list[SongCandidate],
    *,
    engines_used: list[str],
    mode: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """Deduplicate, rank, and split results into 1 best match + up to 4 possible matches."""
    merged: dict[tuple[str, str], SongCandidate] = {}
    for cand in candidates:
        if not cand.title.strip():
            continue
        if not cand.links:
            cand.links = build_platform_links(cand.title, cand.artist)
        key = _dedup_key(cand.title, cand.artist)
        if key in merged:
            merged[key] = _merge_candidate(merged[key], cand)
        else:
            merged[key] = cand

    ranked = sorted(merged.values(), key=lambda c: c.confidence, reverse=True)
    best = ranked[0].to_dict() if ranked else None
    possibles = [c.to_dict() for c in ranked[1:5]]

    return {
        "mode": mode,
        "best_match": best,
        "possible_matches": possibles,
        "total_found": len(ranked),
        "engines_used": engines_used,
        "notes": notes,
    }


def _extract_lyric_snippet(query: str, plain_lyrics: str) -> tuple[int, str | None]:
    """Score lyric overlap and return an excerpt around the matched phrase."""
    if not plain_lyrics:
        return 35, None

    q_norm = _normalize_text(query)
    lines = [ln.strip() for ln in plain_lyrics.splitlines() if ln.strip()]
    joined_norm = _normalize_text(" ".join(lines))

    if not q_norm or not joined_norm:
        return 35, None

    # Exact normalized phrase match in full lyrics
    if q_norm in joined_norm:
        best_line_idx = 0
        best_ratio = -1.0
        for idx, ln in enumerate(lines):
            ln_norm = _normalize_text(ln)
            if q_norm in ln_norm or ln_norm in q_norm:
                best_line_idx = idx
                best_ratio = 1.0
                break
            ratio = SequenceMatcher(None, q_norm, ln_norm).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_line_idx = idx
        start = max(0, best_line_idx - 1)
        end = min(len(lines), best_line_idx + 2)
        snippet = " / ".join(lines[start:end])
        return 95, snippet[:220]

    # Token & CJK character overlap scoring
    q_tokens = set(q_norm.split())
    cjk_q = set(re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", query))
    best_line_idx = 0
    best_line_score = 0.0
    for idx, ln in enumerate(lines):
        ln_norm = _normalize_text(ln)
        seq_score = SequenceMatcher(None, q_norm, ln_norm).ratio()
        if seq_score > best_line_score:
            best_line_score = seq_score
            best_line_idx = idx

    j_tokens = set(joined_norm.split())
    token_overlap = len(q_tokens & j_tokens) / max(1, len(q_tokens))
    cjk_j = set(re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", plain_lyrics))
    cjk_overlap = len(cjk_q & cjk_j) / max(1, len(cjk_q)) if cjk_q else 0.0

    combined = max(token_overlap, cjk_overlap, best_line_score)
    score = int(45 + combined * 45)
    start = max(0, best_line_idx - 1)
    end = min(len(lines), best_line_idx + 2)
    snippet = " / ".join(lines[start:end]) if lines else None
    return min(92, score), (snippet[:220] if snippet else None)


async def _search_lrclib(client: httpx.AsyncClient, query: str) -> list[SongCandidate]:
    """Query LRCLIB free lyrics database."""
    results: list[SongCandidate] = []
    try:
        resp = await client.get(
            "https://lrclib.net/api/search",
            params={"q": query},
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code != 200:
            return results
        items = resp.json()
        if not isinstance(items, list):
            return results

        for idx, item in enumerate(items[:12]):
            title = (item.get("trackName") or item.get("name") or "").strip()
            artist = (item.get("artistName") or "").strip()
            if not title or not artist:
                continue
            plain = item.get("plainLyrics") or ""
            score, snippet = _extract_lyric_snippet(query, plain)
            # Small tie-breaker for earlier search rank
            score = max(40, score - idx)
            reason = f"歌詞資料庫命中：{snippet}" if snippet else "LRCLIB 歌詞關聯比對"
            results.append(
                SongCandidate(
                    title=title,
                    artist=artist,
                    album=item.get("albumName") or None,
                    match_reason=reason,
                    lyric_snippet=snippet,
                    confidence=score,
                    source="LRCLIB",
                    links=build_platform_links(title, artist),
                )
            )
    except Exception:
        pass
    return results


async def _search_itunes(
    client: httpx.AsyncClient,
    term: str,
    *,
    limit: int = 8,
    country: str = "TW",
    base_confidence: int = 72,
    reason: str = "iTunes 曲庫關聯搜尋",
) -> list[SongCandidate]:
    """Query Apple iTunes Search API (no API key required)."""
    results: list[SongCandidate] = []
    try:
        resp = await client.get(
            "https://itunes.apple.com/search",
            params={"term": term, "entity": "song", "limit": limit, "country": country},
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code != 200:
            return results
        data = resp.json()
        for idx, item in enumerate(data.get("results", [])):
            title = (item.get("trackName") or "").strip()
            artist = (item.get("artistName") or "").strip()
            if not title or not artist:
                continue
            art = item.get("artworkUrl100") or item.get("artworkUrl60")
            if art:
                art = art.replace("100x100bb", "300x300bb")
            apple_url = item.get("trackViewUrl")
            results.append(
                SongCandidate(
                    title=title,
                    artist=artist,
                    album=item.get("collectionName") or None,
                    cover_url=art,
                    preview_url=item.get("previewUrl"),
                    match_reason=reason,
                    confidence=max(30, base_confidence - idx * 3),
                    source="iTunes",
                    links=build_platform_links(title, artist, apple_music=apple_url),
                )
            )
    except Exception:
        pass
    return results


async def _search_deezer(
    client: httpx.AsyncClient,
    term: str,
    *,
    limit: int = 8,
    base_confidence: int = 70,
    reason: str = "Deezer 曲庫關聯搜尋",
) -> list[SongCandidate]:
    """Query Deezer public search API (no API key required)."""
    results: list[SongCandidate] = []
    try:
        resp = await client.get(
            "https://api.deezer.com/search",
            params={"q": term, "limit": limit},
            headers={"User-Agent": USER_AGENT},
        )
        if resp.status_code != 200:
            return results
        data = resp.json()
        for idx, item in enumerate(data.get("data", [])):
            title = (item.get("title") or "").strip()
            artist = ((item.get("artist") or {}).get("name") or "").strip()
            if not title or not artist:
                continue
            album_obj = item.get("album") or {}
            results.append(
                SongCandidate(
                    title=title,
                    artist=artist,
                    album=album_obj.get("title") or None,
                    cover_url=album_obj.get("cover_medium") or album_obj.get("cover"),
                    preview_url=item.get("preview") or None,
                    match_reason=reason,
                    confidence=max(28, base_confidence - idx * 3),
                    source="Deezer",
                    links=build_platform_links(title, artist, deezer=item.get("link")),
                )
            )
    except Exception:
        pass
    return results


async def _enrich_with_itunes(client: httpx.AsyncClient, candidates: list[SongCandidate]) -> None:
    """Enrich top candidates lacking artwork/preview/Apple Music link via iTunes."""
    targets = [c for c in candidates if not c.cover_url or not c.preview_url][:6]
    if not targets:
        return

    async def _enrich_one(cand: SongCandidate) -> None:
        hits = await _search_itunes(client, f"{cand.title} {cand.artist}", limit=2, country="TW")
        if not hits:
            hits = await _search_itunes(client, f"{cand.title} {cand.artist}", limit=2, country="US")
        if hits:
            top = hits[0]
            cand.cover_url = cand.cover_url or top.cover_url
            cand.preview_url = cand.preview_url or top.preview_url
            cand.album = cand.album or top.album
            if "apple_music" in top.links:
                cand.links["apple_music"] = top.links["apple_music"]

    await asyncio.gather(*[_enrich_one(c) for c in targets], return_exceptions=True)


async def _verify_lyrics_on_lrclib(
    client: httpx.AsyncClient, query: str, candidates: list[SongCandidate]
) -> None:
    """Fetch actual lyrics from LRCLIB for top metadata hits and re-score by lyric match."""
    seen: set[tuple[str, str]] = set()
    targets: list[SongCandidate] = []
    for cand in candidates:
        if cand.lyric_snippet:
            continue
        key = _dedup_key(cand.title, cand.artist)
        if key not in seen:
            seen.add(key)
            targets.append(cand)
        if len(targets) >= 6:
            break

    if not targets:
        return

    async def _verify_one(cand: SongCandidate) -> None:
        clean_title = re.sub(r"\s*[\(\[].*?[\)\]]", "", cand.title).strip() or cand.title
        try:
            resp = await client.get(
                "https://lrclib.net/api/search",
                params={"track_name": clean_title, "artist_name": cand.artist},
                headers={"User-Agent": USER_AGENT},
            )
            items = resp.json() if resp.status_code == 200 else []
            if not items:
                resp2 = await client.get(
                    "https://lrclib.net/api/search",
                    params={"q": f"{clean_title} {cand.artist}"},
                    headers={"User-Agent": USER_AGENT},
                )
                items = resp2.json() if resp2.status_code == 200 else []
            if not isinstance(items, list) or not items:
                return

            best_score = -1
            best_snippet: str | None = None
            for item in items[:5]:
                plain = item.get("plainLyrics") or ""
                if not plain:
                    continue
                score, snippet = _extract_lyric_snippet(query, plain)
                if score > best_score:
                    best_score = score
                    best_snippet = snippet

            if best_score > 45 and best_snippet:
                cand.lyric_snippet = best_snippet
                if best_score > cand.confidence:
                    cand.confidence = best_score
                    cand.match_reason = f"歌詞內容比對命中：{best_snippet}"
                    if "LRCLIB" not in cand.source:
                        cand.source = f"{cand.source} + LRCLIB"
        except Exception:
            pass

    await asyncio.gather(*[_verify_one(c) for c in targets], return_exceptions=True)


def _parse_json_array_from_text(raw: str) -> list[dict[str, Any]]:
    """Extract JSON array from LLM output, stripping optional code fences."""
    cleaned = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    data = json.loads(cleaned)
    return data if isinstance(data, list) else []


async def _identify_lyrics_with_gemini(
    client: httpx.AsyncClient, lyrics: str, api_key: str
) -> list[SongCandidate]:
    """Use Gemini API (optional) for fuzzy/multilingual/translated lyrics."""
    prompt = (
        "你是一個音樂辨識專家。使用者提供了一段歌詞或對歌曲的記憶片段。\n"
        "請推測最符合的 1 首歌，以及另外 4 首可能符合的歌曲（共 5 首不重複歌曲）。\n"
        "請只回傳純 JSON 陣列，每個物件包含欄位："
        '{"title": "歌名", "artist": "歌手", "confidence": 0到99的整數, "reason": "簡短繁體中文說明"}。\n'
        f"使用者輸入：{lyrics}"
    )
    return await _call_gemini_json(client, api_key, [{"text": prompt}], source="Gemini AI")


async def _identify_audio_with_gemini(
    client: httpx.AsyncClient, audio_bytes: bytes, mime_type: str, api_key: str
) -> list[SongCandidate]:
    """Use Gemini multimodal audio understanding (optional) for humming/covers/clips."""
    if len(audio_bytes) > 15 * 1024 * 1024:
        return []
    b64 = base64.b64encode(audio_bytes).decode("ascii")
    prompt = (
        "請仔細聆聽這段音檔（可能是原曲片段、翻唱、純音樂或哼唱），辨識這是哪一首歌或音樂。\n"
        "請列出 1 個最符合的選項與 4 個可能的選項（共 5 首不重複歌曲，依可能性由高到低排序）。\n"
        "請只回傳純 JSON 陣列，每個物件包含欄位："
        '{"title": "歌名", "artist": "歌手或作曲者", "confidence": 0到99的整數, "reason": "簡短繁體中文推測理由"}。'
    )
    parts = [
        {"inline_data": {"mime_type": mime_type, "data": b64}},
        {"text": prompt},
    ]
    return await _call_gemini_json(client, api_key, parts, source="Gemini AI 音訊")


async def _call_gemini_json(
    client: httpx.AsyncClient, api_key: str, parts: list[dict[str, Any]], *, source: str
) -> list[SongCandidate]:
    results: list[SongCandidate] = []
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={api_key.strip()}"
    )
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    try:
        resp = await client.post(url, json=payload, timeout=httpx.Timeout(25.0))
        if resp.status_code != 200:
            return results
        body = resp.json()
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        items = _parse_json_array_from_text(text)
        for idx, item in enumerate(items[:5]):
            title = str(item.get("title") or "").strip()
            artist = str(item.get("artist") or "").strip()
            if not title:
                continue
            conf = int(item.get("confidence") or max(50, 90 - idx * 8))
            reason = str(item.get("reason") or "Gemini AI 語意與特徵推測").strip()
            results.append(
                SongCandidate(
                    title=title,
                    artist=artist or "未知歌手",
                    match_reason=reason,
                    confidence=conf,
                    source=source,
                    links=build_platform_links(title, artist),
                )
            )
    except Exception:
        pass
    return results


async def _identify_audio_with_audd(
    client: httpx.AsyncClient, audio_bytes: bytes, filename: str, token: str
) -> list[SongCandidate]:
    """Use AudD audio fingerprinting API (optional)."""
    results: list[SongCandidate] = []
    try:
        resp = await client.post(
            "https://api.audd.io/",
            data={"api_token": token.strip(), "return": "apple_music,spotify,deezer"},
            files={"file": (filename, audio_bytes)},
            timeout=httpx.Timeout(20.0),
        )
        if resp.status_code != 200:
            return results
        data = resp.json()
        res = data.get("result")
        if not res:
            return results
        title = (res.get("title") or "").strip()
        artist = (res.get("artist") or "").strip()
        if not title:
            return results
        spotify_url = ((res.get("spotify") or {}).get("external_urls") or {}).get("spotify")
        apple_url = (res.get("apple_music") or {}).get("url")
        deezer_obj = res.get("deezer") or {}
        deezer_url = deezer_obj.get("link")
        preview = deezer_obj.get("preview")
        cover = (
            ((deezer_obj.get("album") or {}).get("cover_medium"))
            or (((res.get("spotify") or {}).get("album") or {}).get("images") or [{}])[0].get("url")
        )
        results.append(
            SongCandidate(
                title=title,
                artist=artist,
                album=res.get("album") or None,
                cover_url=cover,
                preview_url=preview,
                match_reason="AudD 音訊指紋精準比對",
                confidence=96,
                source="AudD",
                links=build_platform_links(
                    title,
                    artist,
                    apple_music=apple_url,
                    spotify=spotify_url,
                    deezer=deezer_url,
                ),
            )
        )
    except Exception:
        pass
    return results


def _parse_shazam_track(track: dict[str, Any], *, confidence: int, reason: str) -> SongCandidate | None:
    title = (track.get("title") or "").strip()
    artist = (track.get("subtitle") or "").strip()
    if not title:
        return None

    images = track.get("images") or {}
    cover = images.get("coverarthq") or images.get("coverart") or images.get("background")
    shazam_url = track.get("url")

    apple_url = None
    preview_url = None
    hub = track.get("hub") or {}
    for action in hub.get("actions") or []:
        if action.get("type") == "uri" and action.get("uri"):
            preview_url = action["uri"]
    for opt in hub.get("options") or []:
        for action in opt.get("actions") or []:
            if action.get("type") == "applemusicopen" and action.get("uri"):
                apple_url = action["uri"]
            elif not apple_url and isinstance(action.get("uri"), str) and "music.apple.com" in action["uri"]:
                apple_url = action["uri"]

    album = None
    for sec in track.get("sections") or []:
        for meta in sec.get("metadata") or []:
            if (meta.get("title") or "").lower() in {"album", "專輯"}:
                album = meta.get("text")
                break

    return SongCandidate(
        title=title,
        artist=artist or "未知歌手",
        album=album,
        cover_url=cover,
        preview_url=preview_url,
        match_reason=reason,
        confidence=confidence,
        source="Shazam",
        links=build_platform_links(title, artist, apple_music=apple_url, shazam=shazam_url),
    )


def _probe_duration_seconds(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.check_output(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        return float(out.strip())
    except Exception:
        return None


def _slice_audio_segment(src: Path, dst: Path, start_sec: float, duration_sec: float = 12.0) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-ss",
                f"{start_sec:.2f}",
                "-t",
                f"{duration_sec:.2f}",
                "-i",
                str(src),
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "44100",
                "-ac",
                "1",
                str(dst),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=15,
        )
        return dst.exists() and dst.stat().st_size > 1000
    except Exception:
        return False


async def _recognize_with_shazam(audio_path: Path) -> tuple[list[SongCandidate], str | None]:
    """Run Shazam recognition with multi-offset fallback for longer clips."""
    shazam = Shazam()
    candidates: list[SongCandidate] = []
    matched_track_id: str | None = None

    try:
        raw = await shazam.recognize(str(audio_path))
        track = raw.get("track") if isinstance(raw, dict) else None

        # If the clip start had silence/intro dialogue, probe 25% and 55% offsets.
        if not track:
            duration = _probe_duration_seconds(audio_path)
            if duration and duration > 14.0:
                for ratio in (0.25, 0.55):
                    offset = max(3.0, duration * ratio)
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_seg:
                        seg_path = Path(tmp_seg.name)
                    try:
                        if _slice_audio_segment(audio_path, seg_path, offset, 12.0):
                            raw = await shazam.recognize(str(seg_path))
                            track = raw.get("track") if isinstance(raw, dict) else None
                            if track:
                                break
                    finally:
                        seg_path.unlink(missing_ok=True)

        if track:
            matched_track_id = str(track.get("key") or "") or None
            primary = _parse_shazam_track(
                track, confidence=97, reason="Shazam 音訊指紋精準比對"
            )
            if primary:
                candidates.append(primary)

        # Fetch related tracks from Shazam for plausible alternatives
        if matched_track_id:
            try:
                related = await shazam.related_tracks(track_id=int(matched_track_id), limit=8)
                for idx, rel in enumerate((related or {}).get("tracks") or []):
                    cand = _parse_shazam_track(
                        rel,
                        confidence=max(45, 74 - idx * 5),
                        reason="Shazam 相似曲風 / 關聯推薦",
                    )
                    if cand:
                        candidates.append(cand)
            except Exception:
                pass
    finally:
        await shazam.close()

    return candidates, matched_track_id


async def identify_by_lyrics(
    lyrics: str,
    *,
    gemini_api_key: str | None = None,
) -> dict[str, Any]:
    """Identify 1 best + 4 possible songs from a lyric snippet."""
    cleaned = re.sub(r"\s+", " ", lyrics).strip()
    if len(cleaned) < 2:
        return format_one_plus_four([], engines_used=[], mode="lyrics", notes="請輸入至少 2 個字元的歌詞片段。")

    gemini_key = (gemini_api_key or os.getenv("GEMINI_API_KEY") or "").strip()
    engines_used = ["LRCLIB", "iTunes", "Deezer"]

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        tasks: list[Any] = [
            _search_lrclib(client, cleaned),
            _search_itunes(client, cleaned, limit=10, country="TW", base_confidence=62),
            _search_itunes(client, cleaned, limit=8, country="US", base_confidence=58),
            _search_deezer(client, cleaned, limit=10, base_confidence=60),
        ]
        if gemini_key:
            engines_used.append("Gemini AI")
            tasks.append(_identify_lyrics_with_gemini(client, cleaned, gemini_key))

        batches = await asyncio.gather(*tasks, return_exceptions=True)
        all_candidates: list[SongCandidate] = []
        for batch in batches:
            if isinstance(batch, list):
                all_candidates.extend(batch)

        # If query is very long and LRCLIB returned nothing, retry with a shorter middle slice
        if not any(c.source == "LRCLIB" for c in all_candidates) and len(cleaned) > 36:
            words = cleaned.split()
            if len(words) >= 6:
                short_slice = " ".join(words[:6])
                all_candidates.extend(await _search_lrclib(client, short_slice))

        # Verify top iTunes/Deezer metadata hits against real LRCLIB lyrics text
        prelim = sorted(all_candidates, key=lambda c: c.confidence, reverse=True)
        await _verify_lyrics_on_lrclib(client, cleaned, prelim)

        # Re-sort after lyric verification and enrich top hits with artwork/preview/Apple Music links
        prelim = sorted(all_candidates, key=lambda c: c.confidence, reverse=True)
        await _enrich_with_itunes(client, prelim[:6])

        # If we have a strong top match but fewer than 5 unique songs, add artist/title alternatives
        unique_keys = {_dedup_key(c.title, c.artist) for c in prelim}
        if prelim and len(unique_keys) < 5:
            top = prelim[0]
            extra_title, extra_artist = await asyncio.gather(
                _search_itunes(
                    client,
                    top.title,
                    limit=8,
                    country="TW",
                    base_confidence=52,
                    reason=f"與「{top.title}」同名或翻唱版本",
                ),
                _search_itunes(
                    client,
                    top.artist,
                    limit=10,
                    country="TW",
                    base_confidence=46,
                    reason=f"同歌手「{top.artist}」其他熱門作品",
                ),
            )
            all_candidates.extend(extra_title + extra_artist)

    notes = None
    if not all_candidates:
        notes = "免費歌詞資料庫未找到完全相符的曲目，建議改用更精確的一句副歌，或在上方設定選填 Gemini API Key 以啟用模糊語意推測。"

    return format_one_plus_four(all_candidates, engines_used=engines_used, mode="lyrics", notes=notes)


async def identify_by_audio(
    audio_bytes: bytes,
    filename: str,
    *,
    gemini_api_key: str | None = None,
    audd_token: str | None = None,
) -> dict[str, Any]:
    """Identify 1 best + 4 possible songs from an audio file or recording."""
    if not audio_bytes:
        return format_one_plus_four([], engines_used=[], mode="audio", notes="音檔內容為空，請重新上傳或錄製。")

    gemini_key = (gemini_api_key or os.getenv("GEMINI_API_KEY") or "").strip()
    audd_key = (audd_token or os.getenv("AUDD_API_TOKEN") or "").strip()

    suffix = Path(filename or "audio.webm").suffix or ".webm"
    mime_type, _ = mimetypes.guess_type(filename or "audio.wav")
    if not mime_type or not mime_type.startswith("audio/"):
        mime_type = "audio/wav" if suffix.lower() == ".wav" else "audio/mpeg"

    engines_used = ["Shazam"]
    all_candidates: list[SongCandidate] = []

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = Path(tmp.name)

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            tasks: list[Any] = [_recognize_with_shazam(tmp_path)]
            if audd_key:
                engines_used.append("AudD")
                tasks.append(_identify_audio_with_audd(client, audio_bytes, filename, audd_key))
            if gemini_key:
                engines_used.append("Gemini AI 音訊")
                tasks.append(_identify_audio_with_gemini(client, audio_bytes, mime_type, gemini_key))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            shazam_res = results[0]
            if isinstance(shazam_res, tuple):
                all_candidates.extend(shazam_res[0])
            for extra in results[1:]:
                if isinstance(extra, list):
                    all_candidates.extend(extra)

            # Ensure we have 1 best + 4 possible options with rich links & artwork
            prelim = sorted(all_candidates, key=lambda c: c.confidence, reverse=True)
            if prelim:
                top = prelim[0]
                unique_keys = {_dedup_key(c.title, c.artist) for c in prelim}
                if len(unique_keys) < 5:
                    # Search same title (covers/remixes/originals) and artist's other songs
                    extra_title, extra_artist_it, extra_artist_dz = await asyncio.gather(
                        _search_itunes(
                            client,
                            top.title,
                            limit=8,
                            country="TW",
                            base_confidence=60,
                            reason=f"與「{top.title}」同名、翻唱或不同版本",
                        ),
                        _search_itunes(
                            client,
                            top.artist,
                            limit=12,
                            country="TW",
                            base_confidence=52,
                            reason=f"同歌手「{top.artist}」其他代表作品",
                        ),
                        _search_deezer(
                            client,
                            top.artist,
                            limit=10,
                            base_confidence=48,
                            reason=f"同歌手「{top.artist}」關聯曲目",
                        ),
                    )
                    all_candidates.extend(extra_title + extra_artist_it + extra_artist_dz)

                await _enrich_with_itunes(client, sorted(all_candidates, key=lambda c: c.confidence, reverse=True)[:6])
    finally:
        tmp_path.unlink(missing_ok=True)

    notes = None
    if not all_candidates:
        notes = (
            "未比對到精準音訊指紋（常見原因：純人聲哼唱、清唱翻唱、環境雜音過大或片段過短）。"
            "建議靠近喇叭重新錄製 8–15 秒原曲，或改用「歌詞搜尋」，或在設定中填入選填的 Gemini API Key 辨識哼唱與翻唱。"
        )

    return format_one_plus_four(all_candidates, engines_used=engines_used, mode="audio", notes=notes)
