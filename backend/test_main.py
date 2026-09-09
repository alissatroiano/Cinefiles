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

from main import (  # noqa: E402
    app, _extract_match, _get_audd_token, _is_royalty_free, _build_audio_licenses,
    _extract_script_candidates, _is_royalty_free_url, AuddMatch,
    SYNC_LICENSE_BASE_FEE, MASTER_LICENSE_BASE_FEE,
)

client = TestClient(app, raise_server_exceptions=False)


class TestScriptUrlScanning:
    def test_extracts_unique_audio_url_with_context(self):
        text = "INT. BAR - NIGHT\nMusic plays: https://example.com/song.mp3\nSame URL: https://example.com/song.mp3"

        candidates = _extract_script_candidates(text)

        assert candidates == [{
            "url": "https://example.com/song.mp3",
            "line_number": 2,
            "context": "Music plays: https://example.com/song.mp3",
            "likely_audio": True,
            "media_hint": "audio",
        }]

    def test_classifies_video_and_unknown_urls(self):
        candidates = _extract_script_candidates(
            "Watch https://example.com/scene.mp4\nSee https://example.com/notes"
        )

        assert candidates[0]["media_hint"] == "video"
        assert candidates[0]["likely_audio"] is False
        assert candidates[1]["media_hint"] == "unknown"

    def test_strips_sentence_punctuation(self):
        candidates = _extract_script_candidates("Audio: https://example.com/clip.wav.")

        assert candidates[0]["url"] == "https://example.com/clip.wav"


class TestScriptScanEndpoint:
    URL = "/api/v1/scripts/scan"

    def test_scans_utf8_text_file(self):
        response = client.post(
            self.URL,
            files={"script_file": ("scene.txt", "Music plays https://example.com/track.mp3", "text/plain")},
        )

        assert response.status_code == 200
        assert response.json()["candidate_count"] == 1
        assert response.json()["candidates"][0]["likely_audio"] is True

    def test_rejects_unsupported_file_type(self):
        response = client.post(
            self.URL,
            files={"script_file": ("scene.pdf", b"not a text script", "application/pdf")},
        )

        assert response.status_code == 400

    def test_rejects_non_utf8_text_file(self):
        response = client.post(
            self.URL,
            files={"script_file": ("scene.txt", b"\xff\xfe", "text/plain")},
        )

        assert response.status_code == 400

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

    def test_url_markers_classify_explicit_free_sources(self):
        assert _is_royalty_free_url("https://example.com/royalty-free/track.mp3") is True
        assert _is_royalty_free_url("https://example.com/public-domain/track.mp3") is True
        assert _is_royalty_free_url("https://example.com/commercial/track.mp3") is False

    def test_explicit_free_url_skips_audd_and_returns_zero_fees(self):
        with patch("main._query_audd") as query_audd:
            response = client.post(
                "/api/v1/clearance/audio",
                json={"audio_url": "https://example.com/freemusic/track.mp3"},
            )

        assert response.status_code == 200
        assert response.json()["royalty_free"] is True
        assert response.json()["total_fee_usd"] == 0.0
        assert len(response.json()["licenses"]) == 2
        query_audd.assert_not_called()


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

    def test_audio_contract_draft_present(self):
        import base64
        from pypdf import PdfReader
        from io import BytesIO

        draft = self._post().json()["contract_draft"]
        assert draft["content_type"] == "application/pdf"
        assert draft["data_base64"]
        pdf_bytes = base64.b64decode(draft["data_base64"])
        assert pdf_bytes.startswith(b"%PDF-")
        assert len(PdfReader(BytesIO(pdf_bytes)).pages) == 1

    def test_audio_contract_draft_mentions_track(self):
        import base64
        from io import BytesIO
        from pypdf import PdfReader

        draft = self._post().json()["contract_draft"]
        contract_text = "\n".join(
            page.extract_text() or ""
            for page in PdfReader(BytesIO(base64.b64decode(draft["data_base64"]))).pages
        )
        assert "AUDIO CLEARANCE AGREEMENT" in contract_text
        assert "Blinding Lights" in contract_text


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
# _is_royalty_free() unit tests
# ---------------------------------------------------------------------------


