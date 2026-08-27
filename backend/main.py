"""
Cinefiles – Audio Copyright Clearance Microservice
FastAPI application exposing POST /api/v1/clearance/audio

Audio fingerprinting is performed by the AudD API (https://api.audd.io/).
The AudD API token must be set in the AUDD_API_TOKEN environment variable.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Literal, Optional

import requests as http_client
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYNC_LICENSE_BASE_FEE: float = 15_000.00    # USD – Synchronisation right
MASTER_LICENSE_BASE_FEE: float = 15_000.00  # USD – Master recording right

AUDD_API_URL = "https://api.audd.io/"

API_VERSION = "1.0.0"
SERVICE_NAME = "Cinefiles Audio Copyright Clearance Service"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ClearanceRequest(BaseModel):
    """Payload for an audio clearance request.

    Supply exactly one of `audio_url` (a publicly accessible URL) or
    `file_path` (a server-side absolute or relative path to an audio file).
    """

    audio_url: Optional[str] = Field(
        default=None,
        description="Publicly accessible URL of the audio file to identify.",
        examples=["https://example.com/clip.mp3"],
    )
    file_path: Optional[str] = Field(
        default=None,
        description="Server-side file-system path to the audio file to identify.",
        examples=["/uploads/clip.mp3"],
    )

    @model_validator(mode="after")
    def exactly_one_source(self) -> "ClearanceRequest":
        has_url = bool(self.audio_url and self.audio_url.strip())
        has_path = bool(self.file_path and self.file_path.strip())
        if has_url == has_path:  # both truthy or both falsy
            raise ValueError(
                "Provide exactly one of 'audio_url' or 'file_path', not both or neither."
            )
        return self


class LicenseFee(BaseModel):
    """Individual licence fee line-item."""

    license_type: Literal["Sync", "Master"]
    description: str
    amount_usd: float


class AuddMatch(BaseModel):
    """Subset of AudD match data surfaced in the response."""

    title: str
    artist: str
    apple_music_link: Optional[str] = None


class ClearanceResponse(BaseModel):
    """Full clearance response payload."""

    status: Literal["approved", "pending", "denied"]
    match: AuddMatch
    licenses: list[LicenseFee]
    total_fee_usd: float
    currency: Literal["USD"]
    requested_at: str   # ISO-8601 UTC
    service: str
    version: str


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title=SERVICE_NAME,
    version=API_VERSION,
    description=(
        "Microservice that fingerprints an audio clip via the AudD API, "
        "identifies the track, and returns estimated Sync and Master recording "
        "licensing fees required to clear the copyright for use in film, "
        "television, or digital media."
    ),
    contact={"name": "Cinefiles", "url": "https://github.com/Cinefiles"},
    license_info={"name": "MIT"},
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_audd_token() -> str:
    token = os.environ.get("AUDD_API_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=500,
            detail="Server configuration error: AUDD_API_TOKEN is not set.",
        )
    return token


def _query_audd(audio_url: Optional[str], file_path: Optional[str], token: str) -> dict:
    """Call the AudD recognition API and return the raw JSON response dict."""
    data = {
        "api_token": token,
        "return": "apple_music",
    }

    try:
        if audio_url:
            data["url"] = audio_url
            resp = http_client.post(AUDD_API_URL, data=data, timeout=30)
        else:
            with open(file_path, "rb") as audio_file:  # type: ignore[arg-type]
                resp = http_client.post(
                    AUDD_API_URL,
                    data=data,
                    files={"file": audio_file},
                    timeout=30,
                )
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=f"File not found: {file_path}")
    except http_client.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="AudD API request timed out.")
    except http_client.exceptions.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"AudD API unreachable: {exc}")

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"AudD API returned HTTP {resp.status_code}.",
        )

    return resp.json()


def _extract_match(audd_response: dict) -> AuddMatch:
    """Parse the AudD JSON and extract title, artist, and Apple Music link."""
    if audd_response.get("status") != "success":
        raise HTTPException(
            status_code=502,
            detail=f"AudD API error: {audd_response.get('error', {}).get('error_message', 'unknown')}",
        )

    result = audd_response.get("result")
    if not result:
        raise HTTPException(
            status_code=404,
            detail="No matching track found for the provided audio.",
        )

    apple_music_data = result.get("apple_music") or {}
    apple_music_link: Optional[str] = (
        apple_music_data.get("url") or None
    )

    return AuddMatch(
        title=result.get("title", "Unknown Title"),
        artist=result.get("artist", "Unknown Artist"),
        apple_music_link=apple_music_link,
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@app.post(
    "/api/v1/clearance/audio",
    response_model=ClearanceResponse,
    summary="Request audio copyright clearance",
    description=(
        "Accepts a raw audio URL or server-side file path, fingerprints the clip "
        "via the AudD API, extracts the matched artist, title, and Apple Music "
        "link, calculates estimated Sync and Master recording licensing fees, "
        "and returns a structured clearance response."
    ),
    tags=["Clearance"],
    responses={
        200: {"description": "Clearance estimate successfully generated."},
        400: {"description": "Bad request – invalid file path or audio source."},
        404: {"description": "No matching track found for the provided audio."},
        422: {"description": "Validation error – invalid request payload."},
        502: {"description": "AudD API error or unreachable."},
        504: {"description": "AudD API request timed out."},
    },
)
async def request_audio_clearance(payload: ClearanceRequest) -> JSONResponse:
    token = _get_audd_token()
    audd_response = _query_audd(payload.audio_url, payload.file_path, token)
    match = _extract_match(audd_response)

    licenses: list[dict] = [
        {
            "license_type": "Sync",
            "description": (
                "Synchronisation licence – grants the right to pair the "
                "musical composition with visual media."
            ),
            "amount_usd": SYNC_LICENSE_BASE_FEE,
        },
        {
            "license_type": "Master",
            "description": (
                "Master recording licence – grants the right to use the "
                "specific sound recording owned by the record label."
            ),
            "amount_usd": MASTER_LICENSE_BASE_FEE,
        },
    ]

    total_fee = sum(lic["amount_usd"] for lic in licenses)

    response_body = {
        "status": "approved",
        "match": {
            "title": match.title,
            "artist": match.artist,
            "apple_music_link": match.apple_music_link,
        },
        "licenses": licenses,
        "total_fee_usd": total_fee,
        "currency": "USD",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "service": SERVICE_NAME,
        "version": API_VERSION,
    }

    return JSONResponse(status_code=200, content=response_body)
