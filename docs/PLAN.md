# Cinefiles — 14-Day Hackathon Execution Plan

An enterprise media perception agent built with Gemini Enterprise and IBM Bob to automate audio copyright clearance for indie filmmakers.

## 🛠 Hackathon Requirements Checklist

- [x] **IBM Partner Track:** Backend logic generated via IBM Bob IDE.
- [x] **Google AI Infrastructure:** Built using Gemini Multimodal, Google Cloud Agent Builder, and Google Cloud Run.
- [x] **Runtime SDK Import:** Explicit calls to `google-cloud-aiplatform[agent_engines,adk]` in code.
- [x] **Repository:** Public GitHub repository with root MIT `LICENSE` file.
- [x] **Video:** Public YouTube/Vimeo demo under 3 minutes.

## 🗓 Step-by-Step Execution Schedule

### Phase 1: IBM Bob Legal Microservice (Days 1–3)

- **Step 1: Backend Generation (IBM Bob IDE)**

  Prompt IBM Bob in the standalone Bob IDE:

  > *"Bob, write a FastAPI application in `backend/main.py` accepting `POST /api/v1/clearance/audio` with a JSON payload containing `audio_url`. Query the AudD API (`https://api.audd.io/`) using `requests` and `AUDD_API_TOKEN` from environment variables. Extract track title, artist, and Apple Music link. Calculate Sync ($15,000) and Master ($15,000) fees, return structured JSON, and export `backend/openapi.json`."*

- **Step 2: Local Endpoint Verification**

  Test locally using Uvicorn and AudD's test clip (`https://audd.tech/example.mp3`):

  ```bash
  uvicorn backend.main:app --reload --env-file .env
  ```

- **Step 3: Commit & Push**

  Commit generated files directly from the IBM Bob IDE to `https://github.com/alissatroiano/cinefiles` to establish provenance.

### Phase 2: Serverless Deployment & Security (Days 4–5)

- **Step 4: Google Cloud Run Deployment**

  Deploy the microservice serverlessly per Google Cloud guidelines:

  ```bash
  gcloud run deploy cinefiles-backend --source ./backend --port 8080 --allow-unauthenticated
  ```

- **Step 5: Secret Management**

  Store the `AUDD_API_TOKEN` inside Google Cloud Secret Manager and attach it to the Cloud Run service instance.

### Phase 3: Agent Builder & ADK Orchestration (Days 6–9)

- **Step 6: OpenAPI Webhook Tool Registration**

  Import `backend/openapi.json` into Google Cloud Agent Builder under **Tools → Create Tool (OpenAPI Webhook)** and set the target URL to the Cloud Run endpoint.

- **Step 7: Playbook System Instructions**

  Configure the Agent Builder System Instruction:

  > *"You are Cinefiles, an expert film legal and music clearance agent. When reviewing media timelines or user prompts, identify background commercial audio, obtain the audio clip URL, and execute the `request_audio_clearance` tool using `audio_url` to calculate synchronization and master use licensing fees."*

- **Step 8: Native ADK Integration Script**

  Install required SDKs:

  ```bash
  pip install "google-cloud-aiplatform[agent_engines,adk]>=1.101.0"
  ```

  Write `agent_runner.py` to instantiate the Agent Engine client and run programmatically, satisfying the runtime SDK requirement.

### Phase 4: UI, Demo Video & Submission (Days 10–14)

- **Step 9: Agent Web Chat Deployment**

  Publish the agent as a web chat interface via Agent Builder or host the endpoint.

- **Step 10: Video Recording (Max 3 Minutes)**

  Record a walkthrough demonstrating:

  1. Multimodal detection of commercial audio in a film clip.
  2. Gemini invoking the IBM Bob Cloud Run webhook via `audio_url`.
  3. Real-time acoustic fingerprinting via AudD and instant breakdown of Sync/Master license costs ($30,000 USD).

#### Video Submission

The Dual-Payment Hurdle: Independent filmmakers must independently negotiate and pay two separate sets of rights holders for every piece of copyrighted music used—paying both a synchronization license to the songwriter/publisher and a master use license to the recording artist/label, effectively doubling the music cost per track.

The Micro-Budget Squeeze: For low-budget independent films with budgets ranging from $50,000 to $500,000, licensing fees for a single established track typically range from $500 to $5,000 per side, which can quickly consume a massive chunk of a restricted production budget.

The Macro Imbalance: The global music copyright market reached $45.5 billion, making the music copyright industry roughly 38% larger than the entire global movie box office industry ($33.2 billion), underscoring how heavily protected and economically walled-off audio assets are relative to indie film revenues.

The Chain of Title Bottleneck: A failure to secure proper clearance for even a few seconds of background audio can result in failed E&O (Errors and Omissions) insurance underwriting, disqualification from major film festivals, or catastrophic post-distribution copyright infringement lawsuits.

- **Step 11: Final Devpost Submission**

  Submit to the IBM Track before the deadline with the GitHub link, description, and public video URL.
