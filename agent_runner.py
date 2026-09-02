"""
Cinefiles — Agent Engine Runner
docs/PLAN.md · Phase 3, Step 8

Satisfies the Devpost IBM Track runtime SDK requirement:
  "Code repository showing actual runtime imports/calls to
   google-cloud-aiplatform[agent_engines,adk]"

Usage:
    pip install "google-cloud-aiplatform[agent_engines,adk]>=1.101.0"

    # Set required environment variables (or use a .env file):
    export GOOGLE_CLOUD_PROJECT="your-gcp-project-id"
    export GOOGLE_CLOUD_LOCATION="us-east1"
    export AGENT_ENGINE_ID="your-agent-engine-resource-id"
    # e.g. projects/123456/locations/us-east1/reasoningEngines/abc123

    python agent_runner.py
    python agent_runner.py --audio-url "https://audd.tech/example.mp3"
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# ── Google Cloud AI Platform SDK (runtime SDK requirement) ──────────────────
import vertexai
from vertexai import agent_engines                                # ADK engine client
from vertexai.preview import reasoning_engines                    # ReasoningEngine API

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_AUDIO_URL = "https://audd.tech/example.mp3"

CLEARANCE_PROMPT_TEMPLATE = (
    "You are Cinefiles. I need a copyright clearance estimate for the following "
    "audio clip. Please call the request_audio_clearance tool with this audio URL "
    "and return the full licensing breakdown.\n\nAudio URL: {audio_url}"
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _require_env(name: str) -> str:
    """Read a required environment variable or exit with a clear message."""
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"[ERROR] Environment variable '{name}' is not set.", file=sys.stderr)
        print(
            f"        Set it with:  export {name}=<value>  "
            f"(or add it to a .env file and load with python-dotenv)",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _print_response(response: object) -> None:
    """Pretty-print whatever the agent engine returns."""
    if hasattr(response, "text"):
        print("\n── Agent Response ─────────────────────────────────────────")
        print(response.text)
    elif isinstance(response, dict):
        print("\n── Agent Response (JSON) ───────────────────────────────────")
        print(json.dumps(response, indent=2))
    else:
        print("\n── Agent Response ─────────────────────────────────────────")
        print(response)


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Cinefiles Agent Engine programmatically."
    )
    parser.add_argument(
        "--audio-url",
        default=DEFAULT_AUDIO_URL,
        help="Publicly accessible URL of the audio clip to clear (default: AudD test clip).",
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        help="GCP project ID (overrides GOOGLE_CLOUD_PROJECT env var).",
    )
    parser.add_argument(
        "--location",
        default=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-east1"),
        help="GCP region (default: us-east1).",
    )
    parser.add_argument(
        "--agent-engine-id",
        default=os.environ.get("AGENT_ENGINE_ID"),
        help="Agent Engine resource ID (overrides AGENT_ENGINE_ID env var).",
    )
    args = parser.parse_args()

    project = args.project or _require_env("GOOGLE_CLOUD_PROJECT")
    location = args.location
    agent_engine_id = args.agent_engine_id or _require_env("AGENT_ENGINE_ID")

    # ── Initialise Vertex AI SDK ─────────────────────────────────────────────
    print(f"[INFO] Initialising Vertex AI  project={project}  location={location}")
    vertexai.init(project=project, location=location)

    # ── Instantiate the Agent Engine client ──────────────────────────────────
    print(f"[INFO] Connecting to Agent Engine: {agent_engine_id}")
    agent = reasoning_engines.ReasoningEngine(agent_engine_id)

    # ── Build the clearance prompt ────────────────────────────────────────────
    prompt = CLEARANCE_PROMPT_TEMPLATE.format(audio_url=args.audio_url)
    print(f"[INFO] Sending query for audio URL: {args.audio_url}")

    # ── Query the agent ───────────────────────────────────────────────────────
    response = agent.query(input=prompt)

    _print_response(response)


if __name__ == "__main__":
    main()
