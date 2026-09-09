"""Cinefiles audio copyright clearance service."""

from __future__ import annotations

import base64
import io
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import requests as http_client
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

load_dotenv()

SYNC_LICENSE_BASE_FEE = 15_000.00
MASTER_LICENSE_BASE_FEE = 15_000.00
AUDD_API_URL = "https://api.audd.io/"
API_VERSION = "1.0.0"
SERVICE_NAME = "Cinefiles Audio Copyright Clearance Service"

CONTRACT_TEMPLATES = {
    "audio": """AUDIO CLEARANCE AGREEMENT - DRAFT
=================================
Date:           {date}
Clearance ID:   {clearance_id}
Production:     {production_title}
Track:          {song_title}
Artist:         {artist}
Source URL:     {audio_url}
Clip window:    {timestamp_start} - {timestamp_end}

This draft identifies the composition and sound recording for review before
the production is distributed. Separate synchronisation and master-use
permissions may be required from the relevant rights holders.

LICENSE ESTIMATES
{license_lines}
TOTAL ESTIMATED FEE: ${total_fee:,.0f} USD
STATUS: ESTIMATE - HUMAN LEGAL REVIEW REQUIRED

Rights holder approval: ______________________    Date: ____________
"""
}


class ClearanceRequest(BaseModel):
    audio_url: Optional[str] = Field(default=None, examples=["https://example.com/clip.mp3"])
    file_path: Optional[str] = Field(default=None, examples=["/uploads/clip.mp3"])

    @model_validator(mode="after")
    def exactly_one_source(self) -> "ClearanceRequest":
        has_url = bool(self.audio_url and self.audio_url.strip())
        has_path = bool(self.file_path and self.file_path.strip())
        if has_url == has_path:
            raise ValueError("Provide exactly one of 'audio_url' or 'file_path'.")
        return self


class LicenseFee(BaseModel):
    license_type: Literal["Sync", "Master"]
    description: str
    amount_usd: float


class DirectClearanceRequest(BaseModel):
    song_title: str = Field(..., min_length=1, examples=["Bohemian Rhapsody"])
    artist: str = Field(..., min_length=1, examples=["Queen"])
    timestamp_start: str = Field(..., min_length=1, examples=["00:01:30"])
    timestamp_end: str = Field(..., min_length=1, examples=["00:03:45"])


class PdfDraftPayload(BaseModel):
    filename: str
    content_type: Literal["application/pdf"]
    data_base64: str
    page_count: int


class AuddMatch(BaseModel):
    title: str
    artist: str
    apple_music_link: Optional[str] = None


class ClearanceResponse(BaseModel):
    status: Literal["approved", "pending", "denied"]
    royalty_free: bool
    match: AuddMatch
    licenses: list[LicenseFee]
    total_fee_usd: float
    currency: Literal["USD"]
    requested_at: str
    service: str
    version: str
    contract_draft: PdfDraftPayload


class ScriptUrlCandidate(BaseModel):
    url: str
    line_number: int
    context: str
    likely_audio: bool
    media_hint: Literal["audio", "video", "unknown"]


class ScriptScanResponse(BaseModel):
    filename: str
    candidate_count: int
    candidates: list[ScriptUrlCandidate]
    message: str

class AudioMetadata(BaseModel):
    track_title: str
    artist: str
    film_budget: float

app = FastAPI(
    title=SERVICE_NAME,
    version=API_VERSION,
    description=(
        "Audio copyright clearance using AudD fingerprinting, Sync and Master "
        "fee estimates, audio clearance drafts, and plain-text script URL scanning."
    ),
    contact={"name": "Cinefiles", "url": "https://github.com/alissatroiano/Cinefiles"},
    license_info={"name": "MIT"},
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


def _get_audd_token() -> str:
    token = os.environ.get("AUDD_API_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=500, detail="Server configuration error: AUDD_API_TOKEN is not set.")
    return token


def _query_audd(audio_url: Optional[str], file_path: Optional[str], token: str) -> dict:
    data = {"api_token": token, "return": "apple_music"}
    try:
        if audio_url:
            data["url"] = audio_url
            response = http_client.post(AUDD_API_URL, data=data, timeout=30)
        else:
            with open(file_path, "rb") as audio_file:  # type: ignore[arg-type]
                response = http_client.post(AUDD_API_URL, data=data, files={"file": audio_file}, timeout=30)
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=f"File not found: {file_path}")
    except http_client.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="AudD API request timed out.")
    except http_client.exceptions.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"AudD API unreachable: {exc}")
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"AudD API returned HTTP {response.status_code}.")
    return response.json()


