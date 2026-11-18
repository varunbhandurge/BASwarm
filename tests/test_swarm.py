"""
Unit and Integration Tests for BA Account Swarm:
1. Multi-hop agent handoff: Triage -> Commercial -> BA Docs -> Comms
2. Parallel BRD/PRD/PDD section generation using asyncio.gather on gemini-2.5-flash
3. Deployment note parsing -> Jira ticket status transition
4. Human-in-the-Loop (HITL) approval queueing and execution flow (Zoho & WhatsApp)
5. Voice audio transcription & Swarm delegation endpoint via Gemini
6. Meta WhatsApp webhook challenge verification and message receiver
7. PWA manifest & Mission Control dashboard endpoints
8. Interactive Chat Delegation Console endpoint with context history & TTS spoken summary
9. Google Gemini default configuration (gemini-2.5-pro & gemini-2.5-flash)
10. Missing Gemini API key graceful warning guard
11. Native Google Gemini audio transcription via generateContent REST
"""

import sys
import os
import json
import base64
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

# Ensure root directory is on python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from swarm.engine import (
    run_swarm,
    generate_parallel_sections,
    transcribe_audio_bytes,
    get_llm_client,
    get_gemini_api_key,
    is_gemini_key_configured,
    GEMINI_OPENAI_BASE_URL,
    GEMINI_NATIVE_API_URL,
    MISSING_KEY_WARNING
)
from swarm.tools import (
    queue_for_approval,
    execute_approved_action,
    whatsapp_send_message,
    jira_transition_issue,
    zoho_update_deal,
    get_env,
    calculate_project_quote,
    analyze_scope_impact,
    upsert_raid_item,
    get_active_raid_items
)
from api.index import app


# ==============================================================================
# MOCK LLM OBJECTS
# ==============================================================================
class MockFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class MockToolCall:
    def __init__(self, call_id: str, name: str, arguments: dict):
        self.id = call_id
        self.function = MockFunction(name, json.dumps(arguments))


class MockMessage:
    def __init__(self, content: str = "", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class MockChoice:
    def __init__(self, message: MockMessage):
        self.message = message


class MockCompletionResponse:
    def __init__(self, message: MockMessage):
        self.choices = [MockChoice(message)]


# ==============================================================================
# TEST 1: MULTI-HOP SWARM HANDOFF (Triage -> Commercial -> BA Docs -> Comms)
# ==============================================================================
def test_multi_hop_swarm_handoff():
    """Verify clean agent-to-agent transfers while preserving history and audit trails."""
    async def _run():
        call1 = MockCompletionResponse(
            MockMessage(
                content="Classified as commercial proposal and pricing inquiry.",
                tool_calls=[
                    MockToolCall("call_1", "transfer_to_commercial", {"reason": "Client requested SOW renewal and revised pricing"})
                ]
            )
        )
        call2 = MockCompletionResponse(
            MockMessage(
                content="Commercial terms analyzed. Scope specifications needed.",
                tool_calls=[
                    MockToolCall("call_2", "transfer_to_ba_docs", {"reason": "PRD required for newly requested features"})
                ]
            )
        )
        call3 = MockCompletionResponse(
            MockMessage(
                content="Drafted PRD structure. Routing to comms for meeting scheduling.",
                tool_calls=[
                    MockToolCall("call_3", "transfer_to_comms", {"reason": "Draft alignment meeting with client stakeholders"})
                ]
            )
        )
        call4 = MockCompletionResponse(
            MockMessage(
                content="### Minutes of Meeting (MoM)\n- Decisions: Agreed on Gold SLA.\n- Action Items: Schedule review call on Friday."
            )
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[call1, call2, call3, call4])

        with patch("swarm.engine.get_llm_client", return_value=mock_client), \
             patch("swarm.engine.log_swarm_run", new_callable=AsyncMock) as mock_log:

            result = await run_swarm(
                initial_event="Client Acme Corp requested renewal terms and new feature PRD.",
                starting_agent="triage",
                max_turns=5
            )

            assert result["status"] == "success"
            assert result["final_agent"] == "comms"
            assert result["agent_history"] == ["triage", "commercial", "ba_docs", "comms"]
            assert len(result["handoffs"]) == 3
            assert result["handoffs"][0]["from"] == "triage"
            assert result["handoffs"][0]["to"] == "commercial"
            assert result["handoffs"][1]["from"] == "commercial"
            assert result["handoffs"][1]["to"] == "ba_docs"
            assert result["handoffs"][2]["from"] == "ba_docs"
            assert result["handoffs"][2]["to"] == "comms"
            assert "Minutes of Meeting" in result["final_summary"]
            mock_log.assert_called_once()

    asyncio.run(_run())


# ==============================================================================
# TEST 2: PARALLEL SECTION GENERATION (asyncio.gather)
# ==============================================================================
def test_parallel_section_generation():
    """Verify BRD/PRD/PDD sections are generated concurrently via asyncio.gather."""
    async def _run():
        def mock_section_creator(*args, **kwargs):
            messages = kwargs.get("messages", [])
            user_text = messages[1]["content"] if len(messages) > 1 else ""
            if "Functional Requirements" in user_text:
                return MockCompletionResponse(MockMessage("REQ-001: User SSO Authentication (Must Have)"))
            elif "User Stories" in user_text:
                return MockCompletionResponse(MockMessage("As an Admin, I want SSO, So that security is centralized."))
            return MockCompletionResponse(MockMessage("Section Content Placeholder"))

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=mock_section_creator)

        with patch("swarm.engine.get_llm_client", return_value=mock_client):
            sections = [
                "1. Problem Statement",
                "2. Functional Requirements",
                "3. User Stories"
            ]
            results = await generate_parallel_sections(
                doc_type="prd",
                context="SSO OAuth2 authentication with SAML 2.0 support",
                sections=sections
            )

            assert len(results) == 3
            assert "1. Problem Statement" in results
            assert "2. Functional Requirements" in results
            assert "3. User Stories" in results
            assert "REQ-001" in results["2. Functional Requirements"]
            assert "As an Admin" in results["3. User Stories"]
            assert mock_client.chat.completions.create.call_count == 3

    asyncio.run(_run())