class TestIsRoyaltyFree:
    def test_commercial_artist_returns_false(self):
        assert _is_royalty_free("Blinding Lights", "The Weeknd") is False

    def test_royalty_free_keyword_in_artist(self):
        assert _is_royalty_free("Happy Tune", "Royalty Free Music Co") is True

    def test_royalty_free_hyphenated_in_artist(self):
        assert _is_royalty_free("Chill Beat", "royalty-free sounds") is True

    def test_creative_commons_in_artist(self):
        assert _is_royalty_free("Sunrise", "Creative Commons Artist") is True

    def test_cc0_in_title(self):
        assert _is_royalty_free("CC0 Ambient Pad", "Unknown") is True

    def test_public_domain_in_title(self):
        assert _is_royalty_free("Public Domain Waltz", "Composer") is True

    def test_pixabay_in_artist(self):
        assert _is_royalty_free("Upbeat Track", "Pixabay Music") is True

    def test_incompetech_in_artist(self):
        assert _is_royalty_free("Galway", "Incompetech") is True

    def test_bensound_in_title(self):
        assert _is_royalty_free("Bensound Epic", "Various") is True

    def test_no_copyright_in_title(self):
        assert _is_royalty_free("No Copyright Music 2024", "VlogBeats") is True

    def test_rf_label_in_audd_result(self):
        audd_raw = {"result": {"title": "Track", "artist": "X", "label": "Epidemic Sound"}}
        assert _is_royalty_free("Track", "X", audd_raw) is True

    def test_jamendo_label(self):
        audd_raw = {"result": {"title": "Beat", "artist": "Y", "label": "Jamendo"}}
        assert _is_royalty_free("Beat", "Y", audd_raw) is True

    def test_low_confidence_score(self):
        audd_raw = {"result": {"title": "Ambiguous", "artist": "Z", "score": 30}}
        assert _is_royalty_free("Ambiguous", "Z", audd_raw) is True

    def test_high_confidence_score_commercial(self):
        audd_raw = {"result": {"title": "Blinding Lights", "artist": "The Weeknd", "score": 95}}
        assert _is_royalty_free("Blinding Lights", "The Weeknd", audd_raw) is False

    def test_boundary_score_49_is_free(self):
        audd_raw = {"result": {"score": 49}}
        assert _is_royalty_free("Track", "Artist", audd_raw) is True

    def test_boundary_score_50_is_commercial(self):
        audd_raw = {"result": {"score": 50}}
        assert _is_royalty_free("Track", "Artist", audd_raw) is False

    def test_case_insensitive_artist(self):
        assert _is_royalty_free("Song", "ROYALTY FREE BEATS") is True

    def test_case_insensitive_title(self):
        assert _is_royalty_free("CREATIVE COMMONS HIT", "Artist") is True

    def test_none_audd_raw_safe(self):
        assert _is_royalty_free("Song", "Artist", None) is False


# ---------------------------------------------------------------------------
# _build_audio_licenses() unit tests
# ---------------------------------------------------------------------------


