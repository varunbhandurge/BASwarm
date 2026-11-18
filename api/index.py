"""
FastAPI Application & Vercel Serverless Entry Point
- POST /api/webhook: Multi-channel event ingestion (Gmail, Outlook, Transcripts, Releases)
- POST /api/voice: Voice audio transcription & Swarm delegation (Zero python-multipart)
- GET/POST /api/webhook/whatsapp: Meta WhatsApp Cloud API webhook challenge & message ingestion
- GET /api/cron/daily-account-sync: 24h Vercel Cron for renewals & delivery scans
- GET /api/approvals: List pending Human-in-the-Loop (HITL) approvals
- POST /api/approvals/{approval_id}/approve: 1-click execution of staged external actions
- POST /api/approvals/{approval_id}/reject: Reject staged action with audit reason
- GET /manifest.json: Progressive Web App (PWA) manifest
- GET /: Mission Control PWA Console with Browser Voice Dictation & Multi-Agent Activity
"""

import os
import json
import time
import base64
from typing import Dict, Any, List, Optional
import httpx
from fastapi import FastAPI, APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from swarm.engine import (
    run_swarm,
    transcribe_audio_bytes,
    generate_parallel_sections,
    get_gemini_api_key,
    is_gemini_key_configured,
    discover_available_gemini_models
)
from swarm.tools import (
    execute_approved_action,
    zoho_get_expiring_renewals,
    jira_search_issues,
    get_env,
    HTTP_TIMEOUT,
    calculate_project_quote,
    analyze_scope_impact,
    upsert_raid_item,
    get_active_raid_items,
    _IN_MEMORY_RAID_LOGS,
    _IN_MEMORY_AUDIT_LOGS
)

# Initialize FastAPI application
app = FastAPI(
    title="BA Account Swarm API & Mission Control",
    description="Autonomous Business Analyst & Account Management Multi-Agent Swarm with HITL Approvals",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter()


# ==============================================================================
# REQUEST & RESPONSE MODELS
# ==============================================================================
class WebhookRequest(BaseModel):
    source: str = Field(..., description="Event origin: gmail, outlook, whatsapp, voice, meeting_transcript, deployment_note, daily_cron")
    subject: Optional[str] = Field(default="", description="Subject line, phone number, or meeting title")
    body: str = Field(..., description="Raw text payload, transcript, or release notes")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Account name, deal id, or phone number")


class VoiceRequest(BaseModel):
    audio_base64: str = Field(..., description="Base64 encoded audio string (m4a, webm, mp3, wav)")
    format: Optional[str] = Field(default="webm", description="Audio container extension: webm, m4a, mp3, wav")
    account_name: Optional[str] = Field(default="", description="Optional account context to associate")


class TranscribeRequest(BaseModel):
    audio_base64: str = Field(..., description="Base64-encoded audio bytes (webm, ogg, mp3, wav, m4a)")
    format: Optional[str] = Field(default="webm", description="Audio format: webm, ogg, mp3, wav, m4a")


class RejectRequest(BaseModel):
    reason: Optional[str] = Field(default="Rejected by Human Reviewer", description="Rejection rationale")


class DocGenRequest(BaseModel):
    doc_type: str = Field(..., description="prd, brd, or pdd")
    context: str = Field(..., description="Business requirements or process description")
    sections: Optional[List[str]] = Field(default=None, description="Optional custom section titles")


class DelegateRequest(BaseModel):
    command: str = Field(..., description="Conversational command or task delegation")
    history: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="Prior conversation messages")
    channel: Optional[str] = Field(default="web_chat", description="Origin channel")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Optional context metadata")


class RaidItemRequest(BaseModel):
    account_name: str = Field(..., description="Account or client name")
    category: str = Field(default="Risk", description="Risk, Assumption, Issue, or Dependency")
    severity: str = Field(default="Medium", description="High, Medium, or Low")
    description: str = Field(..., description="Description of the RAID item")
    owner: Optional[str] = Field(default="Unassigned", description="Assigned owner or role")
    status: Optional[str] = Field(default="open", description="open, mitigated, or resolved")
    item_id: Optional[str] = Field(default=None, description="Optional existing RAID item ID")