# ==============================================================================
# TEST 3: DEPLOYMENT NOTE PARSING -> JIRA TICKET TRANSITION
# ==============================================================================
def test_deployment_note_to_jira_transition():
    """Verify release notes parse ticket keys and trigger Jira transition."""
    async def _run():
        call1 = MockCompletionResponse(
            MockMessage(
                content="Deployment confirmed. Advancing verified tickets in Jira.",
                tool_calls=[
                    MockToolCall("jira_call_1", "jira_transition_issue", {"issue_key": "PROJ-101", "transition_name_or_id": "Deployed"})
                ]
            )
        )
        call2 = MockCompletionResponse(
            MockMessage(
                content="Ticket PROJ-101 successfully transitioned to Deployed. Release notes compiled."
            )
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[call1, call2])

        with patch("swarm.engine.get_llm_client", return_value=mock_client), \
             patch("swarm.engine.log_swarm_run", new_callable=AsyncMock):

            event = "Production Deploy Complete: Released build v2.4.0 containing PROJ-101 (SSO Provider)."
            result = await run_swarm(
                initial_event=event,
                starting_agent="delivery",
                event_source="deployment_note"
            )

            assert result["status"] == "success"
            executed_tools = [tc["tool"] for tc in result["tool_calls_executed"]]
            assert "jira_transition_issue" in executed_tools
            assert result["tool_calls_executed"][0]["args"]["issue_key"] == "PROJ-101"
            assert result["tool_calls_executed"][0]["args"]["transition_name_or_id"] == "Deployed"

    asyncio.run(_run())


# ==============================================================================
# TEST 4: HITL APPROVAL QUEUEING & EXECUTION FLOW (Zoho & WhatsApp)
# ==============================================================================
def test_hitl_approval_queue_and_execute():
    """Verify high-risk actions (Zoho update and WhatsApp message) are staged and executed upon approval."""
    async def _run():
        # Case A: Zoho deal modification
        queue_res = await queue_for_approval(
            agent_name="commercial",
            action_type="zoho_update_deal",
            target_system="zoho",
            payload={"deal_id": "DEAL-5544", "data": {"Amount": 175000, "Stage": "Closed Won"}},
            risk_summary="Updates Zoho deal amount to $175,000 upon contract renewal."
        )

        assert queue_res["status"] == "queued"
        approval_id = queue_res["approval_id"]
        assert approval_id is not None

        exec_res = await execute_approved_action(approval_id)
        assert exec_res["status"] == "success"
        assert exec_res["approval_id"] == approval_id

        # Case B: WhatsApp outbound message
        wa_queue = await queue_for_approval(
            agent_name="comms",
            action_type="whatsapp_send_message",
            target_system="whatsapp",
            payload={"to": "+15551234567", "message": "Hi Mark, your SLA renewal is approved."},
            risk_summary="Sends direct WhatsApp confirmation to client."
        )
        assert wa_queue["status"] == "queued"
        wa_exec = await execute_approved_action(wa_queue["approval_id"])
        assert wa_exec["status"] == "success"

    asyncio.run(_run())