class TestBuildAudioLicenses:
    def test_commercial_returns_two_licenses(self):
        assert len(_build_audio_licenses(False)) == 2

    def test_commercial_sync_fee(self):
        sync = next(l for l in _build_audio_licenses(False) if l["license_type"] == "Sync")
        assert sync["amount_usd"] == SYNC_LICENSE_BASE_FEE

    def test_commercial_master_fee(self):
        master = next(l for l in _build_audio_licenses(False) if l["license_type"] == "Master")
        assert master["amount_usd"] == MASTER_LICENSE_BASE_FEE

    def test_royalty_free_sync_fee_is_zero(self):
        sync = next(l for l in _build_audio_licenses(True) if l["license_type"] == "Sync")
        assert sync["amount_usd"] == 0.0

    def test_royalty_free_master_fee_is_zero(self):
        master = next(l for l in _build_audio_licenses(True) if l["license_type"] == "Master")
        assert master["amount_usd"] == 0.0

    def test_royalty_free_description_mentions_creative_commons(self):
        sync = next(l for l in _build_audio_licenses(True) if l["license_type"] == "Sync")
        assert "Royalty-Free" in sync["description"] or "Creative Commons" in sync["description"]


# ---------------------------------------------------------------------------
# /api/v1/clearance/audio — royalty_free field in response
# ---------------------------------------------------------------------------


class TestAudioClearanceRoyaltyFreeField:
    def test_commercial_track_royalty_free_is_false(self):
        with patch("main._query_audd", return_value=AUDD_SUCCESS):
            resp = client.post(
                "/api/v1/clearance/audio",
                json={"audio_url": "https://example.com/clip.mp3"},
            )
        assert resp.json()["royalty_free"] is False

    def test_commercial_track_total_fee_is_30000(self):
        with patch("main._query_audd", return_value=AUDD_SUCCESS):
            resp = client.post(
                "/api/v1/clearance/audio",
                json={"audio_url": "https://example.com/clip.mp3"},
            )
        assert resp.json()["total_fee_usd"] == 30_000.0

    def test_royalty_free_track_detected_via_artist(self):
        rf_audd = {
            "status": "success",
            "result": {
                "title": "Happy Beats",
                "artist": "Royalty Free Music",
                "apple_music": {},
            },
        }
        with patch("main._query_audd", return_value=rf_audd):
            resp = client.post(
                "/api/v1/clearance/audio",
                json={"audio_url": "https://example.com/rf.mp3"},
            )
        assert resp.json()["royalty_free"] is True

    def test_royalty_free_track_total_fee_is_zero(self):
        rf_audd = {
            "status": "success",
            "result": {
                "title": "Happy Beats",
                "artist": "Royalty Free Music",
                "apple_music": {},
            },
        }
        with patch("main._query_audd", return_value=rf_audd):
            resp = client.post(
                "/api/v1/clearance/audio",
                json={"audio_url": "https://example.com/rf.mp3"},
            )
        assert resp.json()["total_fee_usd"] == 0.0

    def test_royalty_free_sync_fee_is_zero(self):
        rf_audd = {
            "status": "success",
            "result": {"title": "Chill", "artist": "CC0 Sounds", "apple_music": {}},
        }
        with patch("main._query_audd", return_value=rf_audd):
            resp = client.post(
                "/api/v1/clearance/audio",
                json={"audio_url": "https://example.com/cc.mp3"},
            )
        sync = next(l for l in resp.json()["licenses"] if l["license_type"] == "Sync")
        assert sync["amount_usd"] == 0.0

    def test_low_confidence_score_gives_zero_fee(self):
        low_conf = {
            "status": "success",
            "result": {"title": "Ambient", "artist": "Unknown", "apple_music": {}, "score": 20},
        }
        with patch("main._query_audd", return_value=low_conf):
            resp = client.post(
                "/api/v1/clearance/audio",
                json={"audio_url": "https://example.com/lo.mp3"},
            )
        assert resp.json()["total_fee_usd"] == 0.0


# ---------------------------------------------------------------------------
# /api/v1/clearance/audio/direct — restored + royalty-free logic
# ---------------------------------------------------------------------------

DIRECT_PAYLOAD = {
    "song_title": "Bohemian Rhapsody",
    "artist": "Queen",
    "timestamp_start": "00:01:30",
    "timestamp_end": "00:03:45",
}

