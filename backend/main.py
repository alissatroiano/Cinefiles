"""
Cinefiles – Audio Copyright Clearance Microservice

Endpoints
---------
POST /api/v1/clearance/audio   – AudD audio fingerprint + fee estimate
POST /api/v1/clearance/asset   – Mock copyright DB lookup + PDF draft + human-approval gate
POST /api/v1/clearance/approve – Finalise a pending asset clearance after human review

Audio fingerprinting is performed by the AudD API (https://api.audd.io/).
The AudD API token must be set in the AUDD_API_TOKEN environment variable.
"""

from __future__ import annotations

import base64
import os
import textwrap
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import requests as http_client
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

load_dotenv()  # Loads variables from backend/.env

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYNC_LICENSE_BASE_FEE: float = 15_000.00    # USD – Synchronisation right
MASTER_LICENSE_BASE_FEE: float = 15_000.00  # USD – Master recording right

AUDD_API_URL = "https://api.audd.io/"

API_VERSION = "1.0.0"
SERVICE_NAME = "Cinefiles Audio Copyright Clearance Service"

# ---------------------------------------------------------------------------
# Mock copyright database
# Keyed by normalised lower-case asset_name substring for fuzzy matching.
# ---------------------------------------------------------------------------

MOCK_COPYRIGHT_DB: dict[str, dict[str, Any]] = {
    "nike": {
        "asset_name": "Nike Swoosh Logo",
        "rights_holder": "Nike, Inc.",
        "registration_id": "TM-US-1977-0823",
        "asset_type": "trademark",
        "risk_level": "high",
        "clearance_contact": "legal@nike.com",
    },
    "coca-cola": {
        "asset_name": "Coca-Cola Script Logo",
        "rights_holder": "The Coca-Cola Company",
        "registration_id": "TM-US-1893-0042",
        "asset_type": "trademark",
        "risk_level": "high",
        "clearance_contact": "trademarks@coca-cola.com",
    },
    "apple": {
        "asset_name": "Apple Logo (Bitten Apple)",
        "rights_holder": "Apple Inc.",
        "registration_id": "TM-US-1977-1119",
        "asset_type": "trademark",
        "risk_level": "high",
        "clearance_contact": "trademark@apple.com",
    },
    "starbucks": {
        "asset_name": "Starbucks Siren Logo",
        "rights_holder": "Starbucks Corporation",
        "registration_id": "TM-US-1987-0305",
        "asset_type": "trademark",
        "risk_level": "medium",
        "clearance_contact": "legal@starbucks.com",
    },
    "adidas": {
        "asset_name": "Adidas Three Stripes",
        "rights_holder": "adidas AG",
        "registration_id": "TM-DE-1952-0818",
        "asset_type": "trademark",
        "risk_level": "medium",
        "clearance_contact": "brand.protection@adidas.com",
    },
}

# ---------------------------------------------------------------------------
# Contract templates
# One template per risk_level. {placeholders} are filled at request time.
# ---------------------------------------------------------------------------