_ROYALTY_FREE_KEYWORDS = frozenset({
    "royalty free", "royalty-free", "royaltyfree", "creative commons", "cc by", "cc0",
    "public domain", "no copyright", "copyright free", "free music", "stock music",
    "background music free", "pixabay", "freesound", "incompetech", "bensound",
    "audionautix", "ccmixter", "musopen",
})
_ROYALTY_FREE_LABELS = frozenset({
    "epidemic sound", "artlist", "musicbed", "premiumbeat", "pond5", "audiojungle",
    "envato", "motionarray", "soundsnap", "jamendo",
})
_ROYALTY_FREE_URL_MARKERS = frozenset({
    "royalty-free", "royaltyfree", "freemusic", "free-music",
    "public-domain", "publicdomain", "creative-commons", "creativecommons",
})


def _is_royalty_free(title: str, artist: str, audd_raw: Optional[dict] = None) -> bool:
    if any(keyword in title.lower() or keyword in artist.lower() for keyword in _ROYALTY_FREE_KEYWORDS):
        return True
    if audd_raw:
        result = audd_raw.get("result") or {}
        label = (result.get("label") or result.get("distributor") or "").lower()
        if any(label_name in label for label_name in _ROYALTY_FREE_LABELS):
            return True
        score = result.get("score")
        if score is not None and int(score) < 50:
            return True
    return False


def _is_royalty_free_url(audio_url: str) -> bool:
    """Recognize explicit royalty-free/public-domain markers in a source URL."""
    normalized_url = audio_url.lower().replace("_", "-")
    return any(marker in normalized_url for marker in _ROYALTY_FREE_URL_MARKERS)


def _build_audio_licenses(royalty_free: bool) -> list[dict]:
    if royalty_free:
        note = "Royalty-Free / Creative Commons track - no commercial sync fee required."
        return [
            {"license_type": "Sync", "description": note, "amount_usd": 0.0},
            {"license_type": "Master", "description": note, "amount_usd": 0.0},
        ]
    return [
        {"license_type": "Sync", "description": "Synchronisation licence for pairing the composition with visual media.", "amount_usd": SYNC_LICENSE_BASE_FEE},
        {"license_type": "Master", "description": "Master recording licence for use of the specific sound recording.", "amount_usd": MASTER_LICENSE_BASE_FEE},
    ]


def _extract_match(audd_response: dict) -> AuddMatch:
    if audd_response.get("status") != "success":
        raise HTTPException(status_code=502, detail=f"AudD API error: {audd_response.get('error', {}).get('error_message', 'unknown')}")
    result = audd_response.get("result")
    if not result:
        raise HTTPException(status_code=404, detail="No matching track found for the provided audio.")
    apple_music = result.get("apple_music") or {}
    return AuddMatch(title=result.get("title", "Unknown Title"), artist=result.get("artist", "Unknown Artist"), apple_music_link=apple_music.get("url") or None)


def _build_audio_contract_text(clearance_id: str, song_title: str, artist: str, audio_url: str, timestamp_start: str, timestamp_end: str, licenses: list[dict], total_fee: float) -> str:
    license_lines = "\n".join(f"        {item['license_type']}: ${item['amount_usd']:,.0f} USD" for item in licenses)
    return CONTRACT_TEMPLATES["audio"].format(
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        clearance_id=clearance_id,
        production_title="Untitled Production",
        song_title=song_title,
        artist=artist,
        audio_url=audio_url or "Not provided",
        timestamp_start=timestamp_start or "Not provided",
        timestamp_end=timestamp_end or "Not provided",
        license_lines=license_lines,
        total_fee=total_fee,
    )


