## Cinefiles

```
  [Indie Filmmaker UI]
          │
          ▼
┌───────────────────────────────────┐
│     Vertex AI Agent Builder       │  <-- Built in minutes via Playbooks
│  (Gemini 3.7 Vision & Perception) │  <-- Scans video timeline
└─────────────────┬─────────────────┘
                  │ Triggers Open Webhook Tool
                  ▼
┌───────────────────────────────────┐
│              IBM Bob              │  <-- Formulates, validates, & hosts 
│  (Action Engine & MCP Protocol)   │  <-- the heavy Enterprise Legal Tasks
└───────────────────────────────────┘
```

### How To Build It

#### Step 1: Initialize the Vertex AI Agent Playbook

***Instead of programming loops, you write a Playbook—a plain-English set of structural instructions.***

1. Go to the Google Cloud Console ➔ Vertex AI Agent Builder.
2. Create a new Playbook-based Agent.
3. Define the Agent's goal in the instructions

#### Step 2: Use IBM Bob to Build the "Clearance" Backend Tools

***IBM Bob excels at spinning up valid, compliant microservices and Model Context Protocol (MCP) tools without manual boilerplate coding.***

1. Open your development workspace and prompt the IBM Bob coding assistant: ***"Bob, build me a FastAPI python endpoint that accepts an asset payload (asset_name, timestamp). It needs to search an external mock copyright database, match a template legal contract, and prepare a clearance PDF form. Ensure it has a human-approval step before completion."***
2. Watch as IBM Bob automatically generates the code, writes unit tests, and structures  server logic cleanly using best practices.

#### Step 3: Connect Agent Builder to Bob via Webhooks

***To make your low-code agent trigger Bob's legal engine, you hook them together using an OpenAPI tool schema.***

1. In Vertex AI Agent Builder, click Tools ➔ Create Tool.
2. Set the tool type to OpenAPI / Webhook.
3. Paste the API schema that IBM Bob generated for your clearance endpoint.
4. In your Agent Builder Playbook instructions, add a deterministic directive:
***"Whenever a user uploads a video file, call the VerifyTrademarks tool immediately with the extracted timestamps."***

#### Step 4: Rapidly Deploy the FrontendYou

In the event I do not have time to code a custom web frontend from scratch in 15 days...

1. Inside Agent Builder, navigate to the Deployment tab.
2. Use the built-in Web Chat Interface widget
3. This generates a clean, functional web page UI with an embeddable script tag that you can present straight to the judges.

#### Step 5: Secure and Audit the System

***To nail the "Technological Implementation" judging criteria, wrap deployment in standard enterprise safety layers:***

- Secrets: Ensure the API keys that Bob uses to hit registries are safely drawn from the Google Cloud Secret Manager.
- Safety: Toggle the Gemini Safety Settings to maximum inside Agent Builder to block toxic or illicit creative outputs.
