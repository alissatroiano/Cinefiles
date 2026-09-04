"""
Unit tests for Cinefiles Audio Copyright Clearance Microservice.

Run:
    cd backend
    pip install pytest httpx
    pytest test_main.py -v
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure token env var is set before importing the app so _get_audd_token
# doesn't raise during module-level TestClient construction.
os.environ.setdefault("AUDD_API_TOKEN", "test-token")

from main import app, _extract_match, _get_audd_token, AuddMatch  # noqa: E402

client = TestClient(app, raise_server_exceptions=False)

# ---------------------------------------------------------------------------
# Fixtures / shared helpers
# ---------------------------------------------------------------------------

AUDD_SUCCESS = {
    "status": "success",
    "result": {
        "title": "Blinding Lights",
        "artist": "The Weeknd",
        "apple_music": {
            "url": "https://music.apple.com/album/blinding-lights/1499378560"
        },
    },
}

AUDD_NO_MATCH = {
    "status": "success",
    "result": None,
}

AUDD_API_ERROR = {
    "status": "error",
    "error": {"error_message": "Invalid API token"},
}


def _mock_audd_response(payload: dict, status_code: int = 200) -> MagicMock:
    """Return a mock requests.Response with the given JSON payload."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = payload
    return mock_resp


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


class TestRequestValidation:
    def test_rejects_empty_body(self):
        resp = client.post("/api/v1/clearance/audio", json={})
        assert resp.status_code == 422

    def test_rejects_both_fields_supplied(self):
        resp = client.post(
            "/api/v1/clearance/audio",
            json={"audio_url": "https://example.com/a.mp3", "file_path": "/tmp/a.mp3"},
        )
        assert resp.status_code == 422

    def test_rejects_blank_audio_url(self):
        resp = client.post("/api/v1/clearance/audio", json={"audio_url": "   "})
        assert resp.status_code == 422

    def test_rejects_blank_file_path(self):
        resp = client.post("/api/v1/clearance/audio", json={"file_path": ""})
        assert resp.status_code == 422

    def test_accepts_audio_url_only(self):
        with patch("main._query_audd", return_value=AUDD_SUCCESS):
            resp = client.post(
                "/api/v1/clearance/audio",
                json={"audio_url": "https://example.com/clip.mp3"},
            )
        assert resp.status_code == 200

    def test_accepts_file_path_only(self):
        with patch("main._query_audd", return_value=AUDD_SUCCESS):
            resp = client.post(
                "/api/v1/clearance/audio",
                json={"file_path": "/uploads/clip.mp3"},
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Successful clearance response shape
# ---------------------------------------------------------------------------


class TestSuccessfulClearance:
    @pytest.fixture(autouse=True)
    def mock_audd(self):
        with patch("main._query_audd", return_value=AUDD_SUCCESS):
            yield

    def _post(self):
        return client.post(
            "/api/v1/clearance/audio",
            json={"audio_url": "https://example.com/clip.mp3"},
        )

    def test_status_200(self):
        assert self._post().status_code == 200

    def test_response_status_approved(self):
        assert self._post().json()["status"] == "approved"

    def test_match_title(self):
        assert self._post().json()["match"]["title"] == "Blinding Lights"

    def test_match_artist(self):
        assert self._post().json()["match"]["artist"] == "The Weeknd"

    def test_match_apple_music_link(self):
        link = self._post().json()["match"]["apple_music_link"]
        assert link == "https://music.apple.com/album/blinding-lights/1499378560"

    def test_two_licenses_returned(self):
        assert len(self._post().json()["licenses"]) == 2

    def test_sync_fee(self):
        licenses = self._post().json()["licenses"]
        sync = next(l for l in licenses if l["license_type"] == "Sync")
        assert sync["amount_usd"] == 15_000.0

    def test_master_fee(self):
        licenses = self._post().json()["licenses"]
        master = next(l for l in licenses if l["license_type"] == "Master")
        assert master["amount_usd"] == 15_000.0

    def test_total_fee(self):
        assert self._post().json()["total_fee_usd"] == 30_000.0

    def test_currency_usd(self):
        assert self._post().json()["currency"] == "USD"

    def test_requested_at_present(self):
        assert self._post().json()["requested_at"]

    def test_service_name(self):
        assert "Cinefiles" in self._post().json()["service"]

    def test_version_present(self):
        assert self._post().json()["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# No match found (404)
# ---------------------------------------------------------------------------


class TestNoMatch:
    def test_returns_404_when_audd_result_is_null(self):
        with patch("main._query_audd", return_value=AUDD_NO_MATCH):
            resp = client.post(
                "/api/v1/clearance/audio",
                json={"audio_url": "https://example.com/silence.mp3"},
            )
        assert resp.status_code == 404
        assert "No matching track" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# AudD API error (502)
# ---------------------------------------------------------------------------


class TestAuddApiError:
    def test_returns_502_on_audd_error_status(self):
        with patch("main._query_audd", return_value=AUDD_API_ERROR):
            resp = client.post(
                "/api/v1/clearance/audio",
                json={"audio_url": "https://example.com/clip.mp3"},
            )
        assert resp.status_code == 502
        assert "Invalid API token" in resp.json()["detail"]

    def test_returns_502_on_non_200_http(self):
        with patch(
            "main.http_client.post",
            return_value=_mock_audd_response({}, status_code=503),
        ):
            resp = client.post(
                "/api/v1/clearance/audio",
                json={"audio_url": "https://example.com/clip.mp3"},
            )
        assert resp.status_code == 502

    def test_returns_504_on_timeout(self):
        import requests as req
        with patch("main.http_client.post", side_effect=req.exceptions.Timeout):
            resp = client.post(
                "/api/v1/clearance/audio",
                json={"audio_url": "https://example.com/clip.mp3"},
            )
        assert resp.status_code == 504

    def test_returns_400_on_missing_file(self):
        with patch("main.http_client.post", side_effect=FileNotFoundError):
            resp = client.post(
                "/api/v1/clearance/audio",
                json={"file_path": "/nonexistent/clip.mp3"},
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Missing token (500)
# ---------------------------------------------------------------------------


class TestMissingToken:
    def test_returns_500_when_token_unset(self):
        with patch.dict(os.environ, {"AUDD_API_TOKEN": ""}):
            resp = client.post(
                "/api/v1/clearance/audio",
                json={"audio_url": "https://example.com/clip.mp3"},
            )
        assert resp.status_code == 500
        assert "AUDD_API_TOKEN" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# _extract_match unit tests (no HTTP)
# ---------------------------------------------------------------------------


class TestExtractMatch:
    def test_extracts_title_and_artist(self):
        match = _extract_match(AUDD_SUCCESS)
        assert match.title == "Blinding Lights"
        assert match.artist == "The Weeknd"

    def test_extracts_apple_music_link(self):
        match = _extract_match(AUDD_SUCCESS)
        assert match.apple_music_link is not None
        assert "apple.com" in match.apple_music_link

    def test_apple_music_link_is_none_when_absent(self):
        payload = {
            "status": "success",
            "result": {"title": "Track", "artist": "Artist"},
        }
        match = _extract_match(payload)
        assert match.apple_music_link is None


# ---------------------------------------------------------------------------
# Asset clearance — POST /api/v1/clearance/asset
# ---------------------------------------------------------------------------


class TestAssetClearanceSubmit:
    URL = "/api/v1/clearance/asset"

    def _post(self, body: dict):
        return client.post(self.URL, json=body)

    # ── Validation ──────────────────────────────────────────────────────────

    def test_rejects_missing_asset_name(self):
        assert self._post({"timestamp": "00:01:00"}).status_code == 422

    def test_rejects_missing_timestamp(self):
        assert self._post({"asset_name": "Nike Logo"}).status_code == 422

    def test_rejects_empty_asset_name(self):
        assert self._post({"asset_name": "", "timestamp": "00:01:00"}).status_code == 422

    # ── Known asset (Nike → high risk) ──────────────────────────────────────

    def test_returns_200_for_known_asset(self):
        resp = self._post({"asset_name": "Nike Swoosh", "timestamp": "00:02:15"})
        assert resp.status_code == 200

    def test_status_is_pending_human_approval(self):
        resp = self._post({"asset_name": "Nike Swoosh", "timestamp": "00:02:15"})
        assert resp.json()["status"] == "pending_human_approval"

    def test_clearance_id_present(self):
        resp = self._post({"asset_name": "Nike Swoosh", "timestamp": "00:02:15"})
        cid = resp.json().get("clearance_id", "")
        assert cid.startswith("clr_")

    def test_risk_level_high_for_nike(self):
        resp = self._post({"asset_name": "Nike logo on hoodie", "timestamp": "00:00:30"})
        assert resp.json()["risk_level"] == "high"

    def test_estimated_fee_high_risk(self):
        resp = self._post({"asset_name": "Nike logo", "timestamp": "00:00:30"})
        assert resp.json()["estimated_fee_usd"] == 25_000.0

    def test_matched_record_returned(self):
        resp = self._post({"asset_name": "Nike logo", "timestamp": "00:00:30"})
        assert resp.json()["matched_record"] is not None
        assert resp.json()["matched_record"]["rights_holder"] == "Nike, Inc."

    def test_pdf_draft_present(self):
        resp = self._post({"asset_name": "Nike logo", "timestamp": "00:00:30"})
        pdf = resp.json()["pdf_draft"]
        assert pdf["content_type"] == "application/pdf"
        assert pdf["data_base64"]
        assert pdf["page_count"] == 1

    def test_pdf_filename_has_draft_suffix(self):
        resp = self._post({"asset_name": "Nike logo", "timestamp": "00:00:30"})
        assert "_draft.pdf" in resp.json()["pdf_draft"]["filename"]

    def test_message_contains_approve_instruction(self):
        resp = self._post({"asset_name": "Nike logo", "timestamp": "00:00:30"})
        assert "approve" in resp.json()["message"].lower()

    # ── Unknown asset (falls back to medium risk) ────────────────────────────

    def test_unknown_asset_returns_medium_risk(self):
        resp = self._post({"asset_name": "Random Brand XYZ", "timestamp": "00:05:00"})
        assert resp.json()["risk_level"] == "medium"

    def test_unknown_asset_fee_is_10000(self):
        resp = self._post({"asset_name": "Totally Unknown Brand", "timestamp": "00:05:00"})
        assert resp.json()["estimated_fee_usd"] == 10_000.0

    def test_unknown_asset_matched_record_is_none(self):
        resp = self._post({"asset_name": "Totally Unknown Brand 999", "timestamp": "00:05:00"})
        assert resp.json()["matched_record"] is None

    # ── Production title optional field ─────────────────────────────────────

    def test_custom_production_title_accepted(self):
        resp = self._post({
            "asset_name": "Starbucks cup",
            "timestamp": "00:10:00",
            "production_title": "My Indie Film",
        })
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Asset clearance — POST /api/v1/clearance/approve
# ---------------------------------------------------------------------------


class TestAssetClearanceApprove:
    ASSET_URL   = "/api/v1/clearance/asset"
    APPROVE_URL = "/api/v1/clearance/approve"

    def _submit(self, asset_name="Nike logo", timestamp="00:01:00") -> str:
        """Submit a clearance and return the clearance_id."""
        resp = client.post(self.ASSET_URL, json={"asset_name": asset_name, "timestamp": timestamp})
        assert resp.status_code == 200
        return resp.json()["clearance_id"]

    def _approve(self, clearance_id: str, name="Jane Smith", notes=None):
        body = {"clearance_id": clearance_id, "approver_name": name}
        if notes:
            body["approver_notes"] = notes
        return client.post(self.APPROVE_URL, json=body)

    # ── Happy path ───────────────────────────────────────────────────────────

    def test_approve_returns_200(self):
        cid = self._submit()
        assert self._approve(cid).status_code == 200

    def test_approved_status(self):
        cid = self._submit()
        assert self._approve(cid).json()["status"] == "approved"

    def test_approver_name_echoed(self):
        cid = self._submit()
        resp = self._approve(cid, name="John Doe")
        assert resp.json()["approver_name"] == "John Doe"

    def test_approver_notes_echoed(self):
        cid = self._submit()
        resp = self._approve(cid, notes="Festival use only.")
        assert resp.json()["approver_notes"] == "Festival use only."

    def test_approved_at_present(self):
        cid = self._submit()
        assert self._approve(cid).json()["approved_at"]

    def test_final_pdf_present(self):
        cid = self._submit()
        pdf = self._approve(cid).json()["pdf_final"]
        assert pdf["content_type"] == "application/pdf"
        assert pdf["data_base64"]

    def test_final_pdf_filename_has_approved_suffix(self):
        cid = self._submit()
        pdf = self._approve(cid).json()["pdf_final"]
        assert "_approved.pdf" in pdf["filename"]

    def test_message_contains_approver_name(self):
        cid = self._submit()
        resp = self._approve(cid, name="Alice")
        assert "Alice" in resp.json()["message"]

    # ── Error cases ──────────────────────────────────────────────────────────

    def test_unknown_clearance_id_returns_404(self):
        resp = client.post(self.APPROVE_URL, json={
            "clearance_id": "clr_doesnotexist",
            "approver_name": "Bob",
        })
        assert resp.status_code == 404

    def test_double_approve_returns_409(self):
        cid = self._submit()
        self._approve(cid)                    # first approval — OK
        resp = self._approve(cid)             # second approval — conflict
        assert resp.status_code == 409

    def test_missing_approver_name_returns_422(self):
        cid = self._submit()
        resp = client.post(self.APPROVE_URL, json={"clearance_id": cid})
        assert resp.status_code == 422