CONTRACT_TEMPLATES: dict[str, str] = {
    "high": textwrap.dedent("""\
        CLEARANCE AGREEMENT — HIGH-RISK ASSET
        ======================================
        Date:           {date}
        Clearance ID:   {clearance_id}
        Production:     {production_title}
        Asset:          {asset_name}
        Timestamp:      {timestamp}
        Rights Holder:  {rights_holder}
        Contact:        {clearance_contact}

        This agreement constitutes a formal request for synchronisation clearance
        of the above-identified asset as it appears incidentally in the production
        footage at the stated timestamp.

        HIGH-RISK NOTICE: This asset is a registered trademark of {rights_holder}.
        Commercial distribution requires written clearance from the rights holder
        prior to release. Failure to obtain clearance may result in statutory
        damages of up to $150,000 per wilful infringement under 17 U.S.C. § 504.

        ESTIMATED CLEARANCE FEE: ${estimated_fee:,.0f} USD
        STATUS: PENDING HUMAN APPROVAL

        Approved by: ______________________    Date: ____________
    """),
    "medium": textwrap.dedent("""\
        CLEARANCE AGREEMENT — STANDARD ASSET
        ======================================
        Date:           {date}
        Clearance ID:   {clearance_id}
        Production:     {production_title}
        Asset:          {asset_name}
        Timestamp:      {timestamp}
        Rights Holder:  {rights_holder}
        Contact:        {clearance_contact}

        This agreement constitutes a standard clearance request for the above asset.
        A written licence from {rights_holder} is required for commercial release.

        ESTIMATED CLEARANCE FEE: ${estimated_fee:,.0f} USD
        STATUS: PENDING HUMAN APPROVAL

        Approved by: ______________________    Date: ____________
    """),
    "low": textwrap.dedent("""\
        CLEARANCE NOTICE — LOW-RISK ASSET
        ==================================
        Date:           {date}
        Clearance ID:   {clearance_id}
        Asset:          {asset_name}
        Timestamp:      {timestamp}

        This asset has been assessed as low-risk. Document and retain this notice
        for your Errors & Omissions insurance file.

        ESTIMATED CLEARANCE FEE: ${estimated_fee:,.0f} USD
        STATUS: PENDING HUMAN APPROVAL
    """),
}

# ---------------------------------------------------------------------------
# In-memory clearance store  { clearance_id: AssetClearanceRecord }
# In production replace with a Cloud Firestore / Cloud SQL write.
# ---------------------------------------------------------------------------

_clearance_store: dict[str, dict[str, Any]] = {}


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


# ── Asset clearance models ────────────────────────────────────────────────


class AssetClearanceRequest(BaseModel):
    """Payload for a visual/brand asset clearance request."""

    asset_name: str = Field(
        ...,
        min_length=1,
        description="Name or description of the asset detected in the footage.",
        examples=["Nike Swoosh Logo"],
    )
    timestamp: str = Field(
        ...,
        min_length=1,
        description="Timecode in the footage where the asset appears (e.g. '00:04:32').",
        examples=["00:04:32"],
    )
    production_title: str = Field(
        default="Untitled Production",
        description="Title of the film or production for the contract header.",
        examples=["My Indie Film"],
    )


class PdfDraftPayload(BaseModel):
    """Base-64 encoded PDF contract draft."""

    filename: str
    content_type: Literal["application/pdf"]
    data_base64: str
    page_count: int


class AssetClearanceResponse(BaseModel):
    """Response returned immediately after submitting an asset clearance request."""

    clearance_id: str
    status: Literal["pending_human_approval"]
    asset_name: str
    matched_record: Optional[dict] = None
    risk_level: str
    estimated_fee_usd: float
    contract_template_used: str
    pdf_draft: PdfDraftPayload
    submitted_at: str
    message: str


class ApproveRequest(BaseModel):
    """Payload for the human-approval endpoint."""

    clearance_id: str = Field(
        ...,
        description="The clearance_id returned by POST /api/v1/clearance/asset.",
        examples=["clr_a1b2c3d4"],
    )
    approver_name: str = Field(
        ...,
        min_length=1,
        description="Full name of the human approver.",
        examples=["Jane Smith"],
    )
    approver_notes: Optional[str] = Field(
        default=None,
        description="Optional notes or conditions attached to the approval.",
        examples=["Cleared for festival distribution only."],
    )


class ApproveResponse(BaseModel):
    """Response returned after a clearance is finalised."""

    clearance_id: str
    status: Literal["approved"]
    asset_name: str
    approver_name: str
    approver_notes: Optional[str]
    approved_at: str
    pdf_final: PdfDraftPayload
    message: str


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
        "Microservice that (1) fingerprints audio clips via the AudD API to "
        "estimate Sync & Master licensing fees, and (2) searches a copyright "
        "database for visual/brand assets, generates a draft clearance PDF, and "
        "enforces a human-in-the-loop approval gate before finalising the clearance."
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
async def request_audio_clearance(payload: ClearanceRequest) -> JSONResponse:  # noqa: F811
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