def _encode_pdf_payload(contract_text: str, filename: str) -> PdfDraftPayload:
    pdf_buffer = io.BytesIO()
    document = SimpleDocTemplate(
        pdf_buffer,
        pagesize=letter,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )
    styles = getSampleStyleSheet()
    body_style = styles["BodyText"]
    body_style.fontName = "Helvetica"
    body_style.fontSize = 9
    body_style.leading = 12
    story = []
    for block in contract_text.split("\n\n"):
        story.append(Paragraph(block.replace("\n", "<br/>").replace("&", "&amp;"), body_style))
        story.append(Spacer(1, 8))
    document.build(story)
    encoded = base64.b64encode(pdf_buffer.getvalue()).decode("ascii")
    return PdfDraftPayload(filename=filename, content_type="application/pdf", data_base64=encoded, page_count=1)


def _audio_response(match: AuddMatch, licenses: list[dict], total_fee: float, audio_url: str = "", timestamp_start: str = "", timestamp_end: str = "") -> dict[str, Any]:
    draft = _encode_pdf_payload(
        _build_audio_contract_text(f"aud_{uuid.uuid4().hex[:12]}", match.title, match.artist, audio_url, timestamp_start, timestamp_end, licenses, total_fee),
        "audio_clearance_draft.pdf",
    )
    return {
        "status": "approved", "royalty_free": total_fee == 0, "match": match.model_dump(),
        "licenses": licenses, "total_fee_usd": total_fee, "currency": "USD",
        "requested_at": datetime.now(timezone.utc).isoformat(), "service": SERVICE_NAME,
        "version": API_VERSION, "contract_draft": draft.model_dump(),
    }


@app.post("/api/v1/clearance/audio", response_model=ClearanceResponse, tags=["Clearance"])
async def request_audio_clearance(payload: ClearanceRequest) -> JSONResponse:
    if payload.audio_url and _is_royalty_free_url(payload.audio_url):
        match = AuddMatch(
            title="Indie Background Track",
            artist="Public Domain / Royalty-Free Provider",
        )
        licenses = _build_audio_licenses(royalty_free=True)
        total_fee = 0.0
        return JSONResponse(
            status_code=200,
            content=_audio_response(match, licenses, total_fee, payload.audio_url),
        )

    audd_response = _query_audd(payload.audio_url, payload.file_path, _get_audd_token())
    match = _extract_match(audd_response)
    licenses = _build_audio_licenses(_is_royalty_free(match.title, match.artist, audd_response))
    total_fee = sum(item["amount_usd"] for item in licenses)
    return JSONResponse(status_code=200, content=_audio_response(match, licenses, total_fee, payload.audio_url or ""))


@app.post("/api/v1/clearance/audio/direct", response_model=ClearanceResponse, tags=["Clearance"])
async def direct_audio_clearance(payload: DirectClearanceRequest) -> JSONResponse:
    match = AuddMatch(title=payload.song_title, artist=payload.artist)
    licenses = _build_audio_licenses(_is_royalty_free(payload.song_title, payload.artist))
    total_fee = sum(item["amount_usd"] for item in licenses)
    content = _audio_response(match, licenses, total_fee, "", payload.timestamp_start, payload.timestamp_end)
    content.update({"timestamp_start": payload.timestamp_start, "timestamp_end": payload.timestamp_end})
    return JSONResponse(status_code=200, content=content)