# ==============================================================================
# TEST 5: VOICE DELEGATION (GEMINI NATIVE TRANSCRIBE -> SWARM DELEGATION)
# ==============================================================================
def test_voice_transcription_and_delegation():
    """Verify raw audio bytes are transcribed via Gemini and passed into the Swarm without python-multipart."""
    import httpx

    async def _run():
        dummy_audio = b"RIFF....WAVEfmt ....data...."
        b64_audio = base64.b64encode(dummy_audio).decode("utf-8")

        mock_client = MagicMock()
        mock_swarm_turn = MockCompletionResponse(
            MockMessage(content="Voice memo processed: Commercial specialist updated SOW.")
        )
        mock_client.chat.completions.create = AsyncMock(return_value=mock_swarm_turn)

        with patch("api.index.transcribe_audio_bytes", new_callable=AsyncMock) as mock_transcribe, \
             patch("swarm.engine.get_llm_client", return_value=mock_client), \
             patch("swarm.engine.log_swarm_run", new_callable=AsyncMock):

            mock_transcribe.return_value = "Client approved SOW section 4 with 5 percent discount concession."

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                res = await client.post("/api/voice", json={
                    "audio_base64": b64_audio,
                    "format": "webm",
                    "account_name": "Acme Corp"
                })

                assert res.status_code == 200
                data = res.json()
                assert data["status"] == "success"
                assert "Client approved SOW" in data["transcription"]
                assert data["swarm_result"]["status"] == "success"

    asyncio.run(_run())


# ==============================================================================
# TEST 6: META WHATSAPP WEBHOOK VERIFICATION & INBOUND MESSAGE
# ==============================================================================
def test_whatsapp_webhook_flow():
    """Verify Meta WhatsApp Cloud API GET challenge verification and POST message receiver."""
    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. GET Challenge Verification
            with patch("api.index.get_env", return_value="test_verify_token"):
                challenge_resp = await client.get("/api/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=test_verify_token&hub.challenge=1122334455")
                assert challenge_resp.status_code == 200
                assert challenge_resp.text == "1122334455"

            # 2. POST Inbound WhatsApp Message
            mock_client = MagicMock()
            mock_swarm_turn = MockCompletionResponse(
                MockMessage(content="Processed WhatsApp message from client.")
            )
            mock_client.chat.completions.create = AsyncMock(return_value=mock_swarm_turn)

            wa_payload = {
                "object": "whatsapp_business_account",
                "entry": [{
                    "id": "WHATSAPP_BUS_ID",
                    "changes": [{
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [{
                                "from": "15559876543",
                                "id": "wamid.12345",
                                "timestamp": "1710000000",
                                "text": {"body": "Can you share the updated PRD for REQ-002?"},
                                "type": "text"
                            }]
                        },
                        "field": "messages"
                    }]
                }]
            }

            with patch("swarm.engine.get_llm_client", return_value=mock_client), \
                 patch("swarm.engine.log_swarm_run", new_callable=AsyncMock):

                post_resp = await client.post("/api/webhook/whatsapp", json=wa_payload)
                assert post_resp.status_code == 200
                res_data = post_resp.json()
                assert res_data["status"] == "processed"
                assert res_data["from"] == "15559876543"

    asyncio.run(_run())


