# BA Account Swarm (`ba-account-swarm`)

> **Autonomous Multi-Agent Business Analyst & Account Management Swarm** with institutional-grade Human-in-the-Loop (HITL) governance, Supabase Postgres state synchronization, WhatsApp Cloud API, Browser Voice Dictation PWA, and zero-heavy-framework architecture (`fastapi`, `openai`, `httpx`, `pydantic` only).

[![Vercel Deployment](https://img.shields.io/badge/Deploy-Vercel-black?logo=vercel)](https://vercel.com)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![Supabase](https://img.shields.io/badge/Database-Supabase%20Postgres-3ECF8E?logo=supabase)](https://supabase.com)
[![Meta WhatsApp](https://img.shields.io/badge/WhatsApp-Cloud%20API-25D366?logo=whatsapp)](https://developers.facebook.com)

---

## 🏗️ Architecture Overview

The **BA Account Swarm** operates on a lightweight, native OpenAI Swarm multi-agent handoff loop. It contains **no heavy agent frameworks** (no LangChain, no LangGraph, no CrewAI, no `python-multipart`), executing in <15s on Vercel Serverless Functions with zero cold-start bloat.

```
                              [ Omnichannel Inbound Ingestion ]
                                              │
                    ┌─────────────────────────┴─────────────────────────┐
                    │  WhatsApp • Voice Audio • Gmail • Outlook         │
                    │  Meeting Transcripts • CI/CD Releases • Daily Cron │
                    └─────────────────────────┬─────────────────────────┘
                                              ▼
                                 ┌─────────────────────────┐
                                 │  1. Triage & Router     │
                                 │  (Context Enrichment)   │
                                 └────────────┬────────────┘
                                              │
            ┌─────────────────────────────────┼─────────────────────────────────┐
            │                                 │                                 │
            ▼                                 ▼                                 ▼
┌───────────────────────┐        ┌─────────────────────────┐       ┌─────────────────────────┐
│ 2. Commercial Agent   │        │ 3. Principal BA & Docs  │       │ 4. Technical Delivery   │
│ - Zoho 90/60/30 Renew │        │ - BRD / PRD / PDD       │       │ - Parse Releases/Logs   │
│ - SOW Proposals       │        │ - REQ-001 Traceability  │       │ - Jira 'In QA'->Deploy  │
│ - SLA Matrices (P1-P4)│        │ - MoSCoW & Gherkin      │       │ - Audit Comments        │
└───────────┬───────────┘        └────────────┬────────────┘       └────────────┬────────────┘
            │                                 │                                 │
            └─────────────────────────────────┼─────────────────────────────────┘
                                              ▼
                                 ┌─────────────────────────┐
                                 │ 5. Comms Orchestrator   │
                                 │ - MoM with Action Items │
                                 │ - Drafts Email/WhatsApp │
                                 └────────────┬────────────┘
                                              │
                                              ▼
                          ┌───────────────────────────────────────┐
                          │  🛡️ Supabase HITL Approval Gate       │
                          │  (Destructive / External / Contracts) │
                          └───────────────────┬───────────────────┘
                                              │
                             Human Reviewer Approves via UI
                                              │
                                              ▼
                            ┌───────────────────────────────────┐
                            │ External Execution Engine         │
                            │ - Update Zoho Deal Stage/Amount   │
                            │ - Send Client WhatsApp or Email   │
                            │ - Jira Production Transitions     │
                            └───────────────────────────────────┘
```

---

## 🤖 Specialized Swarm Agents & Guardrails

| Agent | Role & Domain | Primary Tools & Handoffs | High-Leverage Capabilities |
|---|---|---|---|
| **`triage`** | Ingests multi-channel events (WhatsApp, Voice, Email, Releases), loads Supabase/Zoho/Jira context, and classifies routing intent | `fetch_account_context`, `zoho_search_account`, `jira_search_issues`, `transfer_to_*` | Orchestrates complete multi-agent handoff chains (`triage` ➔ `commercial` ➔ `ba_docs` ➔ `comms`) |
| **`commercial`** | Monitors 90/60/30-day Zoho CRM renewal windows, analyzes discount concessions, drafts SOWs, and generates SLA matrices | `calculate_project_quote`, `zoho_get_deals`, `zoho_get_expiring_renewals`, `queue_for_approval` | **Rate-Card & Effort Estimator**: $120/$100/$85 rates, 30/40/30 milestone split, flags `⚠️ MARGIN ALERT` on discounts > 10% |
| **`ba_docs`** | Authors audit-ready **BRD**, **PRD** (with `REQ-XXX` IDs, MoSCoW, INVEST stories, Gherkin criteria), and **PDD** (Mermaid workflows) via parallel LLM generation | `analyze_scope_impact`, `upsert_raid_item`, `get_active_raid_items`, `generate_parallel_sections`, `jira_create_issue` | **Scope Creep & CR Detector**: Analyzes incoming requests against baseline traceability map; classifies Billable CRs with effort/cost delta & negative edge cases |
| **`delivery`** | Parses release notes/PR summaries, correlates Jira tickets (`PROJ-123`), validates acceptance criteria, and advances status | `upsert_raid_item`, `get_active_raid_items`, `jira_search_issues`, `jira_transition_issue`, `jira_add_comment` | **RAID Radar & WSR**: Tracks open project risks and generates 1-click Executive Weekly Status Reports |
| **`comms`** | Converts raw meeting transcripts/voice memos to formal Minutes of Meeting (Decisions, Action Items with Owners & Dates), drafts email/WhatsApp replies | `email_create_draft`, `whatsapp_send_message`, `calendar_create_event`, `queue_for_approval` | **Omnichannel Governance**: Stages client emails and WhatsApp updates into HITL queue for human sign-off |

### Strict Shared Guardrails
1. **Anti-Hallucination & Open Questions**: If pricing, discounts, SLAs, or delivery dates are missing or ambiguous, the agents never guess. Missing items are tagged as `[OPEN QUESTION — OWNER: <Role>]`.
2. **Discrepancy Alerts (`⚠️ DISCREPANCY ALERT`)**: Compares inbound claims against Zoho CRM deal terms and Jira scope. Any deviation raises an immediate discrepancy banner in logs and summaries.
3. **Margin Alerts (`⚠️ MARGIN ALERT`)**: Any commercial discount or concession > 10% triggers an immediate margin erosion alert requiring Commercial Director escalation.
4. **HITL Governance**: No external WhatsApp message or email is ever dispatched and no Zoho deal amount is modified without human sign-off via `/api/approvals`.

---

## 🚀 Step-by-Step Setup & Deployment

### ⚡ Vercel + Google Gemini Quickstart (Recommended Default)

The swarm runs by default on **Google Gemini 2.5 Pro** (reasoning & multi-agent orchestration) and **Gemini 2.5 Flash** (rapid concurrent PRD/BRD drafting & native audio transcription) via Gemini's OpenAI-compatible REST endpoint. Zero extra SDK dependencies required (`fastapi`, `openai`, `httpx`, `pydantic` only).

1. **Get a Free Gemini API Key**:
   - Visit [Google AI Studio](https://aistudio.google.com/) and click **Get API key**.
   - Create and copy your key (format: `AIzaSy...`).
2. **Configure in Vercel**:
   - In your Vercel Project → **Settings → Environment Variables**, add:
     - `GEMINI_API_KEY`: `AIzaSy...` (your Google AI Studio API key)
   - *(Optional customizations)*:
     - `LLM_MODEL`: `gemini-2.5-pro` (default)
     - `FAST_LLM_MODEL`: `gemini-2.5-flash` (default)
     - `LLM_BASE_URL`: `https://generativelanguage.googleapis.com/v1beta/openai/` (default)
3. **Deploy & Validate**:
   - Push to `main` or click **Redeploy** in the Vercel dashboard.
   - The Swarm Command Chat console will immediately activate. If `GEMINI_API_KEY` is ever omitted, the UI gracefully warns you with clear configuration instructions instead of throwing 401 exceptions.

---

### Step 1: Database Migration (Supabase Postgres)
1. Go to your [Supabase Dashboard](https://database.new) and select your project.
2. Open the **SQL Editor** in the left sidebar.
3. Paste and run the complete migration script located in [`supabase/schema.sql`](file:///g:/My%20Drive/Varun's%20git/VBS%20BA%20AGENT/supabase/schema.sql):
   - Creates `account_context` with requirement traceability mapping and WhatsApp contact links.
   - Creates `hitl_approvals` with approval status state machine (`pending`, `approved`, `rejected`, `executed`).
   - Creates `swarm_audit_logs` with JSONB handoff telemetry and discrepancy tracking.
   - Configures Row Level Security (RLS) policies for the `service_role`.
4. Copy your **Project URL** and **`service_role` Secret Key** from **Project Settings → API**.

---

### Step 2: Third-Party Integrations & Token Setup

#### 1. Meta WhatsApp Cloud API
1. Go to [Meta for Developers](https://developers.facebook.com/) and create a Business App with **WhatsApp** product enabled.
2. In the WhatsApp API Setup tab, copy your **Phone Number ID** (`WHATSAPP_PHONE_NUMBER_ID`) and **System User Access Token** (`WHATSAPP_TOKEN`).
3. Set your custom verification string as `WHATSAPP_VERIFY_TOKEN` (e.g. `my_secret_token_123`).
4. Under **Configuration → Webhooks**:
   - Callback URL: `https://<your-vercel-domain>.vercel.app/api/webhook/whatsapp`
   - Verify Token: Matches `WHATSAPP_VERIFY_TOKEN`
   - Subscribed Fields: `messages`

#### 2. Zoho CRM (OAuth 2.0 with Refresh Token)
1. Open the [Zoho Developer Console](https://api-console.zoho.com/).
2. Create a **Self-Client** application with scopes: `ZohoCRM.modules.ALL,ZohoCRM.settings.ALL`.
3. Exchange the authorization code for a long-lived **Refresh Token**:
   ```bash
   curl -X POST https://accounts.zoho.com/oauth/v2/token \
     -d "grant_type=authorization_code" \
     -d "client_id=YOUR_CLIENT_ID" \
     -d "client_secret=YOUR_CLIENT_SECRET" \
     -d "code=YOUR_GENERATED_CODE"
   ```
4. Store `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, and `ZOHO_REFRESH_TOKEN`.

#### 3. Atlassian Jira REST API v3
1. Log in to [Atlassian Account API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens).
2. Generate an API token and set `JIRA_BASE_URL` (e.g. `https://yourdomain.atlassian.net`), `JIRA_EMAIL`, and `JIRA_API_TOKEN`.

#### 4. Google Workspace & Microsoft 365
- **Google Gmail & Calendar**: Set `GMAIL_ACCESS_TOKEN` with scopes `gmail.compose`, `gmail.send`, `calendar.events`.
- **Microsoft Graph**: Set `MS_GRAPH_ACCESS_TOKEN` with `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`.

---

### Step 3: Deploy to Vercel from GitHub

1. Push this repository to GitHub:
   ```bash
   git add .
   git commit -m "feat: initial ba-account-swarm release"
   git push origin main
   ```
2. In the [Vercel Dashboard](https://vercel.com/new), import your repository.
3. Under **Environment Variables**, configure the keys specified in [`.env.example`](file:///g:/My%20Drive/Varun's%20git/VBS%20BA%20AGENT/.env.example):
   - `SWARM_API_KEY`: Secret string for webhook and API auth.
   - `CRON_SECRET`: Random secret string for Vercel Cron.
   - `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `FAST_LLM_MODEL`.
   - `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.
   - `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`.
   - Zoho, Jira, and Email credentials.
4. Click **Deploy**. Vercel will automatically configure serverless function routing and register the daily cron job at `0 3 * * *`.

---

### Step 4: Omnichannel Triggers & Ingestion

#### A. Direct Browser Voice Dictation (PWA)
1. Open your hosted dashboard URL on your phone or desktop.
2. On iOS Safari or Android Chrome, tap **Share &rarr; Add to Home Screen** to install as a standalone PWA.
3. Tap **🎙️ Voice Memo** to record audio directly from your microphone. Audio is encoded in base64, transcribed natively using Google Gemini (gemini-2.5-flash) without needing OpenAI Whisper or `python-multipart`, and automatically triaged by the Swarm!

#### B. WhatsApp Inbound Messages
Clients or account executives can send messages directly to your WhatsApp Business number. The Meta webhook dispatches them to `/api/webhook/whatsapp`, initiating triage and context lookups automatically.

#### C. Gmail Automation (Google Apps Script)
```javascript
function onClientEmailReceived() {
  const threads = GmailApp.search('label:client-account is:unread');
  const swarmUrl = 'https://your-app.vercel.app/api/webhook';
  const apiKey = 'YOUR_SWARM_API_KEY';

  threads.forEach(thread => {
    const messages = thread.getMessages();
    const lastMsg = messages[messages.length - 1];

    UrlFetchApp.fetch(swarmUrl, {
      method: 'post',
      contentType: 'application/json',
      headers: { 'x-api-key': apiKey },
      payload: JSON.stringify({
        source: 'gmail',
        subject: lastMsg.getSubject(),
        body: lastMsg.getPlainBody(),
        metadata: { from: lastMsg.getFrom(), threadId: thread.getId() }
      })
    });
    thread.markRead();
  });
}
```

#### D. GitHub Actions (Release Webhook)
Add to `.github/workflows/deploy.yml`:
```yaml
- name: Notify BA Swarm of Deployment
  run: |
    curl -X POST https://your-app.vercel.app/api/webhook \
      -H "Content-Type: application/json" \
      -H "x-api-key: ${{ secrets.SWARM_API_KEY }}" \
      -d '{
        "source": "deployment_note",
        "subject": "Production Release: ${{ github.ref_name }}",
        "body": "Deployed commit ${{ github.sha }}. Closed PRs: ${{ github.event.head_commit.message }}",
        "metadata": { "repo": "${{ github.repository }}", "run_id": "${{ github.run_id }}" }
      }'
```

---

## 💻 Local Development & Testing

```bash
# 1. Run local development server
python run.py

# 2. Run test suite
pytest tests/ -v
```

Visit `http://localhost:8000/` in your browser to access the live Mission Control PWA Console!