# ---------------------------------------------------------------------------
# Helpers — asset clearance
# ---------------------------------------------------------------------------


def _search_copyright_db(asset_name: str) -> Optional[dict]:
    """Return the first matching mock DB record or None."""
    needle = asset_name.lower()
    for key, record in MOCK_COPYRIGHT_DB.items():
        if key in needle or needle in key:
            return record
    return None


def _estimate_fee(risk_level: str) -> float:
    """Return a flat clearance fee estimate based on risk level."""
    return {"high": 25_000.0, "medium": 10_000.0, "low": 2_500.0}.get(risk_level, 5_000.0)


def _build_contract_text(
    template_key: str,
    clearance_id: str,
    asset_name: str,
    timestamp: str,
    production_title: str,
    rights_holder: str,
    clearance_contact: str,
    estimated_fee: float,
    approved_by: Optional[str] = None,
    approved_at: Optional[str] = None,
) -> str:
    """Fill the chosen contract template and optionally stamp an approval."""
    template = CONTRACT_TEMPLATES.get(template_key, CONTRACT_TEMPLATES["low"])
    text = template.format(
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        clearance_id=clearance_id,
        production_title=production_title,
        asset_name=asset_name,
        timestamp=timestamp,
        rights_holder=rights_holder,
        clearance_contact=clearance_contact,
        estimated_fee=estimated_fee,
    )
    if approved_by and approved_at:
        text = text.replace(
            "Approved by: ______________________    Date: ____________",
            f"Approved by: {approved_by}    Date: {approved_at}",
        )
        text = text.replace("STATUS: PENDING HUMAN APPROVAL", "STATUS: APPROVED")
    return text


def _encode_pdf_payload(contract_text: str, filename: str) -> PdfDraftPayload:
    """
    Encode the contract as a minimal plain-text 'PDF' payload.

    In production swap this body for a real PDF renderer such as ReportLab or
    WeasyPrint.  The base64-encoded bytes are already valid for download or
    e-mail attachment — the recipient gets a readable plain-text file.
    """
    raw = contract_text.encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return PdfDraftPayload(
        filename=filename,
        content_type="application/pdf",
        data_base64=encoded,
        page_count=1,
    )


# ---------------------------------------------------------------------------
# Endpoint — asset clearance (submit)
# ---------------------------------------------------------------------------


@app.post(
    "/api/v1/clearance/asset",
    response_model=AssetClearanceResponse,
    summary="Submit a visual/brand asset for copyright clearance",
    description=(
        "Accepts an asset name and footage timestamp, searches a mock copyright "
        "database, matches the appropriate contract template, calculates an "
        "estimated clearance fee, generates a draft PDF contract, and places the "
        "clearance in 'pending_human_approval' status until /approve is called."
    ),
    tags=["Asset Clearance"],
    responses={
        200: {"description": "Asset clearance draft created — pending human approval."},
        422: {"description": "Validation error – invalid request payload."},
    },
)
async def submit_asset_clearance(payload: AssetClearanceRequest) -> JSONResponse:
    clearance_id = f"clr_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    matched = _search_copyright_db(payload.asset_name)

    if matched:
        risk_level = matched["risk_level"]
        rights_holder = matched["rights_holder"]
        clearance_contact = matched["clearance_contact"]
        canonical_name = matched["asset_name"]
    else:
        # Unknown asset — default to medium risk and generic values
        risk_level = "medium"
        rights_holder = "Unknown Rights Holder"
        clearance_contact = "clearance@example.com"
        canonical_name = payload.asset_name

    template_key = risk_level if risk_level in CONTRACT_TEMPLATES else "low"
    estimated_fee = _estimate_fee(risk_level)

    contract_text = _build_contract_text(
        template_key=template_key,
        clearance_id=clearance_id,
        asset_name=canonical_name,
        timestamp=payload.timestamp,
        production_title=payload.production_title,
        rights_holder=rights_holder,
        clearance_contact=clearance_contact,
        estimated_fee=estimated_fee,
    )

    pdf_payload = _encode_pdf_payload(
        contract_text, filename=f"{clearance_id}_draft.pdf"
    )

    record: dict[str, Any] = {
        "clearance_id": clearance_id,
        "status": "pending_human_approval",
        "asset_name": canonical_name,
        "raw_asset_name": payload.asset_name,
        "timestamp": payload.timestamp,
        "production_title": payload.production_title,
        "matched_record": matched,
        "risk_level": risk_level,
        "rights_holder": rights_holder,
        "clearance_contact": clearance_contact,
        "estimated_fee_usd": estimated_fee,
        "contract_template_used": template_key,
        "contract_text": contract_text,
        "pdf_draft": pdf_payload.model_dump(),
        "submitted_at": now,
    }
    _clearance_store[clearance_id] = record

    response_body = {
        "clearance_id": clearance_id,
        "status": "pending_human_approval",
        "asset_name": canonical_name,
        "matched_record": matched,
        "risk_level": risk_level,
        "estimated_fee_usd": estimated_fee,
        "contract_template_used": template_key,
        "pdf_draft": pdf_payload.model_dump(),
        "submitted_at": now,
        "message": (
            f"Clearance draft created for '{canonical_name}'. "
            "Review the PDF contract and call POST /api/v1/clearance/approve "
            f"with clearance_id='{clearance_id}' to finalise."
        ),
    }
    return JSONResponse(status_code=200, content=response_body)