# ==============================================================================
# TEST 7: FASTAPI WEBHOOK, PWA MANIFEST & DASHBOARD
# ==============================================================================
def test_fastapi_endpoints_and_pwa():
    """Verify HTTP endpoints for Webhook, Approvals, PWA Manifest, and Mission Control Dashboard."""
    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. HTML Dashboard
            dash_resp = await client.get("/")
            assert dash_resp.status_code == 200
            assert "BA Account Swarm" in dash_resp.text
            assert "Mission Control" in dash_resp.text

            # 2. PWA Manifest
            manifest_resp = await client.get("/manifest.json")
            assert manifest_resp.status_code == 200
            manifest = manifest_resp.json()
            assert manifest["display"] == "standalone"
            assert "BA Account Swarm" in manifest["name"]

            # 3. List Approvals
            appr_resp = await client.get("/api/approvals?status=pending")
            assert appr_resp.status_code == 200
            approvals = appr_resp.json()
            assert isinstance(approvals, list)

            # 4. Reject Approval
            if approvals:
                first_id = approvals[0]["id"]
                rej_resp = await client.post(f"/api/approvals/{first_id}/reject", json={"reason": "Testing rejection"})
                assert rej_resp.status_code == 200
                assert rej_resp.json()["status"] == "rejected"

            # 5. List Audit Logs
            logs_resp = await client.get("/api/logs")
            assert logs_resp.status_code == 200
            assert isinstance(logs_resp.json(), list)

    asyncio.run(_run())


# ==============================================================================
# TEST 8: INTERACTIVE CHAT DELEGATION CONSOLE ENDPOINT (/api/delegate)
# ==============================================================================
def test_interactive_chat_delegation_endpoint():
    """Verify conversational task delegation retains history, triggers Swarm, and yields TTS summary."""
    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            mock_client = MagicMock()
            
            # Step 1: LLM returns handoff to commercial and a staged approval tool call
            call1 = MockCompletionResponse(
                MockMessage(
                    content="Analyzed context. Staging high-risk WhatsApp notification for client review.",
                    tool_calls=[
                        MockToolCall("tool_queue_1", "queue_for_approval", {
                            "agent_name": "comms",
                            "action_type": "whatsapp_send_message",
                            "target_system": "whatsapp",
                            "payload": {"to": "+15551234567", "message": "Hi Mark, revised PRD and SOW are ready."},
                            "risk_summary": "Sends direct WhatsApp update to client executive."
                        })
                    ]
                )
            )
            # Step 2: Final response with PRD summary
            call2 = MockCompletionResponse(
                MockMessage(
                    content="### PRD Summary Specification\n- REQ-001: SSO OAuth2 Identity Provider.\n- REQ-002: Role-based access control.\nStaged client notification in approval queue."
                )
            )
            mock_client.chat.completions.create = AsyncMock(side_effect=[call1, call2])

            with patch("swarm.engine.get_llm_client", return_value=mock_client), \
                 patch("swarm.engine.log_swarm_run", new_callable=AsyncMock):

                payload = {
                    "command": "Make the timeline 6 weeks instead of 4 and notify the client on WhatsApp",
                    "history": [
                        {"role": "user", "content": "Can you prepare SOW for Acme?"},
                        {"role": "assistant", "content": "SOW drafted with 4-week timeline."}
                    ],
                    "channel": "web_chat"
                }

                res = await client.post("/api/delegate", json=payload)
                assert res.status_code == 200
                data = res.json()

                assert data["status"] == "success"
                assert "Make the timeline 6 weeks" in data["command"]
                assert data["final_agent"] in ["triage", "commercial", "comms", "ba_docs"]
                assert "PRD Summary Specification" in data["final_summary"]
                assert data["pending_approval_queued"] is True
                assert len(data["queued_approvals"]) > 0
                assert isinstance(data["spoken_summary"], str)
                assert len(data["spoken_summary"]) > 0
                assert data["execution_time_ms"] >= 0

    asyncio.run(_run())


# ==============================================================================
# TEST 9: GOOGLE GEMINI DEFAULT CONFIGURATION & RESOLUTION
# ==============================================================================
def test_gemini_defaults_and_configuration():
    """Verify default models (gemini-2.5-pro, gemini-2.5-flash) and Gemini OpenAI-compatible base URL."""
    # 1. Base URL defaults to Gemini OpenAI endpoint
    with patch.dict(os.environ, {"GEMINI_API_KEY": "", "LLM_API_KEY": "", "LLM_BASE_URL": ""}):
        client = get_llm_client()
        assert str(client.base_url).rstrip("/") == GEMINI_OPENAI_BASE_URL.rstrip("/")
        assert "generativelanguage.googleapis.com" in str(client.base_url)

    # 2. Key resolution priority: GEMINI_API_KEY over LLM_API_KEY
    with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini_key_123", "LLM_API_KEY": "llm_key_456"}):
        assert get_gemini_api_key() == "gemini_key_123"
        assert is_gemini_key_configured() is True

    # 3. Fallback to LLM_API_KEY if GEMINI_API_KEY unset
    with patch.dict(os.environ, {"GEMINI_API_KEY": "", "LLM_API_KEY": "llm_key_789"}):
        assert get_gemini_api_key() == "llm_key_789"
        assert is_gemini_key_configured() is True

    # 4. Support for GeminiAPIKey casing variant
    with patch.dict(os.environ, {"GEMINI_API_KEY": "", "LLM_API_KEY": "", "GeminiAPIKey": "gemini_camel_key"}):
        assert get_gemini_api_key() == "gemini_camel_key"
        assert is_gemini_key_configured() is True


