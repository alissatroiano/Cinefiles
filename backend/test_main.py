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