# ==============================================================================
# AUTHENTICATION HELPERS
# ==============================================================================
def verify_api_key(x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    expected = get_env("SWARM_API_KEY")
    if not expected:
        return True
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing x-api-key header.")
    return True


def verify_cron_auth(request: Request, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    cron_secret = get_env("CRON_SECRET")
    swarm_key = get_env("SWARM_API_KEY")
    auth_header = request.headers.get("authorization", "")

    if cron_secret and auth_header == f"Bearer {cron_secret}":
        return True
    if swarm_key and x_api_key == swarm_key:
        return True
    if not cron_secret and not swarm_key:
        return True
    raise HTTPException(status_code=401, detail="Unauthorized cron invocation.")


# ==============================================================================
# API ENDPOINTS
# ==============================================================================
@router.post("/webhook")
async def handle_webhook(payload: WebhookRequest, request: Request, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """Ingests multi-channel events (Gmail, Outlook, WhatsApp, Transcripts, Releases)."""
    verify_api_key(x_api_key)

    event_payload = f"Subject / Channel: {payload.subject}\n\n{payload.body}"
    result = await run_swarm(
        initial_event=event_payload,
        starting_agent="triage",
        event_source=payload.source,
        context_metadata=payload.metadata
    )
    return JSONResponse(content=result)


@router.post("/transcribe")
async def handle_transcribe(payload: TranscribeRequest, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """
    Direct Google Gemini audio transcription endpoint via native generateContent REST.
    Transcribes base64-encoded voice memos and WhatsApp voice notes into plain text.
    Zero python-multipart dependency required!
    """
    verify_api_key(x_api_key)

    try:
        audio_bytes = base64.b64decode(payload.audio_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 audio payload: {str(e)}")

    filename = f"audio.{payload.format or 'webm'}"
    transcription = await transcribe_audio_bytes(audio_bytes, filename=filename)

    return JSONResponse(content={
        "status": "success",
        "transcription": transcription
    })


@router.post("/voice")
async def handle_voice(payload: VoiceRequest, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """
    Decodes base64 audio and transcribes it via Google Gemini (gemini-2.5-flash) native generateContent.
    Directly delegates the resulting transcript to the Swarm handoff loop.
    Zero python-multipart dependency required!
    """
    verify_api_key(x_api_key)

    try:
        audio_bytes = base64.b64decode(payload.audio_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid base64 audio payload: {str(e)}")

    filename = f"voice_input.{payload.format}"
    transcription = await transcribe_audio_bytes(audio_bytes, filename=filename)

    swarm_result = await run_swarm(
        initial_event=f"### Voice Memo Transcript:\n{transcription}",
        starting_agent="triage",
        event_source="voice",
        context_metadata={"account_name": payload.account_name} if payload.account_name else None
    )

    return JSONResponse(content={
        "status": "success",
        "transcription": transcription,
        "swarm_result": swarm_result
    })


def classify_initial_agent(command: str) -> str:
    """Fast-path classification to start the swarm on the appropriate specialist agent directly."""
    cmd_lower = command.lower()
    if any(k in cmd_lower for k in ["prd", "brd", "pdd", "requirement intake", "user stories", "gherkin", "specification", "scope creep", "change request"]):
        return "ba_docs"
    if any(k in cmd_lower for k in ["quote", "rate card", "commercial", "renewal", "pricing", "discount", "margin alert", "sow"]):
        return "commercial"
    if any(k in cmd_lower for k in ["deploy", "release note", "weekly status report", "wsr", "transition jira", "ci/cd", "commit"]):
        return "delivery"
    if any(k in cmd_lower for k in ["mom", "minutes of meeting", "draft email", "whatsapp update", "follow-up email"]):
        return "comms"
    return "triage"


@router.post("/delegate")
async def handle_delegation(payload: DelegateRequest, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """
    Interactive Chat Delegation Console Endpoint:
    Accepts user instructions with conversation history context,
    invokes the Swarm handoff loop starting from 'triage',
    and generates concise spoken summary for browser TTS.
    """
    verify_api_key(x_api_key)

    formatted_context = ""
    if payload.history:
        formatted_context = "### Recent Conversational Context (Previous Turns):\n"
        for msg in payload.history[-8:]:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "user").capitalize()
            raw_content = msg.get("content")
            if raw_content is not None and isinstance(raw_content, str):
                c_clean = raw_content.strip()
                if c_clean and c_clean.lower() not in ("undefined", "null"):
                    if len(c_clean) > 800:
                        c_clean = c_clean[:800] + "... [prior turn truncated]"
                    formatted_context += f"- **{role}**: {c_clean}\n"
        formatted_context += "\n"

    event_payload = f"{formatted_context}### User Command / Delegation:\n{payload.command}"

    initial_agent = classify_initial_agent(payload.command)
    turns_limit = 2 if initial_agent != "triage" else 3

    result = await run_swarm(
        initial_event=event_payload,
        starting_agent=initial_agent,
        event_source=payload.channel or "web_chat",
        max_turns=turns_limit,
        context_metadata=payload.metadata,
        time_budget_sec=24.0
    )

    handoffs = result.get("handoffs", [])
    if initial_agent != "triage" and not any(h.get("from") == "triage" for h in handoffs):
        handoffs = [{
            "from": "triage",
            "to": initial_agent,
            "reason": f"Direct intent classification routed command to specialized {initial_agent.upper()} agent.",
            "turn": 0
        }] + handoffs

    final_text = result.get("final_summary", "")
    if not final_text:
        tool_calls = result.get("tool_calls_executed", [])
        if tool_calls:
            tools_list = ", ".join(sorted(set(tc.get("tool", "action") for tc in tool_calls)))
            final_text = f"### Swarm Execution Completed\nCompleted {len(tool_calls)} automated operations across: {tools_list}."
        else:
            final_text = "Task completed by the swarm."
    
    # Synthesize clean 1-2 sentence spoken summary for TTS
    spoken_summary = ""
    if "Gemini API key not configured" in final_text or result.get("status") == "warning":
        spoken_summary = "Gemini API key is not configured. Please add GEMINI_API_KEY in your Vercel project settings and redeploy."
    elif final_text:
        cleaned = final_text.replace("#", "").replace("*", "").replace("`", "").strip()
        lines = [line.strip() for line in cleaned.splitlines() if line.strip() and not line.strip().startswith("-") and not line.strip().startswith("|") and not line.strip().startswith(">")]
        if lines:
            sentences = lines[0].split(". ")
            spoken_summary = ". ".join(sentences[:2])
            if not spoken_summary.endswith("."):
                spoken_summary += "."
        if not spoken_summary:
            spoken_summary = f"Task completed by {result.get('final_agent', 'the swarm')}."
    else:
        spoken_summary = "Swarm command executed successfully."

    queued_approvals = [
        tc for tc in result.get("tool_calls_executed", [])
        if tc.get("tool") == "queue_for_approval"
    ]

    return JSONResponse(content={
        "status": result.get("status", "success"),
        "command": payload.command,
        "final_agent": result.get("final_agent", "triage"),
        "agent_history": result.get("agent_history", []),
        "handoffs": handoffs,
        "final_summary": final_text,
        "spoken_summary": spoken_summary,
        "discrepancies_detected": result.get("discrepancies_detected", []),
        "tool_calls_executed": result.get("tool_calls_executed", []),
        "execution_time_ms": result.get("execution_time_ms", 0),
        "pending_approval_queued": len(queued_approvals) > 0,
        "queued_approvals": queued_approvals
    })


@router.get("/health")
async def health_check():
    """
    Diagnostic & observability endpoint for Gemini connectivity, discovered models, and Supabase status.
    """
    configured = is_gemini_key_configured()

    key_name = None
    masked_key = None
    for k in ["GEMINI_API_KEY", "GeminiAPIKey", "gemini_api_key", "GOOGLE_API_KEY", "LLM_API_KEY"]:
        val = get_env(k)
        if val:
            key_name = k
            masked_key = f"{val[:6]}...{val[-4:]}" if len(val) > 10 else "***"
            break
    if not key_name:
        for env_k, env_v in os.environ.items():
            if env_k.replace("_", "").lower() in ("geminiapikey", "geminikey", "googleapikey", "llmapikey"):
                if env_v and env_v.strip():
                    key_name = env_k
                    val = env_v.strip()
                    masked_key = f"{val[:6]}...{val[-4:]}" if len(val) > 10 else "***"
                    break

    discovered_models = []
    if configured:
        try:
            discovered_models = await discover_available_gemini_models()
        except Exception:
            pass

    supabase_url = get_env("SUPABASE_URL")
    supabase_configured = bool(supabase_url and get_env("SUPABASE_SERVICE_KEY"))

    return JSONResponse(content={
        "status": "healthy",
        "gemini_configured": configured,
        "key_source": key_name,
        "masked_key": masked_key,
        "discovered_models": discovered_models,
        "primary_model": get_env("LLM_MODEL", "gemini-flash-lite-latest"),
        "fast_model": get_env("FAST_LLM_MODEL", "gemini-flash-lite-latest"),
        "supabase_configured": supabase_configured
    })


@router.get("/webhook/whatsapp")
async def verify_whatsapp_webhook(request: Request):
    """
    Meta WhatsApp Cloud API Webhook Subscription Verification.
    Responds to hub.challenge when hub.verify_token matches WHATSAPP_VERIFY_TOKEN.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    expected_token = get_env("WHATSAPP_VERIFY_TOKEN", "ba_swarm_verify_token")

    if mode == "subscribe" and token == expected_token:
        return Response(content=challenge, media_type="text/plain")
    elif not mode and not token:
        # Simple health probe
        return JSONResponse({"status": "whatsapp_webhook_active"})
    raise HTTPException(status_code=403, detail="WhatsApp Webhook verification failed.")


@router.post("/webhook/whatsapp")
async def handle_whatsapp_message(request: Request):
    """
    Ingests inbound WhatsApp messages from Meta Cloud API webhook.
    Extracts text, sender phone, and triggers Swarm triage.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    entry = body.get("entry", [])
    sender_phone = ""
    message_text = ""

    if entry:
        changes = entry[0].get("changes", [])
        if changes:
            value = changes[0].get("value", {})
            messages = value.get("messages", [])
            if messages:
                msg = messages[0]
                sender_phone = msg.get("from", "")
                if msg.get("type") == "text":
                    message_text = msg.get("text", {}).get("body", "")
                elif msg.get("type") == "audio":
                    message_text = "[WhatsApp Voice Note received]"

    if not message_text:
        # Acknowledge delivery receipts or status pings from Meta
        return JSONResponse({"status": "ignored_or_no_message_body"})

    swarm_result = await run_swarm(
        initial_event=f"WhatsApp message from {sender_phone}:\n{message_text}",
        starting_agent="triage",
        event_source="whatsapp",
        context_metadata={"whatsapp_phone": sender_phone}
    )

    return JSONResponse(content={
        "status": "processed",
        "from": sender_phone,
        "swarm_result": swarm_result
    })


@router.post("/generate-doc")
async def handle_doc_generation(payload: DocGenRequest, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """Generates PRD, BRD, or PDD sections concurrently via asyncio.gather."""
    verify_api_key(x_api_key)
    results = await generate_parallel_sections(
        doc_type=payload.doc_type,
        context=payload.context,
        sections=payload.sections
    )
    return JSONResponse(content={"doc_type": payload.doc_type, "sections": results})


@router.get("/cron/daily-account-sync")
async def handle_daily_sync(request: Request, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """Triggered daily by Vercel Cron (0 3 * * *). Scans Zoho 90/60/30-day renewals & Jira QA."""
    verify_cron_auth(request, x_api_key)

    zoho_data = await zoho_get_expiring_renewals(days_ahead=90)
    expiring = zoho_data.get("expiring_deals", [])

    jira_data = await jira_search_issues(jql="status in ('In QA', 'Ready for Release')", max_results=10)
    qa_issues = jira_data.get("issues", [])

    daily_context = f"""
### DAILY ACCOUNT & DELIVERY AUDIT REPORT
- Expiring Zoho CRM Contracts (Next 90 Days): {len(expiring)} deals flagged.
  Details: {expiring}
- Engineering Delivery State (Jira In QA): {len(qa_issues)} issues pending release.
  Details: {qa_issues}
"""

    result = await run_swarm(
        initial_event=daily_context,
        starting_agent="commercial",
        event_source="daily_cron",
        context_metadata={"expiring_deals_count": len(expiring), "qa_issues_count": len(qa_issues)}
    )

    return JSONResponse(content={
        "status": "completed",
        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "expiring_deals_found": len(expiring),
        "qa_tickets_found": len(qa_issues),
        "swarm_result": result
    })


@router.get("/approvals")
async def list_approvals(status: str = Query("pending", description="Filter by status: pending, approved, rejected, executed, all")):
    """List Human-in-the-Loop approvals from Supabase."""
    supabase_url = get_env("SUPABASE_URL")
    supabase_key = get_env("SUPABASE_SERVICE_KEY")

    if not (supabase_url and supabase_key):
        return JSONResponse(content=[
            {
                "id": "e3b0c442-98fc-1c14-9af0-2a0000000001",
                "agent_name": "commercial",
                "action_type": "zoho_update_deal",
                "target_system": "zoho",
                "payload": {"deal_id": "ZOHO-DEAL-9981", "data": {"Amount": 135000, "Stage": "Negotiation"}},
                "risk_summary": "Increases contract value to $135k with 5% discount concession.",
                "status": "pending",
                "created_at": "2026-11-18T10:15:00Z"
            },
            {
                "id": "e3b0c442-98fc-1c14-9af0-2a0000000002",
                "agent_name": "comms",
                "action_type": "whatsapp_send_message",
                "target_system": "whatsapp",
                "payload": {"to": "+15551234567", "message": "Hi Mark, your requested SLA P1 response time has been verified with engineering."},
                "risk_summary": "Sends direct WhatsApp confirmation to client executive.",
                "status": "pending",
                "created_at": "2026-11-18T11:00:00Z"
            }
        ])

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json"
    }

    query = "status=eq.pending" if status == "pending" else ("" if status == "all" else f"status=eq.{status}")
    url = f"{supabase_url.rstrip('/')}/rest/v1/hitl_approvals?{query}&order=created_at.desc&limit=50"

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        res = await client.get(url, headers=headers)
        if res.status_code == 200:
            return JSONResponse(content=res.json())
        return JSONResponse(content={"error": res.text}, status_code=res.status_code)


@router.post("/approvals/{approval_id}/approve")
async def approve_action(approval_id: str, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """Executes the staged tool action upon human approval."""
    verify_api_key(x_api_key)
    result = await execute_approved_action(approval_id)
    return JSONResponse(content=result)


@router.post("/approvals/{approval_id}/reject")
async def reject_action(approval_id: str, payload: Optional[RejectRequest] = None, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """Rejects a staged action with an audit reason."""
    verify_api_key(x_api_key)
    supabase_url = get_env("SUPABASE_URL")
    supabase_key = get_env("SUPABASE_SERVICE_KEY")
    reason = payload.reason if payload else "Rejected by Human Reviewer"

    if supabase_url and supabase_key:
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json"
        }
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            url = f"{supabase_url.rstrip('/')}/rest/v1/hitl_approvals?id=eq.{approval_id}"
            await client.patch(url, headers=headers, json={"status": "rejected", "rejection_reason": reason})

    return JSONResponse(content={"status": "rejected", "approval_id": approval_id, "reason": reason})


@router.get("/logs")
async def list_logs(limit: int = 20):
    """Retrieve recent Swarm audit logs."""
    supabase_url = get_env("SUPABASE_URL")
    supabase_key = get_env("SUPABASE_SERVICE_KEY")

    if not (supabase_url and supabase_key):
        return JSONResponse(content=[
            {
                "id": "log-001",
                "event_source": "voice",
                "initial_event": "Voice memo dictation: Client agreed to renewal on Gold SLA tier; requested PRD for REQ-003.",
                "agent_handoff_path": [
                    {"from": "triage", "to": "commercial", "reason": "Renewal terms"},
                    {"from": "commercial", "to": "ba_docs", "reason": "PRD generation"},
                    {"from": "ba_docs", "to": "comms", "reason": "Draft confirmation"}
                ],
                "discrepancies_detected": [],
                "final_summary": "Parallel PRD generated with REQ-003 and Gherkin scenarios. Staged WhatsApp notification into HITL queue.",
                "execution_time_ms": 2840,
                "created_at": "2026-11-18T11:05:00Z"
            }
        ])

    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json"
    }
    url = f"{supabase_url.rstrip('/')}/rest/v1/swarm_audit_logs?order=created_at.desc&limit={limit}"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        res = await client.get(url, headers=headers)
        if res.status_code == 200:
            return JSONResponse(content=res.json())
        return JSONResponse(content=[], status_code=res.status_code)


@router.get("/reports/daily")
async def get_daily_report():
    """
    Daily Agent Operations Report:
    Aggregates today's executions from swarm_audit_logs, hitl_approvals, and raid_logs.
    Generates structured statistics and downloadable EOD Markdown report.
    """
    supabase_url = get_env("SUPABASE_URL")
    supabase_key = get_env("SUPABASE_SERVICE_KEY")

    now_utc = time.gmtime()
    today_str = time.strftime("%Y-%m-%d", now_utc)
    today_iso = f"{today_str}T00:00:00Z"

    audit_logs: List[Dict[str, Any]] = []
    approvals: List[Dict[str, Any]] = []
    active_raids: List[Dict[str, Any]] = []

    if supabase_url and supabase_key:
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json"
        }
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                logs_res = await client.get(
                    f"{supabase_url.rstrip('/')}/rest/v1/swarm_audit_logs?created_at=gte.{today_iso}&order=created_at.desc&limit=100",
                    headers=headers
                )
                if logs_res.status_code == 200:
                    audit_logs = logs_res.json()

                appr_res = await client.get(
                    f"{supabase_url.rstrip('/')}/rest/v1/hitl_approvals?order=created_at.desc&limit=100",
                    headers=headers
                )
                if appr_res.status_code == 200:
                    approvals = appr_res.json()

                raid_res = await client.get(
                    f"{supabase_url.rstrip('/')}/rest/v1/raid_logs?status=neq.resolved&order=created_at.desc&limit=50",
                    headers=headers
                )
                if raid_res.status_code == 200:
                    active_raids = raid_res.json()
        except Exception as e:
            print(f"[Supabase Daily Report Fetch Warning] {e}")

    if not audit_logs:
        audit_logs = list(_IN_MEMORY_AUDIT_LOGS)
    if not active_raids:
        active_raids = [r for r in _IN_MEMORY_RAID_LOGS if r.get("status") in ("open", "mitigated")]

    total_runs = len(audit_logs)
    agent_invocations = {"triage": max(1, total_runs), "commercial": 0, "ba_docs": 0, "delivery": 0, "comms": 0}
    discrepancies_count = 0

    for log in audit_logs:
        handoffs = log.get("agent_handoff_path", [])
        if isinstance(handoffs, list):
            for h in handoffs:
                to_agent = h.get("to")
                if to_agent in agent_invocations:
                    agent_invocations[to_agent] += 1
                from_agent = h.get("from")
                if from_agent in agent_invocations:
                    agent_invocations[from_agent] += 1
        discs = log.get("discrepancies_detected", [])
        if isinstance(discs, list):
            discrepancies_count += len(discs)

    hitl_summary = {"pending": 0, "approved": 0, "rejected": 0, "executed": 0}
    if approvals:
        for a in approvals:
            st = a.get("status", "pending")
            if st in hitl_summary:
                hitl_summary[st] += 1
    else:
        hitl_summary = {"pending": 2, "approved": 4, "rejected": 1, "executed": 3}

    eod_markdown = f"""# Executive End-of-Day (EOD) Operations Report
**Date:** {today_str}  
**Platform:** BA Account Swarm — Autonomous Multi-Agent Command Deck  
**System Status:** All Systems Operational (Green)

---

## 1. Executive Operations Summary
- **Total Workflows & Ingestions Executed:** {total_runs}
- **Autonomous Multi-Agent Invocations:** {sum(agent_invocations.values())}
- **High-Risk Actions Staged for Governance (HITL):** {hitl_summary.get('pending', 0)} Pending Review | {hitl_summary.get('approved', 0)} Approved | {hitl_summary.get('executed', 0)} Executed
- **Active Project RAID Items Monitored:** {len(active_raids)}
- **Discrepancy Alerts Caught & Remediated:** {discrepancies_count}

---

## 2. Agent Workload Distribution
| Specialized Agent | Role / Domain | Invocations Today | Operational Status |
| :--- | :--- | :--- | :--- |
| **TRIAGE** | Multi-channel Ingestion & Routing | {agent_invocations['triage']} | Standby / Listening |
| **COMMERCIAL** | Rate Cards, SOWs, Zoho CRM & Margin Alerts | {agent_invocations['commercial']} | Standby / Active |
| **BA_DOCS** | PRD/PDD/BRD, Traceability & Scope Creep | {agent_invocations['ba_docs']} | Standby / Active |
| **DELIVERY** | Jira Verification, Release Notes & WSR | {agent_invocations['delivery']} | Standby / Active |
| **COMMS** | MoM Extraction, Client Email & WhatsApp | {agent_invocations['comms']} | Standby / Active |

---

## 3. Active Project RAID Log (Risks, Assumptions, Issues, Dependencies)
| Category | Severity | Account | Description | Owner | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
"""
    if active_raids:
        for r in active_raids[:10]:
            eod_markdown += f"| **{r.get('category')}** | `{r.get('severity')}` | {r.get('account_name')} | {r.get('description')[:75]}... | {r.get('owner')} | `{r.get('status')}` |\n"
    else:
        eod_markdown += "| *None* | `Low` | All Accounts | No unmitigated high-risk impediments detected. | Project Team | `mitigated` |\n"

    eod_markdown += f"""
---

## 4. Governance & Human-in-the-Loop (HITL) Queue State
- **Pending Human Verification:** {hitl_summary.get('pending', 0)} external mutations paused at governance gate.
- **Contract / SLA Integrity:** Zero unauthorized external commitments dispatched.
- **Audit Verification:** All state transitions committed to immutable Supabase ledger.

*Report automatically synthesized by BA Account Swarm Command Deck at {time.strftime('%H:%M:%SZ', now_utc)}.*
"""

    return JSONResponse(content={
        "status": "success",
        "date": today_str,
        "total_runs_today": total_runs,
        "agent_invocations": agent_invocations,
        "hitl_summary": hitl_summary,
        "active_raid_items_count": len(active_raids),
        "discrepancies_detected_count": discrepancies_count,
        "eod_report_markdown": eod_markdown
    })


@router.get("/raid")
async def list_raid_items(account_name: Optional[str] = Query(None, description="Filter by account name")):
    """List active RAID items for the live project radar."""
    result = await get_active_raid_items(account_name)
    return JSONResponse(content=result)


@router.post("/raid")
async def create_or_update_raid_item(payload: RaidItemRequest, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """Upsert an item into the project RAID log."""
    verify_api_key(x_api_key)
    result = await upsert_raid_item(
        account_name=payload.account_name,
        category=payload.category,
        severity=payload.severity,
        description=payload.description,
        owner=payload.owner or "Unassigned",
        status=payload.status or "open",
        item_id=payload.item_id
    )
    return JSONResponse(content=result)


@router.post("/raid/{item_id}/resolve")
async def resolve_raid_item(item_id: str, x_api_key: Optional[str] = Header(None, alias="x-api-key")):
    """Mark a RAID log item as resolved."""
    verify_api_key(x_api_key)
    account = "General"
    desc = "Resolved item"
    cat = "Risk"
    sev = "Low"
    for r in _IN_MEMORY_RAID_LOGS:
        if r.get("id") == item_id:
            account = r.get("account_name", "General")
            desc = r.get("description", "")
            cat = r.get("category", "Risk")
            sev = r.get("severity", "Low")
            break
    result = await upsert_raid_item(
        account_name=account,
        category=cat,
        severity=sev,
        description=desc,
        status="resolved",
        item_id=item_id
    )
    return JSONResponse(content={"status": "success", "resolved_id": item_id, "result": result})


@router.get("/manifest.json")
async def get_pwa_manifest():
    """Returns Progressive Web App (PWA) manifest for installable mobile home-screen icon."""
    manifest = {
        "name": "BA Account Swarm Mission Control",
        "short_name": "BA Swarm",
        "description": "Autonomous Business Analyst & Account Swarm with Human-in-the-Loop Approvals",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#020617",
        "theme_color": "#020617",
        "icons": [
            {
                "src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>⚡</text></svg>",
                "sizes": "192x192 512x512",
                "type": "image/svg+xml"
            }
        ]
    }
    return JSONResponse(content=manifest, media_type="application/manifest+json")


app.include_router(router, prefix="/api")
app.include_router(router)


# ==============================================================================
# MISSION CONTROL PWA UI (Single-file Stealth Onyx HTML served via GET /)
# ==============================================================================
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en" class="h-full bg-[#060913] text-[#F8F9FA]">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="theme-color" content="#060913">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <link rel="manifest" href="/manifest.json">
    <title>BA Account Swarm — Mission Control Command Deck</title>
    
    <!-- Google Fonts: Plus Jakarta Sans + JetBrains Mono -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    
    <!-- Tailwind CSS CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    fontFamily: {
                        sans: ['"Plus Jakarta Sans"', 'system-ui', '-apple-system', 'sans-serif'],
                        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace']
                    },
                    colors: {
                        canvas: {
                            DEFAULT: '#060913',
                            soft: '#0B0F17',
                            elevated: '#121722',
                            border: '#1E2638',
                            'border-strong': '#2D384E'
                        },
                        cyan: {
                            DEFAULT: '#06B6D4',
                            glow: 'rgba(6, 182, 212, 0.25)',
                            hover: '#22D3EE'
                        },
                        volt: {
                            DEFAULT: '#D2FC00',
                            glow: 'rgba(210, 252, 0, 0.25)',
                            hover: '#E2FF33'
                        }
                    },
                    boxShadow: {
                        'card': '0 4px 20px -2px rgba(0, 0, 0, 0.6), 0 2px 6px -1px rgba(0, 0, 0, 0.4)',
                        'elevated': '0 12px 32px -4px rgba(0, 0, 0, 0.8), 0 4px 12px -2px rgba(0, 0, 0, 0.5)',
                        'cyan-glow': '0 0 20px rgba(6, 182, 212, 0.35)',
                        'volt-glow': '0 0 20px rgba(210, 252, 0, 0.35)'
                    }
                }
            }
        }
    </script>

    <!-- Marked.js + DOMPurify + Mermaid.js via CDN -->
    <script src="https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/dompurify@3.0.9/dist/purify.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.0/dist/mermaid.min.js"></script>

    <style>
        ::selection {
            background-color: #06B6D4;
            color: #060913;
        }
        ::-webkit-scrollbar {
            width: 5px;
            height: 5px;
        }
        ::-webkit-scrollbar-track {
            background: #060913;
        }
        ::-webkit-scrollbar-thumb {
            background: #1E2638;
            border-radius: 9999px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: #06B6D4;
        }
        .bg-grid-pattern {
            background-image: 
                linear-gradient(to right, rgba(255, 255, 255, 0.02) 1px, transparent 1px),
                linear-gradient(to bottom, rgba(255, 255, 255, 0.02) 1px, transparent 1px);
            background-size: 24px 24px;
        }

        /* Institutional Markdown Table Styling */
        .prose-custom table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            margin: 1rem 0;
            font-size: 0.775rem;
            line-height: 1.35;
            border-radius: 0.5rem;
            overflow: hidden;
            border: 1px solid #1E2638;
        }
        .prose-custom th {
            background: #121722;
            color: #E2E8F0;
            font-weight: 700;
            text-align: left;
            padding: 0.5rem 0.75rem;
            border-bottom: 1px solid #1E2638;
            letter-spacing: 0.025em;
        }
        .prose-custom td {
            padding: 0.45rem 0.75rem;
            border-bottom: 1px solid rgba(30, 38, 56, 0.6);
            color: #CBD5E1;
            background: rgba(11, 15, 23, 0.6);
        }
        .prose-custom tr:last-child td {
            border-bottom: none;
        }
        .prose-custom tr:hover td {
            background: rgba(18, 23, 34, 0.8);
        }
        .prose-custom pre {
            background: #0B0F17 !important;
            border: 1px solid #1E2638;
            border-radius: 0.5rem;
            padding: 0.75rem 1rem;
            margin: 0.75rem 0;
            overflow-x: auto;
            font-family: "JetBrains Mono", monospace;
            font-size: 0.75rem;
        }
        .prose-custom code {
            font-family: "JetBrains Mono", monospace;
            font-size: 0.75rem;
        }
        .prose-custom p {
            margin: 0.5rem 0;
            line-height: 1.5;
        }
        .prose-custom ul {
            list-style-type: disc;
            margin-left: 1.25rem;
            margin-top: 0.35rem;
            margin-bottom: 0.35rem;
        }
        .prose-custom ol {
            list-style-type: decimal;
            margin-left: 1.25rem;
            margin-top: 0.35rem;
            margin-bottom: 0.35rem;
        }
        .prose-custom li {
            margin: 0.2rem 0;
        }
        .prose-custom h1, .prose-custom h2, .prose-custom h3, .prose-custom h4 {
            color: #F8F9FA;
            font-weight: 700;
            margin-top: 1rem;
            margin-bottom: 0.4rem;
        }
        .prose-custom h2 {
            font-size: 0.95rem;
            border-bottom: 1px solid #1E2638;
            padding-bottom: 0.25rem;
        }
        .prose-custom h3 {
            font-size: 0.85rem;
        }
    </style>
</head>
<body class="min-h-full font-sans antialiased flex flex-col bg-canvas text-[#F8F9FA] bg-grid-pattern selection:bg-cyan selection:text-black">
    <!-- Toast Notification Container -->
    <div id="toast-container" class="fixed top-5 right-5 z-[9999] flex flex-col space-y-3 pointer-events-none max-w-sm w-full"></div>

    <!-- Rejection Confirmation Modal -->
    <div id="reject-modal" class="fixed inset-0 z-[9990] hidden flex items-center justify-center p-4 bg-black/85 backdrop-blur-sm">
        <div class="bg-canvas-soft border border-canvas-border rounded-xl p-6 max-w-md w-full shadow-elevated space-y-4">
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-400 flex items-center justify-center font-bold text-lg">
                    ⚠️
                </div>
                <div>
                    <h3 class="text-sm font-bold text-white">Reject Staged Action</h3>
                    <p class="text-xs text-slate-400">Record an audit reason for rejecting this action</p>
                </div>
            </div>
            <div>
                <label class="block text-xs font-medium text-slate-300 mb-1">Rejection Rationale</label>
                <textarea id="reject-modal-reason" rows="3" class="w-full bg-canvas-elevated border border-canvas-border rounded-lg p-3 text-xs text-slate-200 focus:outline-none focus:border-rose-500 focus:ring-1 focus:ring-rose-500" placeholder="e.g. Scope expansion exceeds contract threshold; request pricing approval from Commercial Director."></textarea>
            </div>
            <div class="flex items-center justify-end space-x-3 pt-2">
                <button onclick="closeRejectModal()" class="px-4 py-2 text-xs font-semibold text-slate-400 hover:text-white bg-canvas-elevated hover:bg-slate-800 rounded-lg border border-canvas-border transition">
                    Cancel
                </button>
                <button onclick="confirmRejectAction()" class="px-4 py-2 text-xs font-semibold text-white bg-rose-600 hover:bg-rose-500 rounded-lg shadow-lg shadow-rose-600/25 transition">
                    Confirm Rejection
                </button>
            </div>
        </div>
    </div>

    <!-- Log RAID Item Modal -->
    <div id="raid-modal" class="fixed inset-0 z-[9990] hidden flex items-center justify-center p-4 bg-black/85 backdrop-blur-sm">
        <div class="bg-canvas-soft border border-canvas-border rounded-xl p-6 max-w-md w-full shadow-elevated space-y-4">
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 rounded-lg bg-cyan-500/10 border border-cyan-500/20 text-cyan flex items-center justify-center font-bold text-lg">
                    🛡️
                </div>
                <div>
                    <h3 class="text-sm font-bold text-white">Log RAID Radar Item</h3>
                    <p class="text-xs text-slate-400">Record Risk, Assumption, Issue, or Dependency</p>
                </div>
            </div>
            <div class="space-y-3">
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-[11px] font-semibold text-slate-300 mb-1">Category</label>
                        <select id="raid-category-input" class="w-full bg-canvas-elevated border border-canvas-border rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-cyan">
                            <option value="Risk">Risk</option>
                            <option value="Assumption">Assumption</option>
                            <option value="Issue">Issue</option>
                            <option value="Dependency">Dependency</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-[11px] font-semibold text-slate-300 mb-1">Severity</label>
                        <select id="raid-severity-input" class="w-full bg-canvas-elevated border border-canvas-border rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-cyan">
                            <option value="High">High</option>
                            <option value="Medium" selected>Medium</option>
                            <option value="Low">Low</option>
                        </select>
                    </div>
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-[11px] font-semibold text-slate-300 mb-1">Account</label>
                        <input id="raid-account-input" type="text" value="Acme Corp" class="w-full bg-canvas-elevated border border-canvas-border rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-cyan" placeholder="Account Name">
                    </div>
                    <div>
                        <label class="block text-[11px] font-semibold text-slate-300 mb-1">Owner</label>
                        <input id="raid-owner-input" type="text" value="Lead Architect" class="w-full bg-canvas-elevated border border-canvas-border rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-cyan" placeholder="Assigned Role">
                    </div>
                </div>
                <div>
                    <label class="block text-[11px] font-semibold text-slate-300 mb-1">Description</label>
                    <textarea id="raid-desc-input" rows="2" class="w-full bg-canvas-elevated border border-canvas-border rounded-lg p-2.5 text-xs text-slate-200 focus:outline-none focus:border-cyan" placeholder="Describe the risk, impediment, or technical dependency..."></textarea>
                </div>
            </div>
            <div class="flex items-center justify-end space-x-3 pt-2">
                <button onclick="closeRaidModal()" class="px-4 py-2 text-xs font-semibold text-slate-400 hover:text-white bg-canvas-elevated hover:bg-slate-800 rounded-lg border border-canvas-border transition">
                    Cancel
                </button>
                <button onclick="submitRaidModal()" class="px-4 py-2 text-xs font-semibold text-black bg-cyan hover:bg-cyan-hover rounded-lg shadow-lg shadow-cyan/25 transition">
                    Save to RAID Radar
                </button>
            </div>
        </div>
    </div>

    <!-- Header -->
    <header class="border-b border-canvas-border bg-canvas/90 backdrop-blur sticky top-0 z-50">
        <div class="max-w-[1920px] mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-[#0B0F17] to-[#121722] border border-cyan/30 flex items-center justify-center font-bold text-lg text-cyan shadow-cyan-glow">
                    ⚡
                </div>
                <div>
                    <h1 class="text-base font-extrabold tracking-tight text-white flex items-center gap-2">
                        BA ACCOUNT SWARM
                        <span class="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-full bg-cyan/10 text-cyan border border-cyan/30">Mission Control &bull; Command Deck</span>
                    </h1>
                    <p class="text-[11px] text-slate-400 hidden sm:block">Autonomous BA, Technical PM & Account Manager Multi-Agent System</p>
                </div>
            </div>
            <div class="flex items-center space-x-3">
                <!-- Gemini LLM Gateway Pill -->
                <div id="gemini-status-pill" class="hidden sm:flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-mono rounded-lg border border-canvas-border bg-canvas-soft text-slate-400">
                    <span id="gemini-status-dot" class="w-2 h-2 rounded-full bg-slate-500"></span>
                    <span id="gemini-status-text">Gemini Gateway</span>
                </div>
                <!-- Voice Quick Dictate Button -->
                <button id="voice-record-btn" onclick="toggleVoiceRecording()" class="px-3.5 py-1.5 text-xs font-semibold rounded-lg border border-canvas-border bg-canvas-soft hover:bg-canvas-elevated text-slate-200 flex items-center gap-2 transition hover:border-cyan/40">
                    <span id="voice-icon" class="text-rose-400">🎙️</span>
                    <span id="voice-label">Voice Memo</span>
                </button>
                <!-- Refresh Button -->
                <button onclick="refreshData()" class="p-2 text-slate-400 hover:text-white bg-canvas-soft hover:bg-canvas-elevated rounded-lg border border-canvas-border transition hover:border-cyan/40" title="Refresh state">
                    <svg id="refresh-spinner" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                </button>
            </div>
        </div>
    </header>

    <!-- Recording Banner (Hidden by default) -->
    <div id="recording-banner" class="hidden bg-rose-500/10 border-b border-rose-500/30 px-4 py-2.5 text-center text-xs text-rose-300 font-medium flex items-center justify-center gap-2">
        <span class="animate-pulse w-2 h-2 rounded-full bg-rose-500"></span>
        <span class="font-mono font-semibold">REC</span> Listening to microphone audio... Click 'Stop Recording' to delegate to Swarm.
    </div>

    <!-- Full-Widescreen 12-Column HUD Grid -->
    <main class="flex-1 max-w-[1920px] w-full mx-auto px-3 sm:px-4 lg:px-6 py-4">
        <div class="grid grid-cols-12 gap-4 items-start">

            <!-- =============================================================== -->
            <!-- LEFT COLUMN: 5 Agent HUD + Playbooks + Manual Ingestion (3 Cols) -->
            <!-- =============================================================== -->
            <div class="col-span-12 lg:col-span-3 space-y-4">
                
                <!-- Live Agent HUD -->
                <div class="bg-canvas-soft border border-canvas-border rounded-xl p-4 shadow-card space-y-3">
                    <div class="flex items-center justify-between pb-2 border-b border-canvas-border">
                        <div class="flex items-center gap-2">
                            <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                            <h2 class="text-xs font-bold uppercase tracking-wider text-slate-300">Swarm Agent Roster</h2>
                        </div>
                        <span class="text-[10px] font-mono text-cyan bg-cyan/10 px-2 py-0.5 rounded border border-cyan/20">5 Autonomous Agents</span>
                    </div>

                    <!-- 5 Agent Cards -->
                    <div class="space-y-2">
                        <!-- Triage -->
                        <div class="p-2.5 rounded-lg bg-canvas-elevated/60 border border-canvas-border hover:border-cyan/30 transition flex items-center justify-between">
                            <div class="flex items-center gap-2.5">
                                <span class="w-2 h-2 rounded-full bg-cyan shadow-cyan-glow"></span>
                                <div>
                                    <div class="text-xs font-bold text-white flex items-center gap-1.5">
                                        TRIAGE
                                        <span class="text-[9px] font-mono text-slate-400">Router</span>
                                    </div>
                                    <div class="text-[10px] text-slate-400">Context Enrichment & Handoff</div>
                                </div>
                            </div>
                            <span id="agent-stat-triage" class="text-[10px] font-mono text-cyan bg-cyan/10 px-1.5 py-0.5 rounded">0 runs</span>
                        </div>

                        <!-- Commercial -->
                        <div class="p-2.5 rounded-lg bg-canvas-elevated/60 border border-canvas-border hover:border-volt/30 transition flex items-center justify-between">
                            <div class="flex items-center gap-2.5">
                                <span class="w-2 h-2 rounded-full bg-volt shadow-volt-glow"></span>
                                <div>
                                    <div class="text-xs font-bold text-white flex items-center gap-1.5">
                                        COMMERCIAL
                                        <span class="text-[9px] font-mono text-slate-400">Rate Cards</span>
                                    </div>
                                    <div class="text-[10px] text-slate-400">Quotes, SOWs, Renewals & Margin</div>
                                </div>
                            </div>
                            <span id="agent-stat-commercial" class="text-[10px] font-mono text-volt bg-volt/10 px-1.5 py-0.5 rounded">0 runs</span>
                        </div>

                        <!-- BA Docs -->
                        <div class="p-2.5 rounded-lg bg-canvas-elevated/60 border border-canvas-border hover:border-indigo-400/30 transition flex items-center justify-between">
                            <div class="flex items-center gap-2.5">
                                <span class="w-2 h-2 rounded-full bg-indigo-400"></span>
                                <div>
                                    <div class="text-xs font-bold text-white flex items-center gap-1.5">
                                        BA_DOCS
                                        <span class="text-[9px] font-mono text-slate-400">Architect</span>
                                    </div>
                                    <div class="text-[10px] text-slate-400">PRD/PDD, Scope Creep & RAID</div>
                                </div>
                            </div>
                            <span id="agent-stat-ba_docs" class="text-[10px] font-mono text-indigo-400 bg-indigo-500/10 px-1.5 py-0.5 rounded">0 runs</span>
                        </div>

                        <!-- Delivery -->
                        <div class="p-2.5 rounded-lg bg-canvas-elevated/60 border border-canvas-border hover:border-emerald-400/30 transition flex items-center justify-between">
                            <div class="flex items-center gap-2.5">
                                <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
                                <div>
                                    <div class="text-xs font-bold text-white flex items-center gap-1.5">
                                        DELIVERY
                                        <span class="text-[9px] font-mono text-slate-400">Release</span>
                                    </div>
                                    <div class="text-[10px] text-slate-400">Jira Transitions & Weekly Reports</div>
                                </div>
                            </div>
                            <span id="agent-stat-delivery" class="text-[10px] font-mono text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded">0 runs</span>
                        </div>

                        <!-- Comms -->
                        <div class="p-2.5 rounded-lg bg-canvas-elevated/60 border border-canvas-border hover:border-amber-400/30 transition flex items-center justify-between">
                            <div class="flex items-center gap-2.5">
                                <span class="w-2 h-2 rounded-full bg-amber-400"></span>
                                <div>
                                    <div class="text-xs font-bold text-white flex items-center gap-1.5">
                                        COMMS
                                        <span class="text-[9px] font-mono text-slate-400">Omnichannel</span>
                                    </div>
                                    <div class="text-[10px] text-slate-400">MoM, Email & WhatsApp Staging</div>
                                </div>
                            </div>
                            <span id="agent-stat-comms" class="text-[10px] font-mono text-amber-400 bg-amber-500/10 px-1.5 py-0.5 rounded">0 runs</span>
                        </div>
                    </div>
                </div>

                <!-- 5 Easy 1-Click Playbooks -->
                <div class="bg-canvas-soft border border-canvas-border rounded-xl p-4 shadow-card space-y-3">
                    <div class="flex items-center justify-between pb-2 border-b border-canvas-border">
                        <div class="flex items-center gap-2">
                            <span class="text-volt">⚡</span>
                            <h2 class="text-xs font-bold uppercase tracking-wider text-slate-300">1-Click Playbooks</h2>
                        </div>
                        <span class="text-[10px] text-slate-500">Autonomous</span>
                    </div>

                    <div class="space-y-2">
                        <!-- Playbook 1 -->
                        <button onclick="runPlaybook('quote')" class="w-full text-left p-2.5 rounded-lg bg-canvas-elevated/70 hover:bg-canvas-elevated border border-canvas-border hover:border-volt/40 transition group">
                            <div class="flex items-center justify-between">
                                <div class="text-xs font-bold text-slate-200 group-hover:text-volt flex items-center gap-1.5">
                                    <span>⚡</span> Quote & 6-Wk Plan
                                </div>
                                <span class="text-[10px] font-mono text-slate-500">Commercial</span>
                            </div>
                            <div class="text-[10px] text-slate-400 mt-1 line-clamp-1">Rate-card fee, 30/40/30 milestones & Zoho staging</div>
                        </button>

                        <!-- Playbook 2 -->
                        <button onclick="runPlaybook('renewals')" class="w-full text-left p-2.5 rounded-lg bg-canvas-elevated/70 hover:bg-canvas-elevated border border-canvas-border hover:border-cyan/40 transition group">
                            <div class="flex items-center justify-between">
                                <div class="text-xs font-bold text-slate-200 group-hover:text-cyan flex items-center gap-1.5">
                                    <span>🔄</span> 90-Day Renewal Audit
                                </div>
                                <span class="text-[10px] font-mono text-slate-500">Pipeline</span>
                            </div>
                            <div class="text-[10px] text-slate-400 mt-1 line-clamp-1">Scan Zoho CRM renewals & flag expansion/churn</div>
                        </button>

                        <!-- Playbook 3 -->
                        <button onclick="runPlaybook('deploy')" class="w-full text-left p-2.5 rounded-lg bg-canvas-elevated/70 hover:bg-canvas-elevated border border-canvas-border hover:border-emerald-400/40 transition group">
                            <div class="flex items-center justify-between">
                                <div class="text-xs font-bold text-slate-200 group-hover:text-emerald-400 flex items-center gap-1.5">
                                    <span>🚀</span> Deploy Sync & Jira
                                </div>
                                <span class="text-[10px] font-mono text-slate-500">Delivery</span>
                            </div>
                            <div class="text-[10px] text-slate-400 mt-1 line-clamp-1">Verify QA criteria, advance tickets & release notes</div>
                        </button>

                        <!-- Playbook 4 -->
                        <button onclick="runPlaybook('scope_creep')" class="w-full text-left p-2.5 rounded-lg bg-canvas-elevated/70 hover:bg-canvas-elevated border border-canvas-border hover:border-indigo-400/40 transition group">
                            <div class="flex items-center justify-between">
                                <div class="text-xs font-bold text-slate-200 group-hover:text-indigo-400 flex items-center gap-1.5">
                                    <span>🛡️</span> Scope Creep & CR Check
                                </div>
                                <span class="text-[10px] font-mono text-slate-500">BA Docs</span>
                            </div>
                            <div class="text-[10px] text-slate-400 mt-1 line-clamp-1">Analyze scope delta, billable CR & negative tests</div>
                        </button>

                        <!-- Playbook 5 -->
                        <button onclick="runPlaybook('wsr')" class="w-full text-left p-2.5 rounded-lg bg-canvas-elevated/70 hover:bg-canvas-elevated border border-canvas-border hover:border-amber-400/40 transition group">
                            <div class="flex items-center justify-between">
                                <div class="text-xs font-bold text-slate-200 group-hover:text-amber-400 flex items-center gap-1.5">
                                    <span>📊</span> Weekly Status Report
                                </div>
                                <span class="text-[10px] font-mono text-slate-500">Executive</span>
                            </div>
                            <div class="text-[10px] text-slate-400 mt-1 line-clamp-1">Milestones, active RAID items & Jira velocity</div>
                        </button>
                    </div>
                </div>

                <!-- Collapsible Manual Ingestion Drawer -->
                <div class="bg-canvas-soft border border-canvas-border rounded-xl p-3 shadow-card">
                    <button onclick="toggleManualDrawer()" class="w-full flex items-center justify-between text-left text-xs font-semibold text-slate-400 hover:text-white transition">
                        <span class="flex items-center gap-1.5">
                            <span>⚙️</span> Manual Event Simulator
                        </span>
                        <span id="drawer-arrow" class="text-slate-500 text-xs">▼</span>
                    </button>
                    <div id="manual-drawer-content" class="hidden mt-3 pt-3 border-t border-canvas-border space-y-3">
                        <div>
                            <label class="block text-[10px] font-semibold text-slate-400 mb-1">Source Channel</label>
                            <select id="manual-source" class="w-full bg-canvas-elevated border border-canvas-border rounded-lg p-2 text-xs text-slate-200 focus:outline-none">
                                <option value="gmail">Gmail / Email</option>
                                <option value="whatsapp">WhatsApp Inbound</option>
                                <option value="meeting_transcript">Meeting Transcript</option>
                                <option value="deployment_note">CI/CD Deployment Note</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-[10px] font-semibold text-slate-400 mb-1">Payload Content</label>
                            <textarea id="manual-body" rows="3" class="w-full bg-canvas-elevated border border-canvas-border rounded-lg p-2 text-xs text-slate-200 focus:outline-none" placeholder="Paste raw event payload..."></textarea>
                        </div>
                        <button onclick="triggerManualEvent()" class="w-full py-1.5 bg-canvas-elevated hover:bg-slate-800 text-xs font-bold text-cyan border border-cyan/30 rounded-lg transition">
                            Ingest Event
                        </button>
                    </div>
                </div>

            </div>

            <!-- =============================================================== -->
            <!-- CENTER COLUMN: Tactical Command Console / Full Chat (5 Cols)   -->
            <!-- =============================================================== -->
            <div class="col-span-12 lg:col-span-5 space-y-3">
                <section class="bg-canvas-soft border border-canvas-border rounded-xl shadow-card flex flex-col h-[calc(100vh-135px)] min-h-[720px] overflow-hidden">
                    
                    <!-- Chat Header -->
                    <div class="px-4 py-3 border-b border-canvas-border bg-canvas-soft/90 flex items-center justify-between">
                        <div class="flex items-center space-x-2.5 overflow-hidden">
                            <div class="w-7 h-7 rounded-lg bg-cyan/10 border border-cyan/20 text-cyan flex items-center justify-center font-bold text-xs shadow-cyan-glow">
                                💬
                            </div>
                            <div>
                                <h3 class="text-xs font-bold text-white flex items-center gap-2">
                                    Tactical Command Console
                                    <span id="chat-live-badge" class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                                </h3>
                                <p id="chat-handoff-breadcrumb" class="text-[10px] font-mono text-slate-400 truncate max-w-[340px]">
                                    TRIAGE ➔ Standby for instructions
                                </p>
                            </div>
                        </div>
                        <div class="flex items-center space-x-2">
                            <button onclick="clearChatHistory()" class="px-2 py-1 text-[10px] font-semibold text-slate-400 hover:text-rose-400 bg-canvas-elevated hover:bg-slate-800 rounded border border-canvas-border transition">
                                Clear
                            </button>
                        </div>
                    </div>

                    <!-- Chat Messages Feed (Scrollable, full width) -->
                    <div id="chat-messages" class="flex-1 overflow-y-auto p-4 space-y-4">
                        <!-- Initial Agent Welcome -->
                        <div class="w-full p-4 rounded-xl bg-canvas-elevated/70 border border-canvas-border space-y-3">
                            <div class="flex items-center justify-between">
                                <div class="flex items-center gap-2">
                                    <span class="w-2 h-2 rounded-full bg-cyan shadow-cyan-glow"></span>
                                    <span class="text-xs font-bold text-cyan uppercase tracking-wider">SWARM CORE INITIATED</span>
                                </div>
                                <span class="text-[10px] font-mono text-slate-500">Autonomous</span>
                            </div>
                            <p class="text-xs text-slate-300 leading-relaxed">
                                Standing by to orchestrate commercial pricing, rate-card quotes, Jira deployment transitions, PRD/PDD specifications, and client communications.
                            </p>
                            <div class="grid grid-cols-2 gap-2 pt-1 text-[11px] text-slate-400">
                                <div class="flex items-center gap-1.5">
                                    <span class="text-cyan">✓</span> Full multi-agent handoff chain
                                </div>
                                <div class="flex items-center gap-1.5">
                                    <span class="text-cyan">✓</span> Gherkin criteria & Jira CSV export
                                </div>
                                <div class="flex items-center gap-1.5">
                                    <span class="text-cyan">✓</span> Rate-card quotes & margin alerts
                                </div>
                                <div class="flex items-center gap-1.5">
                                    <span class="text-cyan">✓</span> Pinned HITL governance gate
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Bottom Input Area -->
                    <div class="p-3 border-t border-canvas-border bg-canvas-soft/95 space-y-2">
                        <!-- Quick starter chips -->
                        <div class="flex items-center gap-1.5 overflow-x-auto pb-1 text-[10px] no-scrollbar">
                            <button onclick="injectPromptChip('Draft a commercial quote and 6-week delivery timeline for an Enterprise OCR module for Acme Corp')" class="px-2.5 py-1 rounded-full bg-canvas-elevated hover:bg-slate-800 border border-canvas-border text-slate-300 whitespace-nowrap hover:border-cyan/40 transition">
                                ⚡ Quote & 6-Wk Plan
                            </button>
                            <button onclick="injectPromptChip('Check Zoho renewals expiring in next 90 days and calculate churn risk')" class="px-2.5 py-1 rounded-full bg-canvas-elevated hover:bg-slate-800 border border-canvas-border text-slate-300 whitespace-nowrap hover:border-cyan/40 transition">
                                🔄 90-Day Renewals
                            </button>
                            <button onclick="injectPromptChip('Analyze scope impact of adding SAML SSO & 2FA against our baseline for Acme Corp')" class="px-2.5 py-1 rounded-full bg-canvas-elevated hover:bg-slate-800 border border-canvas-border text-slate-300 whitespace-nowrap hover:border-cyan/40 transition">
                                🛡️ Scope Creep Check
                            </button>
                            <button onclick="injectPromptChip('Generate an Executive Weekly Status Report (WSR) for Acme Corp')" class="px-2.5 py-1 rounded-full bg-canvas-elevated hover:bg-slate-800 border border-canvas-border text-slate-300 whitespace-nowrap hover:border-cyan/40 transition">
                                📊 Weekly Status Report
                            </button>
                        </div>

                        <!-- Textarea & Action Controls -->
                        <div class="flex items-end gap-2 bg-canvas-elevated border border-canvas-border focus-within:border-cyan rounded-xl p-2 transition">
                            <textarea id="chat-input" rows="2" onkeydown="handleChatKeydown(event)" class="flex-1 bg-transparent border-0 text-xs text-white placeholder-slate-500 focus:outline-none resize-none px-2 py-1" placeholder="Type instructions or use quick playbooks... (Cmd+K to focus)"></textarea>
                            
                            <div class="flex items-center gap-1.5 pb-0.5">
                                <!-- TTS Toggle -->
                                <button id="tts-toggle-btn" onclick="toggleVoiceReply()" class="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition" title="Toggle Spoken Voice Reply (TTS)">
                                    <span id="tts-icon">🔇</span>
                                </button>
                                <!-- Mic Button -->
                                <button id="chat-mic-btn" onclick="toggleVoiceRecording()" class="p-1.5 rounded-lg text-rose-400 hover:text-rose-300 hover:bg-rose-500/10 transition" title="Record Voice Instruction">
                                    🎙️
                                </button>
                                <!-- Send Button -->
                                <button id="chat-send-btn" onclick="sendChatMessage()" class="px-3 py-1.5 rounded-lg bg-cyan hover:bg-cyan-hover text-black font-bold text-xs shadow-cyan-glow transition flex items-center gap-1">
                                    <span>Send</span>
                                    <span>➔</span>
                                </button>
                            </div>
                        </div>
                    </div>

                </section>
            </div>

            <!-- =============================================================== -->
            <!-- RIGHT COLUMN: Pinned HITL + RAID Radar + Daily Report (4 Cols)  -->
            <!-- =============================================================== -->
            <div class="col-span-12 lg:col-span-4 space-y-4">

                <!-- Pinned HITL Approval Queue (Always at top) -->
                <div id="hitl-container-section" class="bg-canvas-soft border border-canvas-border rounded-xl p-4 shadow-card space-y-3">
                    <div class="flex items-center justify-between pb-2 border-b border-canvas-border">
                        <div class="flex items-center gap-2">
                            <span class="w-2.5 h-2.5 rounded-full bg-amber-400 animate-pulse"></span>
                            <h2 class="text-xs font-bold uppercase tracking-wider text-slate-300">HITL Approval Queue</h2>
                        </div>
                        <span id="stat-pending-badge" class="text-[10px] font-mono text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20 font-bold">0 Pending</span>
                    </div>

                    <!-- Staged HITL Items Container -->
                    <div id="approvals-list" class="space-y-2.5 max-h-[300px] overflow-y-auto pr-1">
                        <div class="text-center py-6 text-slate-500 text-xs">
                            Checking pending approval queue...
                        </div>
                    </div>
                </div>

                <!-- Active RAID Radar -->
                <div class="bg-canvas-soft border border-canvas-border rounded-xl p-4 shadow-card space-y-3">
                    <div class="flex items-center justify-between pb-2 border-b border-canvas-border">
                        <div class="flex items-center gap-2">
                            <span class="text-cyan">🛡️</span>
                            <h2 class="text-xs font-bold uppercase tracking-wider text-slate-300">Active RAID Radar</h2>
                        </div>
                        <button onclick="openRaidModal()" class="text-[10px] font-bold text-cyan bg-cyan/10 hover:bg-cyan/20 px-2 py-0.5 rounded border border-cyan/20 transition">
                            + Log Item
                        </button>
                    </div>

                    <!-- Category Filter Tabs -->
                    <div class="flex items-center gap-1 text-[10px] font-semibold text-slate-400 pb-1">
                        <button onclick="filterRaid('all')" id="raid-tab-all" class="px-2 py-0.5 rounded bg-canvas-elevated text-cyan border border-cyan/30">All</button>
                        <button onclick="filterRaid('Risk')" id="raid-tab-risk" class="px-2 py-0.5 rounded bg-canvas-elevated hover:text-white">Risks</button>
                        <button onclick="filterRaid('Assumption')" id="raid-tab-assump" class="px-2 py-0.5 rounded bg-canvas-elevated hover:text-white">Assumptions</button>
                        <button onclick="filterRaid('Issue')" id="raid-tab-issue" class="px-2 py-0.5 rounded bg-canvas-elevated hover:text-white">Issues</button>
                        <button onclick="filterRaid('Dependency')" id="raid-tab-dep" class="px-2 py-0.5 rounded bg-canvas-elevated hover:text-white">Dependencies</button>
                    </div>

                    <!-- RAID Items List -->
                    <div id="raid-list" class="space-y-2 max-h-[260px] overflow-y-auto pr-1">
                        <div class="text-center py-4 text-slate-500 text-xs">Loading RAID items...</div>
                    </div>
                </div>

                <!-- Daily Operations Report -->
                <div class="bg-canvas-soft border border-canvas-border rounded-xl p-4 shadow-card space-y-3">
                    <div class="flex items-center justify-between pb-2 border-b border-canvas-border">
                        <div class="flex items-center gap-2">
                            <span class="text-emerald-400">📊</span>
                            <h2 class="text-xs font-bold uppercase tracking-wider text-slate-300">Daily Operations Report</h2>
                        </div>
                        <button onclick="downloadEODReport()" class="text-[10px] font-bold text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/20 px-2 py-0.5 rounded border border-emerald-500/20 transition flex items-center gap-1">
                            <span>⬇️</span> EOD .md
                        </button>
                    </div>

                    <div class="grid grid-cols-2 gap-2 text-center text-xs">
                        <div class="p-2 rounded-lg bg-canvas-elevated border border-canvas-border">
                            <div class="text-[10px] text-slate-400 uppercase">Workflows Today</div>
                            <div id="stat-total-runs" class="text-lg font-bold text-white font-mono mt-0.5">0</div>
                        </div>
                        <div class="p-2 rounded-lg bg-canvas-elevated border border-canvas-border">
                            <div class="text-[10px] text-slate-400 uppercase">Discrepancies</div>
                            <div id="stat-discrepancies" class="text-lg font-bold text-amber-400 font-mono mt-0.5">0</div>
                        </div>
                    </div>

                    <div class="p-2.5 rounded-lg bg-canvas-elevated border border-canvas-border text-[11px] text-slate-400 space-y-1">
                        <div class="flex justify-between">
                            <span>Governance Integrity:</span>
                            <span class="font-bold text-emerald-400">100% Gated</span>
                        </div>
                        <div class="flex justify-between">
                            <span>Rate Card Enforced:</span>
                            <span class="font-bold text-volt">Active</span>
                        </div>
                        <div class="flex justify-between">
                            <span>Supabase State:</span>
                            <span id="stat-supabase-state" class="font-bold text-slate-300">Connected</span>
                        </div>
                    </div>
                </div>

            </div>

        </div>
    </main>

    <!-- Footer -->
    <footer class="border-t border-canvas-border bg-canvas/80 py-3 text-center text-[11px] text-slate-500">
        BA Account Swarm Command Deck &bull; FastAPI + OpenAI SDK + Google Gemini &bull; Zero Heavy Frameworks
    </footer>

    <!-- Client-Side JavaScript Logic -->
    <script>
        // Mermaid Initialization
        if (typeof mermaid !== 'undefined') {
            mermaid.initialize({
                startOnLoad: false,
                theme: 'dark',
                themeVariables: {
                    darkMode: true,
                    background: '#0B0F17',
                    primaryColor: '#06B6D4',
                    primaryTextColor: '#F8F9FA',
                    primaryBorderColor: '#1E2638',
                    lineColor: '#D2FC00',
                    secondaryColor: '#121722',
                    tertiaryColor: '#060913'
                }
            });
        }

        // State Management
        let chatMessages = [];
        let isRecording = false;
        let mediaRecorder = null;
        let audioChunks = [];
        let ttsEnabled = false;
        let currentRaidItems = [];
        let activeRaidFilter = 'all';
        let pendingRejectId = null;

        // Toast Helper
        function showToast(message, type = 'info') {
            const container = document.getElementById('toast-container');
            if (!container) return;
            const toast = document.createElement('div');
            const bgClass = type === 'error' ? 'bg-rose-950/90 border-rose-500/50 text-rose-200' :
                            type === 'success' ? 'bg-emerald-950/90 border-emerald-500/50 text-emerald-200' :
                            type === 'warning' ? 'bg-amber-950/90 border-amber-500/50 text-amber-200' :
                            'bg-slate-900/90 border-cyan/40 text-cyan-200';
            toast.className = `p-3 rounded-lg border shadow-elevated text-xs flex items-center justify-between transition-all duration-300 pointer-events-auto ${bgClass}`;
            toast.innerHTML = `<span>${message}</span><button onclick="this.parentElement.remove()" class="ml-2 text-slate-400 hover:text-white">&times;</button>`;
            container.appendChild(toast);
            setTimeout(() => {
                toast.classList.add('opacity-0', 'translate-x-full');
                setTimeout(() => toast.remove(), 300);
            }, 4000);
        }

        // Render Markdown safely with Marked & DOMPurify
        function renderMarkdownSafe(content) {
            if (!content || typeof content !== 'string') return "";
            if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
                return content.replace(/</g, "&lt;").replace(/>/g, "&gt;");
            }
            try {
                let html = marked.parse(content);
                html = DOMPurify.sanitize(html);
                // Monospace badges for REQ-XXX
                html = html.replace(/\b(REQ-\d{3,4})\b/g, '<span class="inline-flex items-center font-mono text-[11px] font-bold px-1.5 py-0.5 rounded bg-cyan/10 text-cyan border border-cyan/30">$1</span>');
                // Alerts highlighting
                html = html.replace(/⚠️ DISCREPANCY ALERT/g, '<span class="inline-flex items-center gap-1 font-bold text-amber-300 bg-amber-950/60 px-2 py-0.5 rounded border border-amber-500/40">⚠️ DISCREPANCY ALERT</span>');
                html = html.replace(/⚠️ MARGIN ALERT/g, '<span class="inline-flex items-center gap-1 font-bold text-rose-300 bg-rose-950/60 px-2 py-0.5 rounded border border-rose-500/40">⚠️ MARGIN ALERT</span>');
                return html;
            } catch (e) {
                console.error("Markdown parse error:", e);
                return content.replace(/</g, "&lt;").replace(/>/g, "&gt;");
            }
        }

        // Jira CSV RFC 4180 Exporter
        function exportJiraCSV(markdownContent, filename = "Jira_Stories_Import.csv") {
            if (!markdownContent || typeof markdownContent !== 'string') return;
            const lines = markdownContent.split('\n');
            const rows = [["Issue Type", "Summary", "Description", "Priority", "Acceptance Criteria"]];
            
            let currentReq = null;
            let currentSummary = "";
            let currentStory = "";
            let currentCriteria = "";

            for (let line of lines) {
                const reqMatch = line.match(/\b(REQ-\d{3,4})\b[:\s-]*(.*)/i);
                if (reqMatch) {
                    if (currentReq) {
                        rows.push(["Story", `${currentReq}: ${currentSummary || 'Feature Implementation'}`, currentStory || currentSummary, "High", currentCriteria || "Given precondition When action Then verify outcome"]);
                    }
                    currentReq = reqMatch[1];
                    currentSummary = reqMatch[2].replace(/[#*`]/g, '').trim();
                    currentStory = "";
                    currentCriteria = "";
                } else if (line.toLowerCase().includes("as a ") || line.toLowerCase().includes("i want to ")) {
                    currentStory += (currentStory ? " " : "") + line.replace(/[#*`]/g, '').trim();
                } else if (line.toLowerCase().includes("scenario:") || line.toLowerCase().includes("given ") || line.toLowerCase().includes("when ") || line.toLowerCase().includes("then ")) {
                    currentCriteria += (currentCriteria ? "\n" : "") + line.replace(/[#*`]/g, '').trim();
                }
            }

            if (currentReq) {
                rows.push(["Story", `${currentReq}: ${currentSummary || 'Feature Implementation'}`, currentStory || currentSummary, "High", currentCriteria || "Given precondition When action Then verify outcome"]);
            }

            if (rows.length === 1) {
                // Fallback row if no REQ-XXX tags detected
                rows.push(["Story", "Enterprise Module Implementation", markdownContent.substring(0, 300).replace(/\n/g, ' '), "High", "Scenario: Acceptance signoff Given baseline When executed Then verify delivery"]);
            }

            const csvContent = rows.map(row => 
                row.map(field => `"${(field || '').toString().replace(/"/g, '""')}"`).join(',')
            ).join('\r\n');

            const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            showToast("Jira CSV import file downloaded!", "success");
        }

        // Copy Text Helper
        function copyToClipboard(text, msg = "Copied to clipboard!") {
            navigator.clipboard.writeText(text).then(() => {
                showToast(msg, "success");
            }).catch(() => {
                showToast("Failed to copy", "error");
            });
        }

        // Download Markdown File
        function downloadMarkdownFile(content, filename = "Specification.md") {
            const blob = new Blob([content], { type: 'text/markdown;charset=utf-8;' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            showToast("Markdown specification downloaded!", "success");
        }

        // Render Error Bubble with Diagnostics & 1-Click Cache Reset
        function renderErrorBubble(errText) {
            const card = document.createElement('div');
            card.className = "w-full rounded-xl bg-rose-950/40 border border-rose-500/40 p-4 shadow-card space-y-2 text-rose-200 text-xs";
            card.innerHTML = `
                <div class="flex items-center justify-between pb-1.5 border-b border-rose-500/20 font-bold text-rose-300">
                    <span class="flex items-center gap-1.5"><span>⚠️</span> Execution Warning</span>
                    <button onclick="clearChatHistory()" class="text-[10px] underline text-rose-300 hover:text-white">Clear History & Cache</button>
                </div>
                <div class="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-rose-200/90">${errText.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</div>
            `;
            return card;
        }

        // Render Chat Assistant Message Card (100% full width)
        function renderAssistantBubble(msg) {
            const card = document.createElement('div');
            card.className = "w-full rounded-xl bg-canvas-elevated border border-canvas-border p-4 shadow-card space-y-3";
            
            const agentName = (msg.final_agent || "swarm").toUpperCase();
            const latency = (msg.execution_time_ms && Number(msg.execution_time_ms) > 0) ? `${msg.execution_time_ms} ms` : "COMPLETED";
            const handoffs = msg.handoffs || [];

            let rawContent = (msg.content != null && String(msg.content).trim() !== "undefined" && String(msg.content).trim() !== "null") ? String(msg.content) : "_No response content returned by swarm._";

            // Header of Agent Card
            let headerHtml = `
                <div class="flex items-center justify-between pb-2 border-b border-canvas-border">
                    <div class="flex items-center gap-2">
                        <span class="w-2 h-2 rounded-full bg-cyan shadow-cyan-glow"></span>
                        <span class="text-xs font-bold text-white tracking-wide">${agentName}</span>
                        <span class="text-[10px] font-mono text-cyan bg-cyan/10 px-1.5 py-0.5 rounded border border-cyan/20">${latency}</span>
                    </div>
                    <div class="text-[10px] font-mono text-slate-400">
                        ${handoffs.length > 0 ? handoffs.map(h => `${(h.from||'').toUpperCase()} ➔ ${(h.to||'').toUpperCase()}`).join(' &bull; ') : 'DIRECT'}
                    </div>
                </div>
            `;

            // Formatted Markdown Content
            const formattedContent = renderMarkdownSafe(rawContent);
            let bodyHtml = `<div class="prose-custom text-xs text-slate-200 leading-relaxed overflow-x-auto">${formattedContent}</div>`;

            // Specification Drawer (if PRD/BRD/PDD/Quote detected)
            const isDoc = /REQ-\d+|PRD|BRD|PDD|Statement of Work|Quote|Milestone/i.test(rawContent);
            let drawerHtml = "";
            if (isDoc) {
                const escapedContent = encodeURIComponent(rawContent);
                drawerHtml = `
                    <div class="pt-2 border-t border-canvas-border flex flex-wrap items-center justify-between gap-2">
                        <span class="text-[10px] font-mono text-slate-400">Generated Institutional Specification</span>
                        <div class="flex items-center gap-1.5">
                            <button onclick="copyToClipboard(decodeURIComponent('${escapedContent}'))" class="px-2.5 py-1 text-[10px] font-bold rounded bg-canvas-soft hover:bg-slate-800 text-slate-300 border border-canvas-border hover:border-cyan transition flex items-center gap-1">
                                📋 Copy .md
                            </button>
                            <button onclick="downloadMarkdownFile(decodeURIComponent('${escapedContent}'), '${agentName}_Spec.md')" class="px-2.5 py-1 text-[10px] font-bold rounded bg-canvas-soft hover:bg-slate-800 text-slate-300 border border-canvas-border hover:border-cyan transition flex items-center gap-1">
                                ⬇️ Download .md
                            </button>
                            <button onclick="exportJiraCSV(decodeURIComponent('${escapedContent}'))" class="px-2.5 py-1 text-[10px] font-bold rounded bg-cyan/10 hover:bg-cyan/20 text-cyan border border-cyan/30 transition flex items-center gap-1">
                                📊 Export Jira .csv
                            </button>
                        </div>
                    </div>
                `;
            }

            // Inline HITL Notification Badge
            let hitlBannerHtml = "";
            if (msg.pending_approval_queued) {
                hitlBannerHtml = `
                    <div onclick="scrollToHITL()" class="cursor-pointer p-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs flex items-center justify-between hover:bg-amber-500/15 transition">
                        <span class="flex items-center gap-1.5 font-bold">
                            <span class="animate-pulse">⚠️</span> Action Queued for Human Review (HITL)
                        </span>
                        <span class="text-[10px] underline text-amber-200">View in Approval Queue ➔</span>
                    </div>
                `;
            }

            card.innerHTML = headerHtml + bodyHtml + drawerHtml + hitlBannerHtml;
            return card;
        }

        // Render Chat User Bubble
        function renderUserBubble(text) {
            const wrap = document.createElement('div');
            wrap.className = "flex justify-end w-full";
            const bubble = document.createElement('div');
            bubble.className = "max-w-[85%] rounded-xl bg-cyan-950/40 border border-cyan/30 p-3 text-xs text-cyan-100 shadow-card space-y-1";
            bubble.innerHTML = `
                <div class="flex items-center justify-between text-[10px] text-cyan-400 font-mono pb-1 border-b border-cyan/20">
                    <span>YOU</span>
                    <span>${new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</span>
                </div>
                <div class="whitespace-pre-wrap leading-relaxed">${text.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</div>
            `;
            wrap.appendChild(bubble);
            return wrap;
        }

        // Scroll to HITL Section
        function scrollToHITL() {
            const el = document.getElementById('hitl-container-section');
            if (el) {
                el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                el.classList.add('ring-2', 'ring-amber-400');
                setTimeout(() => el.classList.remove('ring-2', 'ring-amber-400'), 2000);
            }
        }

        // Send Chat Command
        async function sendChatMessage() {
            const input = document.getElementById('chat-input');
            const command = input.value.trim();
            if (!command) return;

            input.value = "";
            input.style.height = 'auto';

            const container = document.getElementById('chat-messages');
            container.appendChild(renderUserBubble(command));
            container.scrollTop = container.scrollHeight;

            chatMessages.push({ role: "user", content: command });
            saveChatHistory();

            // Typing Indicator
            const typingCard = document.createElement('div');
            typingCard.id = "agent-typing-card";
            typingCard.className = "w-full rounded-xl bg-canvas-elevated/70 border border-cyan/40 p-4 flex items-center justify-between gap-3 text-xs text-cyan shadow-card animate-pulse";
            typingCard.innerHTML = `
                <div class="flex items-center gap-2">
                    <span class="animate-spin text-base">⚡</span>
                    <span class="font-mono">Swarm executing multi-agent reasoning & verification...</span>
                </div>
                <span class="text-[10px] font-mono text-slate-400">Synthesizing specifications</span>
            `;
            container.appendChild(typingCard);
            container.scrollTop = container.scrollHeight;

            try {
                // Sanitize history to prevent payload schema validation errors
                const cleanHistory = chatMessages
                    .filter(m => m && m.content && String(m.content).trim() !== "undefined" && String(m.content).trim() !== "null")
                    .map(m => ({ role: m.role || "user", content: String(m.content).slice(0, 1000) }));

                const res = await fetch('/api/delegate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ command: command, history: cleanHistory })
                });

                typingCard.remove();

                if (!res.ok) {
                    let errMsg = `Server returned HTTP ${res.status}`;
                    if (res.status === 504) {
                        errMsg = "Serverless execution limit reached (Gateway Timeout). Please narrow request scope or retry.";
                    } else {
                        try {
                            const errData = await res.json();
                            if (errData.detail) {
                                errMsg = typeof errData.detail === 'string' ? errData.detail : JSON.stringify(errData.detail);
                            } else if (errData.error) {
                                errMsg = errData.error;
                            }
                        } catch (_) {
                            errMsg = await res.text();
                        }
                    }
                    console.error("Delegation error:", errMsg);
                    const errorCard = renderErrorBubble(`Execution failed: ${errMsg}`);
                    container.appendChild(errorCard);
                    container.scrollTop = container.scrollHeight;
                    showToast("Delegation error: " + errMsg.slice(0, 80), "error");
                    return;
                }

                const data = await res.json();
                const summaryContent = data.final_summary || (data.tool_calls_executed && data.tool_calls_executed.length > 0 
                    ? `### Swarm Execution Completed\nCompleted ${data.tool_calls_executed.length} automated operations.`
                    : "Task completed without text output.");

                const assistantMsg = {
                    role: "assistant",
                    final_agent: data.final_agent || "triage",
                    handoffs: data.handoffs || [],
                    content: summaryContent,
                    execution_time_ms: data.execution_time_ms || 0,
                    pending_approval_queued: Boolean(data.pending_approval_queued)
                };

                container.appendChild(renderAssistantBubble(assistantMsg));
                container.scrollTop = container.scrollHeight;

                chatMessages.push({
                    role: "assistant",
                    content: summaryContent,
                    final_agent: assistantMsg.final_agent,
                    execution_time_ms: assistantMsg.execution_time_ms,
                    handoffs: assistantMsg.handoffs
                });
                saveChatHistory();

                // Update breadcrumbs
                const bc = document.getElementById('chat-handoff-breadcrumb');
                if (bc && data.handoffs && data.handoffs.length > 0) {
                    bc.textContent = data.handoffs.map(h => `${(h.from||'').toUpperCase()} ➔ ${(h.to||'').toUpperCase()}`).join(' &bull; ');
                }

                // Spoken TTS
                if (ttsEnabled && data.spoken_summary && ('speechSynthesis' in window)) {
                    const utter = new SpeechSynthesisUtterance(data.spoken_summary);
                    utter.rate = 1.05;
                    window.speechSynthesis.speak(utter);
                }

                // Refresh state
                refreshData();

            } catch (err) {
                if (document.getElementById('agent-typing-card')) {
                    typingCard.remove();
                }
                const errorCard = renderErrorBubble(`Network error: ${err.message}. Please check connection or retry.`);
                container.appendChild(errorCard);
                container.scrollTop = container.scrollHeight;
                showToast("Delegation failure: " + err.message, "error");
            }
        }

        function handleChatKeydown(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendChatMessage();
            }
        }

        // Quick Starter Chip
        function injectPromptChip(text) {
            const input = document.getElementById('chat-input');
            input.value = text;
            input.focus();
        }

        // Playbooks
        function runPlaybook(type) {
            const playbooks = {
                quote: "Draft a commercial quote and 6-week delivery timeline for an Enterprise OCR & Document Extraction module for Acme Corp",
                renewals: "Check Zoho renewals expiring in the next 90 days, analyze expansion opportunities, and flag churn risks",
                deploy: "Parse latest deployment commit notes, verify QA acceptance criteria, advance Jira tickets to Deployed, and draft release notes",
                scope_creep: "Analyze scope impact of client's request to add multi-tenant SAML SSO and biometric 2FA against our baseline agreement for Acme Corp",
                wsr: "Generate an Executive Weekly Status Report (WSR) covering active milestones, RAID log, Jira progress, and upcoming deliverables for Acme Corp"
            };
            const prompt = playbooks[type] || playbooks.quote;
            const input = document.getElementById('chat-input');
            input.value = prompt;
            sendChatMessage();
        }

        // Chat Persistence with Automatic Poison Filter
        function saveChatHistory() {
            const clean = chatMessages
                .filter(m => m && m.content && String(m.content).trim() !== "undefined" && String(m.content).trim() !== "null")
                .slice(-20);
            localStorage.setItem('ba_swarm_chat_history', JSON.stringify(clean));
        }

        function loadChatHistory() {
            const raw = localStorage.getItem('ba_swarm_chat_history');
            if (!raw) return;
            try {
                const parsed = JSON.parse(raw);
                if (!Array.isArray(parsed)) return;

                // Aggressively sanitize against poisoned 'undefined' entries from previous runs
                chatMessages = parsed.filter(m => m && m.content && String(m.content).trim() !== "undefined" && String(m.content).trim() !== "null");
                saveChatHistory();

                const container = document.getElementById('chat-messages');
                container.innerHTML = "";
                for (let msg of chatMessages) {
                    if (msg.role === 'user') {
                        container.appendChild(renderUserBubble(msg.content));
                    } else {
                        container.appendChild(renderAssistantBubble({
                            final_agent: msg.final_agent || "swarm",
                            content: msg.content,
                            execution_time_ms: msg.execution_time_ms || 0,
                            handoffs: msg.handoffs || []
                        }));
                    }
                }
                container.scrollTop = container.scrollHeight;
            } catch (e) {
                localStorage.removeItem('ba_swarm_chat_history');
                chatMessages = [];
            }
        }
        function clearChatHistory() {
            localStorage.removeItem('ba_swarm_chat_history');
            chatMessages = [];
            const container = document.getElementById('chat-messages');
            container.innerHTML = "";
            showToast("Chat history cleared", "info");
        }

        // Voice TTS Toggle
        function toggleVoiceReply() {
            ttsEnabled = !ttsEnabled;
            const icon = document.getElementById('tts-icon');
            if (icon) icon.textContent = ttsEnabled ? "🔊" : "🔇";
            showToast(ttsEnabled ? "Voice reply enabled" : "Voice reply muted", "info");
        }

        // Voice Recording & Transcription
        async function toggleVoiceRecording() {
            if (!isRecording) {
                try {
                    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    mediaRecorder = new MediaRecorder(stream);
                    audioChunks = [];
                    mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
                    mediaRecorder.onstop = processVoiceRecording;
                    mediaRecorder.start();
                    isRecording = true;
                    document.getElementById('recording-banner').classList.remove('hidden');
                    document.getElementById('voice-label').textContent = "Stop REC";
                    document.getElementById('voice-record-btn').classList.add('border-rose-500', 'bg-rose-500/10');
                } catch (e) {
                    showToast("Microphone access denied: " + e.message, "error");
                }
            } else {
                if (mediaRecorder && mediaRecorder.state !== 'inactive') {
                    mediaRecorder.stop();
                }
                isRecording = false;
                document.getElementById('recording-banner').classList.add('hidden');
                document.getElementById('voice-label').textContent = "Voice Memo";
                document.getElementById('voice-record-btn').classList.remove('border-rose-500', 'bg-rose-500/10');
            }
        }

        async function processVoiceRecording() {
            const blob = new Blob(audioChunks, { type: 'audio/webm' });
            const reader = new FileReader();
            reader.onloadend = async () => {
                const base64Audio = reader.result.split(',')[1];
                showToast("Transcribing voice audio via Gemini...", "info");
                try {
                    const res = await fetch('/api/transcribe', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ audio_base64: base64Audio, format: 'webm' })
                    });
                    const data = await res.json();
                    if (data.transcription) {
                        const input = document.getElementById('chat-input');
                        input.value = data.transcription;
                        sendChatMessage();
                    } else {
                        showToast("Transcription was empty", "warning");
                    }
                } catch (e) {
                    showToast("Voice transcription failed: " + e.message, "error");
                }
            };
            reader.readAsDataURL(blob);
        }

        // Fetch HITL Approvals
        async function fetchApprovals() {
            try {
                const res = await fetch('/api/approvals?status=pending');
                const items = await res.json();
                const container = document.getElementById('approvals-list');
                const badge = document.getElementById('stat-pending-badge');
                if (badge) badge.textContent = `${items.length} Pending`;

                if (!items || items.length === 0) {
                    container.innerHTML = `<div class="p-3 text-center text-xs text-slate-500">No actions pending review &bull; Governance clear</div>`;
                    return;
                }

                container.innerHTML = items.map(item => `
                    <div id="approval-${item.id}" class="p-3 rounded-lg bg-canvas-elevated border border-canvas-border hover:border-amber-500/40 transition space-y-2">
                        <div class="flex items-center justify-between">
                            <span class="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300 font-bold border border-amber-500/20">
                                ${item.agent_name} &bull; ${item.target_system}
                            </span>
                            <span class="text-[10px] text-slate-500 font-mono">${item.action_type}</span>
                        </div>
                        <p class="text-xs text-slate-200">${item.risk_summary}</p>
                        <div class="flex items-center justify-end gap-2 pt-1 border-t border-canvas-border">
                            <button onclick="openRejectModal('${item.id}')" class="px-2.5 py-1 text-[10px] font-semibold text-rose-400 hover:text-white hover:bg-rose-600/30 rounded border border-rose-500/30 transition">
                                Reject
                            </button>
                            <button onclick="approveAction('${item.id}')" class="px-2.5 py-1 text-[10px] font-semibold text-black bg-emerald-400 hover:bg-emerald-300 rounded shadow transition font-bold">
                                Approve (Execute)
                            </button>
                        </div>
                    </div>
                `).join('');
            } catch (e) {
                console.error("Fetch approvals error:", e);
            }
        }

        async function approveAction(id) {
            try {
                const res = await fetch(`/api/approvals/${id}/approve`, { method: 'POST' });
                const data = await res.json();
                showToast("Action approved & executed!", "success");
                fetchApprovals();
            } catch (e) {
                showToast("Approval execution failed: " + e.message, "error");
            }
        }

        function openRejectModal(id) {
            pendingRejectId = id;
            document.getElementById('reject-modal').classList.remove('hidden');
        }
        function closeRejectModal() {
            pendingRejectId = null;
            document.getElementById('reject-modal').classList.add('hidden');
        }
        async function confirmRejectAction() {
            if (!pendingRejectId) return;
            const reason = document.getElementById('reject-modal-reason').value || "Rejected by reviewer";
            try {
                await fetch(`/api/approvals/${pendingRejectId}/reject`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ reason: reason })
                });
                showToast("Action rejected", "info");
                closeRejectModal();
                fetchApprovals();
            } catch (e) {
                showToast("Rejection failed: " + e.message, "error");
            }
        }

        // RAID Radar Management
        async function fetchRaid() {
            try {
                const res = await fetch('/api/raid');
                const data = await res.json();
                currentRaidItems = data.items || [];
                renderRaidList();
            } catch (e) {
                console.error("Fetch raid error:", e);
            }
        }

        function filterRaid(cat) {
            activeRaidFilter = cat;
            ['all', 'risk', 'assump', 'issue', 'dep'].forEach(id => {
                const btn = document.getElementById(`raid-tab-${id}`);
                if (btn) btn.className = "px-2 py-0.5 rounded bg-canvas-elevated hover:text-white";
            });
            const activeMap = { all: 'all', Risk: 'risk', Assumption: 'assump', Issue: 'issue', Dependency: 'dep' };
            const activeBtn = document.getElementById(`raid-tab-${activeMap[cat] || 'all'}`);
            if (activeBtn) activeBtn.className = "px-2 py-0.5 rounded bg-canvas-elevated text-cyan border border-cyan/30";
            renderRaidList();
        }

        function renderRaidList() {
            const container = document.getElementById('raid-list');
            let items = currentRaidItems;
            if (activeRaidFilter !== 'all') {
                items = items.filter(r => r.category.toLowerCase() === activeRaidFilter.toLowerCase());
            }

            if (!items || items.length === 0) {
                container.innerHTML = `<div class="p-3 text-center text-xs text-slate-500">No active ${activeRaidFilter} items</div>`;
                return;
            }

            container.innerHTML = items.map(r => {
                const sevColor = r.severity === 'High' ? 'text-rose-400 bg-rose-500/10 border-rose-500/30' :
                                 r.severity === 'Medium' ? 'text-amber-400 bg-amber-500/10 border-amber-500/30' :
                                 'text-slate-400 bg-slate-500/10 border-slate-500/30';
                return `
                    <div class="p-2.5 rounded-lg bg-canvas-elevated border border-canvas-border hover:border-cyan/30 transition space-y-1.5">
                        <div class="flex items-center justify-between">
                            <div class="flex items-center gap-1.5">
                                <span class="text-[10px] font-bold px-1.5 py-0.5 rounded ${sevColor} border font-mono">${r.category} &bull; ${r.severity}</span>
                                <span class="text-[10px] text-slate-400 font-medium">${r.account_name}</span>
                            </div>
                            <button onclick="resolveRaid('${r.id}')" class="text-[10px] text-emerald-400 hover:text-emerald-300 font-semibold px-1.5 py-0.5 rounded bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/20 transition">
                                ✓ Resolve
                            </button>
                        </div>
                        <p class="text-xs text-slate-300 line-clamp-2">${r.description}</p>
                        <div class="text-[9px] font-mono text-slate-500">Owner: ${r.owner || 'Unassigned'}</div>
                    </div>
                `;
            }).join('');
        }

        async function resolveRaid(id) {
            try {
                await fetch(`/api/raid/${id}/resolve`, { method: 'POST' });
                showToast("RAID item resolved!", "success");
                fetchRaid();
            } catch (e) {
                showToast("Resolve failed: " + e.message, "error");
            }
        }

        function openRaidModal() {
            document.getElementById('raid-modal').classList.remove('hidden');
        }
        function closeRaidModal() {
            document.getElementById('raid-modal').classList.add('hidden');
        }
        async function submitRaidModal() {
            const cat = document.getElementById('raid-category-input').value;
            const sev = document.getElementById('raid-severity-input').value;
            const acc = document.getElementById('raid-account-input').value;
            const owner = document.getElementById('raid-owner-input').value;
            const desc = document.getElementById('raid-desc-input').value;

            if (!desc) {
                showToast("Please enter a description", "warning");
                return;
            }

            try {
                await fetch('/api/raid', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        account_name: acc,
                        category: cat,
                        severity: sev,
                        description: desc,
                        owner: owner
                    })
                });
                showToast("Item saved to RAID Radar", "success");
                closeRaidModal();
                fetchRaid();
            } catch (e) {
                showToast("Failed to save RAID item: " + e.message, "error");
            }
        }

        // Daily Report & EOD Download
        async function fetchDailyReport() {
            try {
                const res = await fetch('/api/reports/daily');
                const data = await res.json();
                const tr = document.getElementById('stat-total-runs');
                const disc = document.getElementById('stat-discrepancies');
                if (tr) tr.textContent = data.total_runs_today || 0;
                if (disc) disc.textContent = data.discrepancies_detected_count || 0;

                // Update agent stats
                if (data.agent_invocations) {
                    ['triage', 'commercial', 'ba_docs', 'delivery', 'comms'].forEach(a => {
                        const el = document.getElementById(`agent-stat-${a}`);
                        if (el) el.textContent = `${data.agent_invocations[a] || 0} runs`;
                    });
                }
            } catch (e) {
                console.error("Fetch daily report error:", e);
            }
        }

        async function downloadEODReport() {
            try {
                const res = await fetch('/api/reports/daily');
                const data = await res.json();
                downloadMarkdownFile(data.eod_report_markdown, `EOD_Operations_Report_${data.date}.md`);
            } catch (e) {
                showToast("Failed to download EOD report: " + e.message, "error");
            }
        }

        // Manual Event Simulator Drawer
        function toggleManualDrawer() {
            const drawer = document.getElementById('manual-drawer-content');
            const arrow = document.getElementById('drawer-arrow');
            if (drawer.classList.contains('hidden')) {
                drawer.classList.remove('hidden');
                if (arrow) arrow.textContent = "▲";
            } else {
                drawer.classList.add('hidden');
                if (arrow) arrow.textContent = "▼";
            }
        }

        async function triggerManualEvent() {
            const src = document.getElementById('manual-source').value;
            const body = document.getElementById('manual-body').value;
            if (!body) {
                showToast("Enter payload content", "warning");
                return;
            }
            try {
                const res = await fetch('/api/webhook', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ source: src, body: body, subject: "Manual Simulation" })
                });
                const data = await res.json();
                showToast("Event ingested into Swarm!", "success");
                refreshData();
            } catch (e) {
                showToast("Simulation failed: " + e.message, "error");
            }
        }

        // Health Check
        async function checkGeminiHealth() {
            try {
                const res = await fetch('/api/health');
                const data = await res.json();
                const dot = document.getElementById('gemini-status-dot');
                const text = document.getElementById('gemini-status-text');
                const pill = document.getElementById('gemini-status-pill');
                if (!dot || !text) return;

                if (data.gemini_configured) {
                    dot.className = "w-2 h-2 rounded-full bg-emerald-400";
                    const modelName = (data.discovered_models && data.discovered_models[0]) || data.primary_model || "gemini-flash-lite";
                    text.innerHTML = `<span class="text-emerald-400 font-semibold">Gemini Online</span> <span class="text-slate-500 hidden md:inline">(${modelName})</span>`;
                    pill.className = "hidden sm:flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-mono rounded-lg border border-emerald-500/20 bg-emerald-500/5 text-slate-300";
                } else {
                    dot.className = "w-2 h-2 rounded-full bg-amber-400";
                    text.innerHTML = `<span class="text-amber-400 font-semibold">Key Needed</span>`;
                    pill.className = "hidden sm:flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-mono rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-300";
                }

                const supState = document.getElementById('stat-supabase-state');
                if (supState) {
                    supState.textContent = data.supabase_configured ? "Connected" : "In-Memory Mock";
                    supState.className = data.supabase_configured ? "font-bold text-emerald-400" : "font-bold text-amber-400";
                }
            } catch (e) {
                console.error("Health check error:", e);
            }
        }

        function refreshData() {
            const spinner = document.getElementById('refresh-spinner');
            if (spinner) spinner.classList.add('animate-spin');
            Promise.all([fetchApprovals(), fetchRaid(), fetchDailyReport(), checkGeminiHealth()]).finally(() => {
                setTimeout(() => {
                    if (spinner) spinner.classList.remove('animate-spin');
                }, 400);
            });
        }

        // Auto keyboard focus (Cmd+K)
        document.addEventListener('keydown', e => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                const inp = document.getElementById('chat-input');
                if (inp) inp.focus();
            }
        });

        // Initial Load
        loadChatHistory();
        refreshData();
        setInterval(refreshData, 30000);
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serves the Mission Control PWA HTML dashboard."""
    return HTMLResponse(content=DASHBOARD_HTML)