# ==============================================================================
# TEST 10: MISSING GEMINI KEY GRACEFUL WARNING GUARD
# ==============================================================================
def test_missing_gemini_key_graceful_guard():
    """Verify that unconfigured / dummy API key returns a clean, user-friendly alert message without stack traces."""
    import httpx

    async def _run():
        with patch.dict(os.environ, {"GEMINI_API_KEY": "", "LLM_API_KEY": "mock-key", "LLM_BASE_URL": ""}):
            # Direct run_swarm call
            result = await run_swarm(
                initial_event="Client requested 15% discount on renewal.",
                starting_agent="triage"
            )
            assert result["status"] == "warning"
            assert "⚠️ Gemini API key not configured" in result["final_summary"]
            assert "Vercel → Settings → Environment Variables" in result["final_summary"]
            assert "Gemini API key is not configured" in result["spoken_summary"]

            # Web chat delegation endpoint call
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                res = await client.post("/api/delegate", json={
                    "command": "Draft PRD for authentication module",
                    "history": [],
                    "channel": "web_chat"
                })
                assert res.status_code == 200
                data = res.json()
                assert data["status"] == "warning"
                assert "⚠️ Gemini API key not configured" in data["final_summary"]
                assert "Gemini API key is not configured" in data["spoken_summary"]

    asyncio.run(_run())


# ==============================================================================
# TEST 11: NATIVE GEMINI AUDIO TRANSCRIPTION VIA GENERATECONTENT REST
# ==============================================================================
def test_gemini_native_audio_transcription_endpoint():
    """Verify direct transcription via Gemini 2.5 Flash generateContent REST endpoint."""
    import httpx

    async def _run():
        dummy_audio = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00data\x00\x00\x00\x00"
        b64_audio = base64.b64encode(dummy_audio).decode("utf-8")

        mock_gemini_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Client confirmed renewal on Gold SLA tier with P1 response under 15 minutes."}
                        ]
                    }
                }
            ]
        }

        # Mock httpx POST to Gemini generateContent
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_gemini_response

        # 1. Test engine function directly with isolated mock_post
        with patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyTestKey123"}), \
             patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:

            mock_post.return_value = mock_resp
            transcript = await transcribe_audio_bytes(dummy_audio, filename="voice.webm")
            assert "Client confirmed renewal on Gold SLA tier" in transcript

            # Verify Gemini payload structure
            assert mock_post.called
            call_kwargs = mock_post.call_args.kwargs
            json_payload = call_kwargs.get("json", {})
            parts = json_payload["contents"][0]["parts"]
            assert parts[0]["text"] == "Transcribe this voice command accurately into plain text only."
            assert parts[1]["inline_data"]["mime_type"] == "audio/webm"
            assert parts[1]["inline_data"]["data"] == b64_audio

        # 2. Test /api/transcribe HTTP endpoint with mocked transcribe_audio_bytes
        with patch("api.index.transcribe_audio_bytes", new_callable=AsyncMock) as mock_transcribe_fn:
            mock_transcribe_fn.return_value = "Client confirmed renewal on Gold SLA tier with P1 response under 15 minutes."
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                res = await client.post("/api/transcribe", json={
                    "audio_base64": b64_audio,
                    "format": "webm"
                })
                assert res.status_code == 200
                data = res.json()
                assert data["status"] == "success"
                assert "Client confirmed renewal on Gold SLA tier" in data["transcription"]

    asyncio.run(_run())