DIRECT_RF_PAYLOAD = {
    "song_title": "CC0 Ambient Drone",
    "artist": "Pixabay Music",
    "timestamp_start": "00:00:10",
    "timestamp_end": "00:01:00",
}


class TestDirectAudioClearance:
    def test_status_200(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_PAYLOAD)
        assert resp.status_code == 200

    def test_response_status_approved(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_PAYLOAD)
        assert resp.json()["status"] == "approved"

    def test_commercial_royalty_free_false(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_PAYLOAD)
        assert resp.json()["royalty_free"] is False

    def test_commercial_total_fee_30000(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_PAYLOAD)
        assert resp.json()["total_fee_usd"] == 30_000.0

    def test_match_title_echoes_song_title(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_PAYLOAD)
        assert resp.json()["match"]["title"] == "Bohemian Rhapsody"

    def test_match_artist_echoes_artist(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_PAYLOAD)
        assert resp.json()["match"]["artist"] == "Queen"

    def test_apple_music_link_is_none(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_PAYLOAD)
        assert resp.json()["match"]["apple_music_link"] is None

    def test_two_licenses_returned(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_PAYLOAD)
        assert len(resp.json()["licenses"]) == 2

    def test_sync_fee_15000_for_commercial(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_PAYLOAD)
        sync = next(l for l in resp.json()["licenses"] if l["license_type"] == "Sync")
        assert sync["amount_usd"] == 15_000.0

    def test_master_fee_15000_for_commercial(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_PAYLOAD)
        master = next(l for l in resp.json()["licenses"] if l["license_type"] == "Master")
        assert master["amount_usd"] == 15_000.0

    def test_currency_usd(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_PAYLOAD)
        assert resp.json()["currency"] == "USD"

    def test_timestamp_start_echoed(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_PAYLOAD)
        assert resp.json()["timestamp_start"] == "00:01:30"

    def test_timestamp_end_echoed(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_PAYLOAD)
        assert resp.json()["timestamp_end"] == "00:03:45"

    def test_requested_at_present(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_PAYLOAD)
        assert resp.json()["requested_at"]

    def test_royalty_free_artist_gives_zero_fee(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_RF_PAYLOAD)
        assert resp.json()["total_fee_usd"] == 0.0

    def test_royalty_free_artist_sets_flag(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_RF_PAYLOAD)
        assert resp.json()["royalty_free"] is True

    def test_royalty_free_description_in_licenses(self):
        resp = client.post("/api/v1/clearance/audio/direct", json=DIRECT_RF_PAYLOAD)
        sync = next(l for l in resp.json()["licenses"] if l["license_type"] == "Sync")
        assert "Royalty-Free" in sync["description"] or "Creative Commons" in sync["description"]

    def test_rejects_missing_song_title(self):
        p = {**DIRECT_PAYLOAD}; del p["song_title"]
        assert client.post("/api/v1/clearance/audio/direct", json=p).status_code == 422

    def test_rejects_missing_artist(self):
        p = {**DIRECT_PAYLOAD}; del p["artist"]
        assert client.post("/api/v1/clearance/audio/direct", json=p).status_code == 422

    def test_rejects_missing_timestamp_start(self):
        p = {**DIRECT_PAYLOAD}; del p["timestamp_start"]
        assert client.post("/api/v1/clearance/audio/direct", json=p).status_code == 422

    def test_rejects_missing_timestamp_end(self):
        p = {**DIRECT_PAYLOAD}; del p["timestamp_end"]
        assert client.post("/api/v1/clearance/audio/direct", json=p).status_code == 422

    def test_rejects_blank_song_title(self):
        assert client.post(
            "/api/v1/clearance/audio/direct", json={**DIRECT_PAYLOAD, "song_title": ""}
        ).status_code == 422

    def test_rejects_blank_artist(self):
        assert client.post(
            "/api/v1/clearance/audio/direct", json={**DIRECT_PAYLOAD, "artist": ""}
        ).status_code == 422