# ---------------------------------------------------------------------------
# Endpoint — approve clearance (human-in-the-loop finalisation)
# ---------------------------------------------------------------------------


@app.post(
    "/api/v1/clearance/approve",
    response_model=ApproveResponse,
    summary="Finalise a pending asset clearance after human review",
    description=(
        "Accepts a clearance_id and approver details, transitions the clearance "
        "from 'pending_human_approval' to 'approved', stamps the PDF contract "
        "with the approver name and timestamp, and returns the final record."
    ),
    tags=["Asset Clearance"],
    responses={
        200: {"description": "Clearance approved and finalised."},
        404: {"description": "Clearance ID not found."},
        409: {"description": "Clearance is not in pending_human_approval status."},
        422: {"description": "Validation error – invalid request payload."},
    },
)
async def approve_asset_clearance(payload: ApproveRequest) -> JSONResponse:
    record = _clearance_store.get(payload.clearance_id)
    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"Clearance ID '{payload.clearance_id}' not found.",
        )
    if record["status"] != "pending_human_approval":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Clearance '{payload.clearance_id}' has status '{record['status']}' "
                "and cannot be approved again."
            ),
        )

    approved_at = datetime.now(timezone.utc).isoformat()

    final_text = _build_contract_text(
        template_key=record["contract_template_used"],
        clearance_id=record["clearance_id"],
        asset_name=record["asset_name"],
        timestamp=record["timestamp"],
        production_title=record["production_title"],
        rights_holder=record["rights_holder"],
        clearance_contact=record["clearance_contact"],
        estimated_fee=record["estimated_fee_usd"],
        approved_by=payload.approver_name,
        approved_at=approved_at,
    )

    pdf_final = _encode_pdf_payload(
        final_text, filename=f"{record['clearance_id']}_approved.pdf"
    )

    record["status"] = "approved"
    record["approver_name"] = payload.approver_name
    record["approver_notes"] = payload.approver_notes
    record["approved_at"] = approved_at
    record["pdf_final"] = pdf_final.model_dump()

    response_body = {
        "clearance_id": record["clearance_id"],
        "status": "approved",
        "asset_name": record["asset_name"],
        "approver_name": payload.approver_name,
        "approver_notes": payload.approver_notes,
        "approved_at": approved_at,
        "pdf_final": pdf_final.model_dump(),
        "message": (
            f"Clearance for '{record['asset_name']}' has been approved by "
            f"{payload.approver_name}. The signed PDF contract is attached."
        ),
    }
    return JSONResponse(status_code=200, content=response_body)