# ==============================================================================
# TEST 12: HEALTH ENDPOINT & DYNAMIC MODEL DISCOVERY
# ==============================================================================
def test_health_endpoint_and_model_discovery():
    """Verify GET /api/health returns diagnostic state and dynamic model discovery operates correctly."""
    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/api/health")
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "healthy"
            assert "gemini_configured" in data
            assert "discovered_models" in data
            assert "primary_model" in data
            assert "fast_model" in data

    asyncio.run(_run())


# ==============================================================================
# TEST 13: COMMERCIAL RATE-CARD QUOTE & MARGIN ALERT CALCULATOR
# ==============================================================================
def test_commercial_rate_card_quote_and_margin_alert():
    """Verify calculate_project_quote calculates billable hours, 30/40/30 milestone splits, and triggers margin alert > 10%."""
    async def _run():
        # 1. Standard 6-week quote with 5% discount (Authorized threshold)
        q1 = await calculate_project_quote(weeks=6, discount_pct=5.0)
        assert q1["status"] == "success"
        assert q1["duration_weeks"] == 6
        assert q1["total_billable_hours"] == 6 * 40 * 4  # 1 BA/PM, 2 Devs, 1 QA = 4 people * 40 hrs * 6 wks = 960 hrs
        assert q1["gross_fee"] > 0
        assert q1["discount_pct"] == 5.0
        assert q1["has_margin_alert"] is False
        assert q1["margin_alert"] is None
        # Verify 30% / 40% / 30% milestone sum equals net_fee
        schedule = q1["milestone_schedule"]
        total_milestones = round(schedule["milestone_1_kickoff_30pct"] + schedule["milestone_2_uat_40pct"] + schedule["milestone_3_golive_30pct"], 2)
        assert abs(total_milestones - q1["net_fee"]) <= 0.05

        # 2. Concession of 15% (Exceeds 10% threshold -> Triggers ⚠️ MARGIN ALERT)
        q2 = await calculate_project_quote(weeks=8, discount_pct=15.0)
        assert q2["has_margin_alert"] is True
        assert "⚠️ MARGIN ALERT" in q2["margin_alert"]
        assert "Commercial Director" in q2["margin_alert"]
        assert q2["discount_amount"] > 0

    asyncio.run(_run())


# ==============================================================================
# TEST 14: SCOPE CREEP & CHANGE REQUEST (CR) IMPACT DETECTOR
# ==============================================================================
def test_scope_creep_and_change_request_detector():
    """Verify analyze_scope_impact correctly classifies In-Scope vs Billable CR with effort, cost delta, and negative tests."""
    async def _run():
        # 1. Minor functional refinement (In-Scope)
        in_scope = await analyze_scope_impact(
            account_name="Acme Corp",
            new_request="Change the invoice currency display label on the UI from USD to EUR."
        )
        assert in_scope["status"] == "success"
        assert in_scope["classification"] == "In-Scope (Minor Refinement)"
        assert in_scope["is_billable_cr"] is False
        assert in_scope["effort_delta_weeks"] == 0.0
        assert in_scope["estimated_cost_delta"] == 0.0

        # 2. Architectural expansion request (Billable Change Request)
        cr = await analyze_scope_impact(
            account_name="Acme Corp",
            new_request="Please add multi-tenant SAML SSO, biometric 2FA authentication, and bulk webhook streaming to our pipeline."
        )
        assert cr["status"] == "success"
        assert cr["classification"] == "Billable Change Request (CR)"
        assert cr["is_billable_cr"] is True
        assert cr["effort_delta_weeks"] > 0
        assert cr["effort_delta_hours"] >= 80
        assert cr["estimated_cost_delta"] > 5000.0
        assert len(cr["security_compliance_requirements"]) >= 3
        assert len(cr["negative_test_edge_cases"]) >= 3
        assert any("token" in s.lower() for s in cr["security_compliance_requirements"])
        assert any("Given" in n for n in cr["negative_test_edge_cases"])

    asyncio.run(_run())