ALLOWED_AUDIO_TYPES = {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/aac", "audio/ogg", "audio/flac", "audio/mp4", "audio/x-m4a", "video/mp4"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@app.post("/api/v1/clearance/audio/upload", response_model=ClearanceResponse, tags=["Clearance"])
async def upload_audio_clearance(audio_file: UploadFile = File(...)) -> JSONResponse:
    content_type = (audio_file.content_type or "").lower().split(";", 1)[0].strip()
    if content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{content_type}'. Accepted: MP3, WAV, AAC, OGG, FLAC, M4A.")
    data = await audio_file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large. Maximum is 25 MB.")
    import tempfile
    token = _get_audd_token()
    tmp_path = ""
    try:
        suffix = os.path.splitext(audio_file.filename or "clip.mp3")[1] or ".mp3"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_file.write(data)
            tmp_path = temp_file.name
        audd_response = _query_audd(None, tmp_path, token)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    match = _extract_match(audd_response)
    licenses = _build_audio_licenses(_is_royalty_free(match.title, match.artist, audd_response))
    total_fee = sum(item["amount_usd"] for item in licenses)
    return JSONResponse(status_code=200, content=_audio_response(match, licenses, total_fee, f"Uploaded file: {audio_file.filename or 'audio clip'}"))


@app.post("/api/v1/clearance/approve")
async def mock_ibm_bob_clearance(payload: AudioMetadata):
    """
    Simulates the IBM Bob Audio Clearance response for the hackathon demo.
    Returns a deterministic compliance summary to complete the agent loop.
    """
    # Simple logic to determine tier based on input payload
    requires_manual_review = payload.film_budget < 10000
    
    return {
        "status": "success",
        "provider": "IBM_Bob_Audio_Clearance_Tool",
        "track_title": payload.track_title,
        "artist": payload.artist,
        "clearance_status": "Conditional Approval" if requires_manual_review else "Cleared for Production",
        "chain_of_title_ref": "COT-9842-IBM",
        "message": "Audio metadata successfully processed and cleared through orchestration pipeline."
    }


SCRIPT_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
SCRIPT_AUDIO_EXTENSIONS = (".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a")
SCRIPT_VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".avi")
SCRIPT_AUDIO_TERMS = ("audio", "music", "song", "track", "soundtrack", "radio", "broadcast", "recording", "listen", "plays", "playing", "heard")
MAX_SCRIPT_BYTES = 2 * 1024 * 1024


def _extract_script_candidates(script_text: str) -> list[dict[str, Any]]:
    candidates = []
    seen_urls: set[str] = set()
    for line_number, line in enumerate(script_text.splitlines(), start=1):
        context = line.strip()
        for match in SCRIPT_URL_PATTERN.finditer(line):
            url = match.group(0).rstrip(".,;:!?)]}")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            normalized_url = url.lower().split("?", 1)[0]
            lowered = f"{url} {context}".lower()
            likely_audio = normalized_url.endswith(SCRIPT_AUDIO_EXTENSIONS) or any(term in lowered for term in SCRIPT_AUDIO_TERMS)
            media_hint = "audio" if likely_audio else ("video" if normalized_url.endswith(SCRIPT_VIDEO_EXTENSIONS) else "unknown")
            candidates.append({"url": url, "line_number": line_number, "context": context[:240], "likely_audio": likely_audio, "media_hint": media_hint})
    return candidates


@app.post("/api/v1/scripts/scan", response_model=ScriptScanResponse, tags=["Script Scanning"])
async def scan_script(script_file: UploadFile = File(...)) -> ScriptScanResponse:
    filename = script_file.filename or "script.txt"
    if os.path.splitext(filename)[1].lower() not in {".txt", ".md", ".fountain"}:
        raise HTTPException(status_code=400, detail="Unsupported script type. Upload a UTF-8 .txt, .md, or .fountain file.")
    data = await script_file.read()
    if len(data) > MAX_SCRIPT_BYTES:
        raise HTTPException(status_code=400, detail="Script is too large. Maximum size is 2 MB.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Script must be UTF-8 encoded plain text.")
    candidates = _extract_script_candidates(text)
    return ScriptScanResponse(filename=filename, candidate_count=len(candidates), candidates=candidates, message="Review extracted URLs before sending audio candidates for clearance.")


frontend_dir = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend"))
if os.path.isdir(frontend_dir):
    app.mount("/ui", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    app.mount("/assets", StaticFiles(directory=frontend_dir), name="frontend-assets")

    @app.get("/ui")
    def serve_ui():
        return FileResponse(os.path.join(frontend_dir, "index.html"))
