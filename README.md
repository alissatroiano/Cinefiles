## Cinefiles

```txt
[Indie Filmmaker UI]
        │
        ▼
┌───────────────────────────────────┐
│         Gemini Enterprise         │ <-- Enterprise-grade Perception
│ (Gemini 3.7 Vision & Perception)  │ <-- Scans video timeline
└─────────────────┬─────────────────┘
                  │ Triggers Open Webhook Tool
                  ▼
┌───────────────────────────────────┐
│             IBM Bob               │ <-- Formulates, validates, & hosts
│   (Action Engine & MCP Protocol)  │ <-- the heavy Enterprise Legal Tasks
└───────────────────────────────────┘
```
### Application Flow

```txt
[ Filmmaker ] 
     │ Uploads video/audio clip
     ▼
[ Gemini Enterprise Agent ] 
     │ Detects audio & calls IBM Bob Webhook with clip URL/file
     ▼
[ IBM Bob Backend (Cloud Run) ]
     │ 1. Sends audio snippet to AudD API (fingerprint match)
     │ 2. Receives song title, artist, and ISRC code
     │ 3. Calculates Sync & Master Use clearance fees ($30,000)
     │ 4. Returns complete legal package JSON to Gemini
     ▼
[ Gemini Enterprise Agent ]
     │ Formats and presents the final Clearance Report to the user
```

### Local Setup

#### Phase 1 — IBM Bob Backend (Complete ✅)

```bash
git clone https://github.com/alissatroiano/cinefiles.git
cd cinefiles/backend
pip install -r requirements.txt

# Set your AudD API token (https://audd.io)
export AUDD_API_TOKEN="your_token_here"   # PowerShell: $env:AUDD_API_TOKEN="…"

uvicorn main:app --reload --port 8080
# Interactive docs: http://localhost:8080/docs
```

---

#### Phase 2 — Gemini Enterprise Agent Configuration

**Step 4 — Open Google Cloud Console → Agent Builder**

1. Navigate to [console.cloud.google.com](https://console.cloud.google.com) and select (or create) your GCP project.
2. In the left sidebar, go to **Agent Builder** (search "Agent Builder" if not pinned).
3. Click **Create App** → choose **Conversational Agent** → select **Build your own**.

---

**Step 5 — Create the Playbook-based Agent**

1. Inside your new agent, go to the **Playbooks** tab and click **Create**.
2. Name the playbook `Cinefiles Audio Clearance`.
3. Paste the following text verbatim into the **Goal / Instructions** field:

   ```
   You are Cinefiles. Scan uploaded audio timelines for commercial music tracks, ambient radio, or live covers. Extract exact timestamps, song names, and artists. Call IBM_Bob_Audio_Clearance_Tool with this metadata.
   ```

   > The full playbook YAML (including multi-step flow) is in [`playbooks/cinefiles_agent.yaml`](playbooks/cinefiles_agent.yaml).

---

**Step 6 — Register the OpenAPI Webhook Tool**

1. In Agent Builder, go to **Tools → Create Tool**.
2. Set **Tool type** to **OpenAPI**.
3. Name the tool exactly: `IBM_Bob_Audio_Clearance_Tool`
4. Paste the contents of [`backend/openapi.json`](backend/openapi.json) into the schema field.
5. Set the **Service URL** to your deployed backend (Cloud Run URL, or `http://localhost:8080` for local testing).
6. Click **Save**.

   > The full tool descriptor is in [`playbooks/IBM_Bob_Audio_Clearance_Tool.yaml`](playbooks/IBM_Bob_Audio_Clearance_Tool.yaml).

---

#### Phase 3 — Runtime SDK Wire-up & Security *(Days 8–10)*

Store your AudD API token in **Google Cloud Secret Manager**:

```bash
gcloud secrets create AUDD_API_TOKEN --replication-policy="automatic"
echo -n "your_token_here" | gcloud secrets versions add AUDD_API_TOKEN --data-file=-
```

Enable **maximum Gemini Safety Settings** inside Agent Builder:
- Go to **Agent settings → Safety settings**
- Set all harm-category filters to **BLOCK_LOW_AND_ABOVE**

---

#### Phase 4 — Web UI & Submission *(Days 11–14)*

1. In Agent Builder → **Deployment tab** → copy the **Web Chat widget** script tag.
2. Embed it in a static HTML page or Replit project for the demo.
3. Record a < 3-minute video showing: upload → detection → clearance report.

---

### Agent Playbook — System Instruction

```
You are Cinefiles. Scan uploaded audio timelines for commercial music
tracks, ambient radio, or live covers. Extract exact timestamps, song
names, and artists. Call IBM_Bob_Audio_Clearance_Tool with this metadata.
```

### Agent Steps

1. Greet the filmmaker and prompt them to upload a video or audio clip.
2. Analyse the audio timeline using multimodal perception — identify every commercial track, ambient radio broadcast, or live cover.
3. For each detected segment, extract `timestamp_start`, `timestamp_end`, `song_title`, and `artist`.
4. Call `IBM_Bob_Audio_Clearance_Tool` with the clip URL and timestamps.
5. Present a clearance report table: track, artist, timestamp range, Sync fee, Master fee, and total cost.
6. Advise the filmmaker to consult legal counsel before commercial distribution.