# ==============================================================================
# TEST 15: LIVE RAID RADAR TRACKING & ENDPOINTS
# ==============================================================================
def test_raid_radar_tracking_and_endpoints():
    """Verify upsert_raid_item, get_active_raid_items, and HTTP endpoints /api/raid."""
    import httpx

    async def _run():
        # 1. Direct tool call upsert
        item = await upsert_raid_item(
            account_name="Acme Corp",
            category="Risk",
            severity="High",
            description="Third-party OCR rate limiting during month-end invoicing",
            owner="Lead Architect",
            status="open"
        )
        assert item["status"] == "success"
        raid_id = item["raid_item"]["id"]
        assert raid_id.startswith("raid-")

        # 2. Query active items
        active = await get_active_raid_items(account_name="Acme Corp")
        assert active["status"] == "success"
        assert active["count"] >= 1
        found = any(r["id"] == raid_id for r in active["items"])
        assert found is True

        # 3. HTTP GET /api/raid
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            get_res = await client.get("/api/raid?account_name=Acme Corp")
            assert get_res.status_code == 200
            data = get_res.json()
            assert data["status"] == "success"

            # 4. HTTP POST /api/raid
            post_res = await client.post("/api/raid", json={
                "account_name": "Stark Industries",
                "category": "Dependency",
                "severity": "Medium",
                "description": "Waiting for Infosec SSO certificates sign-off",
                "owner": "Security PM",
                "status": "open"
            })
            assert post_res.status_code == 200
            new_raid_id = post_res.json()["raid_item"]["id"]

            # 5. HTTP POST /api/raid/{id}/resolve
            resolve_res = await client.post(f"/api/raid/{new_raid_id}/resolve")
            assert resolve_res.status_code == 200
            assert resolve_res.json()["status"] == "success"
            assert resolve_res.json()["resolved_id"] == new_raid_id

    asyncio.run(_run())


# ==============================================================================
# TEST 16: DAILY OPERATIONS REPORT & EOD MARKDOWN SYNTHESIS
# ==============================================================================
def test_daily_agent_operations_report_endpoint():
    """Verify GET /api/reports/daily aggregates executions, agent workload, and outputs institutional EOD report markdown."""
    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res = await client.get("/api/reports/daily")
            assert res.status_code == 200
            report = res.json()
            assert report["status"] == "success"
            assert "date" in report
            assert "total_runs_today" in report
            assert "agent_invocations" in report
            invocations = report["agent_invocations"]
            for agent in ["triage", "commercial", "ba_docs", "delivery", "comms"]:
                assert agent in invocations
            assert "hitl_summary" in report
            assert "eod_report_markdown" in report
            md = report["eod_report_markdown"]
            assert "# Executive End-of-Day (EOD) Operations Report" in md
            assert "Agent Workload Distribution" in md
            assert "TRIAGE" in md
            assert "COMMERCIAL" in md
            assert "BA_DOCS" in md
            assert "DELIVERY" in md
            assert "COMMS" in md
            assert "Active Project RAID Log" in md

    asyncio.run(_run())


# ==============================================================================
# TEST 17: CORRUPTED CHAT HISTORY RESILIENCE & SUMMARY SYNTHESIS FALLBACK
# ==============================================================================
def test_corrupted_history_resilience_and_synthesis_fallback():
    """Verify POST /api/delegate handles malformed history payloads (null content, lists, extra fields) and synthesizes clean summaries."""
    import httpx

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            mock_client = MagicMock()
            # Agent outputs empty content on final turn but has executed tool calls
            call = MockCompletionResponse(
                MockMessage(
                    content="",
                    tool_calls=[
                        MockToolCall("tool_jira_1", "jira_search_issues", {"jql": "project = NOVA"})
                    ]
                )
            )
            mock_client.chat.completions.create = AsyncMock(return_value=call)

            with patch("swarm.engine.get_llm_client", return_value=mock_client), \
                 patch("swarm.engine.log_swarm_run", new_callable=AsyncMock):

                # Payload containing corrupted / malformed history: null content, integer, list, undefined string
                corrupted_payload = {
                    "command": "Summarize NovaPay open tickets",
                    "history": [
                        {"role": "user", "content": "Hello"},
                        {"role": "assistant", "content": None, "handoffs": [], "execution_time_ms": 240},
                        {"role": "assistant", "content": "undefined"},
                        {"role": "assistant", "content": 12345}
                    ]
                }

                res = await client.post("/api/delegate", json=corrupted_payload)
                assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
                data = res.json()

                assert data["status"] in ["success", "warning"]
                assert data["final_summary"] is not None
                assert data["final_summary"] != "undefined"
                assert len(data["final_summary"]) > 0
                assert data["final_agent"] is not None

    asyncio.run(_run())

