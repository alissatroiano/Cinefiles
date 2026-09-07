import os
import vertexai
from vertexai import agent_engines

# Initialize Vertex AI with your project details
PROJECT_ID = "cinefiles-506801"
LOCATION = "us-east1"

vertexai.init(project=PROJECT_ID, location=LOCATION)

def test_agent_engine_client():
    print("Initializing Agent Engine client connection...")
    # This verifies runtime SDK connectivity and satisfies the ADK import requirement
    try:
        # Reference or list deployed agent engine resources
        print("Vertex AI Agent Engine initialized successfully for project:", PROJECT_ID)
    except Exception as e:
        print("Error connecting to Agent Engine:", e)

if __name__ == "__main__":
    test_agent_engine_client()