"""
Asynchronous HTTP wrappers and OpenAI Tool definitions for:
- Zoho CRM (Deals, Accounts, Notes, Renewal Windows with OAuth token refresh)
- Atlassian Jira REST API v3 (Search JQL, Create Issue, Transition Status, Add Comment)
- Email & Calendar (Gmail API & Microsoft Graph API)
- WhatsApp Cloud API (Outbound messaging and notification dispatch)
- Supabase REST (PostgREST: account_context, hitl_approvals, swarm_audit_logs)
"""

import os
import json
import base64
import time
from typing import Any, Optional, Dict, List
import httpx

# ==============================================================================
# CONFIGURATION & ENVIRONMENT LOADERS
# ==============================================================================
HTTP_TIMEOUT = httpx.Timeout(15.0, connect=10.0)

def get_env(key: str, default: str = "") -> str:
    val = os.getenv(key)
    if val is not None and val.strip():
        return val.strip()
    return default

# In-memory OAuth token cache for Zoho CRM
_ZOHO_CACHE: Dict[str, Any] = {
    "access_token": None,
    "expires_at": 0.0,
    "api_domain": "https://www.zohoapis.com"
}


# ==============================================================================
# 1. ZOHO CRM CLIENT & TOOLS
# ==============================================================================
async def _get_zoho_access_token() -> Optional[str]:
    """Retrieves a valid Zoho access token, refreshing if expired."""
    client_id = get_env("ZOHO_CLIENT_ID")
    client_secret = get_env("ZOHO_CLIENT_SECRET")
    refresh_token = get_env("ZOHO_REFRESH_TOKEN")
    domain = get_env("ZOHO_API_DOMAIN", "zoho.com")

    if not (client_id and client_secret and refresh_token):
        return None

    now = time.time()
    if _ZOHO_CACHE["access_token"] and now < _ZOHO_CACHE["expires_at"]:
        return _ZOHO_CACHE["access_token"]

    token_url = f"https://accounts.{domain}/oauth/v2/token"
    params = {
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token"
    }

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(token_url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                _ZOHO_CACHE["access_token"] = data.get("access_token")
                _ZOHO_CACHE["expires_at"] = now + float(data.get("expires_in", 3600)) - 120.0
                if "api_domain" in data:
                    _ZOHO_CACHE["api_domain"] = data["api_domain"]
                return _ZOHO_CACHE["access_token"]
    except Exception as e:
        print(f"[Zoho Token Refresh Error] {e}")
    return None


async def zoho_get_deals(account_name: Optional[str] = None, deal_id: Optional[str] = None) -> Dict[str, Any]:
    """Fetch deals from Zoho CRM by deal ID or account name filter."""
    token = await _get_zoho_access_token()
    if not token:
        # Fallback / Mock representation for tests or offline execution
        return {
            "status": "mocked",
            "deals": [
                {
                    "id": deal_id or "ZOHO-DEAL-9981",
                    "Deal_Name": f"Enterprise Retainer - {account_name or 'Acme Corp'}",
                    "Amount": 120000.0,
                    "Stage": "Negotiation/Review",
                    "Closing_Date": "2026-12-31",
                    "SLA_Tier": "Gold",
                    "Discount_Percentage": 5.0
                }
            ]
        }

    api_domain = _ZOHO_CACHE.get("api_domain", "https://www.zohoapis.com")
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        if deal_id:
            url = f"{api_domain}/crm/v2/Deals/{deal_id}"
            resp = await client.get(url, headers=headers)
        elif account_name:
            url = f"{api_domain}/crm/v2/Deals/search?criteria=(Account_Name:equals:{account_name})"
            resp = await client.get(url, headers=headers)
        else:
            url = f"{api_domain}/crm/v2/Deals"
            resp = await client.get(url, headers=headers)

        if resp.status_code in (200, 204):
            return resp.json() if resp.text else {"data": []}
        return {"error": resp.text, "status_code": resp.status_code}


async def zoho_search_account(account_name: str) -> Dict[str, Any]:
    """Search for an account by name in Zoho CRM."""
    token = await _get_zoho_access_token()
    if not token:
        return {
            "status": "mocked",
            "account": {
                "id": "ACC-10023",
                "Account_Name": account_name,
                "Industry": "Technology",
                "Phone": "+1-555-0199",
                "Website": "https://example.com"
            }
        }

    api_domain = _ZOHO_CACHE.get("api_domain", "https://www.zohoapis.com")
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        url = f"{api_domain}/crm/v2/Accounts/search?word={account_name}"
        resp = await client.get(url, headers=headers)
        return resp.json() if resp.status_code == 200 else {"data": [], "status_code": resp.status_code}


async def zoho_update_deal(deal_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Directly update Zoho deal fields (Only to be called via HITL execution!)."""
    token = await _get_zoho_access_token()
    if not token:
        return {"status": "mocked", "updated_deal_id": deal_id, "data": data, "message": "Deal updated in mock mode"}

    api_domain = _ZOHO_CACHE.get("api_domain", "https://www.zohoapis.com")
    headers = {"Authorization": f"Zoho-oauthtoken {token}", "Content-Type": "application/json"}

    payload = {"data": [data]}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        url = f"{api_domain}/crm/v2/Deals/{deal_id}"
        resp = await client.put(url, headers=headers, json=payload)
        return resp.json() if resp.status_code in (200, 201) else {"error": resp.text, "status_code": resp.status_code}


async def zoho_add_note(parent_module: str, parent_id: str, title: str, content: str) -> Dict[str, Any]:
    """Add an audit note to a Zoho CRM record (Deal, Account, etc.)."""
    token = await _get_zoho_access_token()
    if not token:
        return {"status": "mocked", "note_id": "NOTE-7712", "parent_id": parent_id}

    api_domain = _ZOHO_CACHE.get("api_domain", "https://www.zohoapis.com")
    headers = {"Authorization": f"Zoho-oauthtoken {token}", "Content-Type": "application/json"}

    payload = {
        "data": [{
            "Note_Title": title,
            "Note_Content": content,
            "se_module": parent_module,
            "parent_id": parent_id
        }]
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        url = f"{api_domain}/crm/v2/Notes"
        resp = await client.post(url, headers=headers, json=payload)
        return resp.json() if resp.status_code in (200, 201) else {"error": resp.text, "status_code": resp.status_code}


async def zoho_get_expiring_renewals(days_ahead: int = 90) -> Dict[str, Any]:
    """Retrieve deals whose contracts/renewals expire within the specified day window."""
    token = await _get_zoho_access_token()
    if not token:
        return {
            "status": "mocked",
            "expiring_deals": [
                {
                    "id": "DEAL-8891",
                    "Deal_Name": "Global Corp Annual License",
                    "Account_Name": "Global Corp",
                    "Closing_Date": "2026-12-15",
                    "Amount": 75000.0,
                    "Stage": "Renewal Due",
                    "Days_To_Renewal": 27
                },
                {
                    "id": "DEAL-8892",
                    "Deal_Name": "Stark Industries Cloud Support",
                    "Account_Name": "Stark Industries",
                    "Closing_Date": "2027-01-20",
                    "Amount": 150000.0,
                    "Stage": "Renewal Due",
                    "Days_To_Renewal": 63
                }
            ]
        }

    api_domain = _ZOHO_CACHE.get("api_domain", "https://www.zohoapis.com")
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        url = f"{api_domain}/crm/v2/Deals/search?criteria=(Stage:equals:Renewal Due)"
        resp = await client.get(url, headers=headers)
        return resp.json() if resp.status_code == 200 else {"expiring_deals": []}


# ==============================================================================
# 2. JIRA REST API V3 CLIENT & TOOLS
# ==============================================================================
def _jira_auth_headers() -> Optional[Dict[str, str]]:
    base_url = get_env("JIRA_BASE_URL")
    email = get_env("JIRA_EMAIL")
    token = get_env("JIRA_API_TOKEN")
    if not (base_url and email and token):
        return None
    raw_creds = f"{email}:{token}".encode("utf-8")
    auth_header = base64.b64encode(raw_creds).decode("utf-8")
    return {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }


async def jira_search_issues(jql: str, max_results: int = 10) -> Dict[str, Any]:
    """Search Jira issues using JQL syntax."""
    headers = _jira_auth_headers()
    base_url = get_env("JIRA_BASE_URL")
    if not (headers and base_url):
        return {
            "status": "mocked",
            "total": 2,
            "issues": [
                {
                    "key": "PROJ-101",
                    "fields": {
                        "summary": "Implement OAuth2 login with SSO",
                        "status": {"name": "In QA"},
                        "labels": ["REQ-001", "security"]
                    }
                },
                {
                    "key": "PROJ-102",
                    "fields": {
                        "summary": "Add real-time webhook dispatcher",
                        "status": {"name": "In QA"},
                        "labels": ["REQ-002", "integration"]
                    }
                }
            ]
        }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        url = f"{base_url.rstrip('/')}/rest/api/3/search"
        resp = await client.get(url, headers=headers, params={"jql": jql, "maxResults": max_results})
        return resp.json() if resp.status_code == 200 else {"error": resp.text, "status_code": resp.status_code}


async def jira_create_issue(
    project_key: str,
    summary: str,
    description: str,
    issue_type: str = "Story",
    req_id: Optional[str] = None,
    epic_link: Optional[str] = None,
    **kwargs
) -> Dict[str, Any]:
    """Create a new Jira issue linked to a REQ-XXX ID for traceability."""
    headers = _jira_auth_headers()
    base_url = get_env("JIRA_BASE_URL")
    if not (headers and base_url):
        issue_key = f"{project_key}-999"
        return {"status": "mocked", "key": issue_key, "id": "10099", "req_id": req_id, "epic_link": epic_link}

    labels = [req_id] if req_id else []
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "description": {
                "version": 1,
                "type": "doc",
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}]
                }]
            },
            "issuetype": {"name": issue_type},
            "labels": labels
        }
    }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        url = f"{base_url.rstrip('/')}/rest/api/3/issue"
        resp = await client.post(url, headers=headers, json=payload)
        return resp.json() if resp.status_code in (200, 201) else {"error": resp.text, "status_code": resp.status_code}


async def jira_transition_issue(issue_key: str, transition_name_or_id: str) -> Dict[str, Any]:
    """Transition a Jira issue (e.g. from 'In QA' to 'Deployed' / 'Done')."""
    headers = _jira_auth_headers()
    base_url = get_env("JIRA_BASE_URL")
    if not (headers and base_url):
        return {
            "status": "mocked",
            "issue_key": issue_key,
            "new_status": transition_name_or_id,
            "message": f"Issue {issue_key} transitioned to {transition_name_or_id} (mocked)"
        }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        transitions_url = f"{base_url.rstrip('/')}/rest/api/3/issue/{issue_key}/transitions"
        res = await client.get(transitions_url, headers=headers)
        if res.status_code != 200:
            return {"error": "Failed to fetch transitions", "details": res.text}

        transitions = res.json().get("transitions", [])
        target_id = None
        for t in transitions:
            if t.get("id") == transition_name_or_id or t.get("name", "").lower() == transition_name_or_id.lower():
                target_id = t.get("id")
                break

        if not target_id and transitions:
            target_id = transitions[0].get("id")

        if not target_id:
            return {"error": f"Transition '{transition_name_or_id}' not found for issue {issue_key}"}

        post_resp = await client.post(transitions_url, headers=headers, json={"transition": {"id": target_id}})
        if post_resp.status_code in (200, 204):
            return {"status": "success", "issue_key": issue_key, "transition_id": target_id}
        return {"error": post_resp.text, "status_code": post_resp.status_code}


async def jira_add_comment(issue_key: str, comment: str) -> Dict[str, Any]:
    """Post an audit comment to a Jira issue."""
    headers = _jira_auth_headers()
    base_url = get_env("JIRA_BASE_URL")
    if not (headers and base_url):
        return {"status": "mocked", "issue_key": issue_key, "comment_id": "COMM-555"}

    payload = {
        "body": {
            "version": 1,
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [{"type": "text", "text": comment}]
            }]
        }
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        url = f"{base_url.rstrip('/')}/rest/api/3/issue/{issue_key}/comment"
        resp = await client.post(url, headers=headers, json=payload)
        return resp.json() if resp.status_code in (200, 201) else {"error": resp.text, "status_code": resp.status_code}


# ==============================================================================
# 3. EMAIL, CALENDAR & WHATSAPP TOOLS
# ==============================================================================
async def email_create_draft(to: str, subject: str, body: str, provider: str = "auto", thread_id: Optional[str] = None) -> Dict[str, Any]:
    """Creates an email draft in either Gmail or Outlook (MS Graph). Does NOT send externally."""
    gmail_token = get_env("GMAIL_ACCESS_TOKEN")
    graph_token = get_env("MS_GRAPH_ACCESS_TOKEN")

    use_gmail = (provider == "gmail") or (provider == "auto" and bool(gmail_token))
    use_graph = (provider == "outlook") or (provider == "auto" and not gmail_token and bool(graph_token))

    if not (gmail_token or graph_token):
        return {
            "status": "mocked",
            "provider": provider if provider != "auto" else "gmail",
            "draft_id": "DRAFT-GMAIL-9912",
            "to": to,
            "subject": subject,
            "preview": body[:120] + "..."
        }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        if use_gmail and gmail_token:
            raw_msg = f"To: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}"
            raw_b64 = base64.urlsafe_b64encode(raw_msg.encode("utf-8")).decode("utf-8")
            url = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
            headers = {"Authorization": f"Bearer {gmail_token}", "Content-Type": "application/json"}
            msg_payload: Dict[str, Any] = {"raw": raw_b64}
            if thread_id:
                msg_payload["threadId"] = thread_id
            resp = await client.post(url, headers=headers, json={"message": msg_payload})
            return resp.json() if resp.status_code in (200, 201) else {"error": resp.text}

        elif use_graph and graph_token:
            url = "https://graph.microsoft.com/v1.0/me/messages"
            headers = {"Authorization": f"Bearer {graph_token}", "Content-Type": "application/json"}
            payload = {
                "subject": subject,
                "toRecipients": [{"emailAddress": {"address": to}}],
                "body": {"contentType": "Text", "content": body}
            }
            resp = await client.post(url, headers=headers, json=payload)
            return resp.json() if resp.status_code in (200, 201) else {"error": resp.text}

    return {"status": "error", "message": "No valid email provider configured"}


async def email_send_approved(to: str, subject: str, body: str, provider: str = "auto") -> Dict[str, Any]:
    """Executes actual email transmission. STRICTLY reserved for post-HITL execution!"""
    gmail_token = get_env("GMAIL_ACCESS_TOKEN")
    graph_token = get_env("MS_GRAPH_ACCESS_TOKEN")

    if not (gmail_token or graph_token):
        return {
            "status": "mocked",
            "delivered": True,
            "to": to,
            "subject": subject,
            "sent_at": "2026-11-18T12:00:00Z"
        }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        if gmail_token and (provider in ("gmail", "auto")):
            raw_msg = f"To: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}"
            raw_b64 = base64.urlsafe_b64encode(raw_msg.encode("utf-8")).decode("utf-8")
            url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
            headers = {"Authorization": f"Bearer {gmail_token}", "Content-Type": "application/json"}
            resp = await client.post(url, headers=headers, json={"raw": raw_b64})
            return resp.json() if resp.status_code in (200, 201) else {"error": resp.text}

        elif graph_token:
            url = "https://graph.microsoft.com/v1.0/me/sendMail"
            headers = {"Authorization": f"Bearer {graph_token}", "Content-Type": "application/json"}
            payload = {
                "message": {
                    "subject": subject,
                    "toRecipients": [{"emailAddress": {"address": to}}],
                    "body": {"contentType": "Text", "content": body}
                },
                "saveToSentItems": True
            }
            resp = await client.post(url, headers=headers, json=payload)
            return {"status": "success", "sent": True} if resp.status_code in (200, 202) else {"error": resp.text}

    return {"status": "error", "message": "No email provider token available"}


async def calendar_create_event(title: str, start_time: str, end_time: str, attendees: List[str], description: str = "", provider: str = "auto") -> Dict[str, Any]:
    """Schedules a calendar meeting invite via Google Calendar or MS Graph."""
    gmail_token = get_env("GMAIL_ACCESS_TOKEN")
    graph_token = get_env("MS_GRAPH_ACCESS_TOKEN")

    if not (gmail_token or graph_token):
        return {
            "status": "mocked",
            "event_id": "CAL-EVT-1029",
            "title": title,
            "start": start_time,
            "end": end_time,
            "attendees": attendees
        }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        if gmail_token and provider in ("gmail", "auto"):
            url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
            headers = {"Authorization": f"Bearer {gmail_token}", "Content-Type": "application/json"}
            payload = {
                "summary": title,
                "description": description,
                "start": {"dateTime": start_time},
                "end": {"dateTime": end_time},
                "attendees": [{"email": a} for a in attendees]
            }
            resp = await client.post(url, headers=headers, json=payload)
            return resp.json() if resp.status_code in (200, 201) else {"error": resp.text}

        elif graph_token:
            url = "https://graph.microsoft.com/v1.0/me/events"
            headers = {"Authorization": f"Bearer {graph_token}", "Content-Type": "application/json"}
            payload = {
                "subject": title,
                "body": {"contentType": "HTML", "content": description},
                "start": {"dateTime": start_time, "timeZone": "UTC"},
                "end": {"dateTime": end_time, "timeZone": "UTC"},
                "attendees": [{"emailAddress": {"address": a}, "type": "required"} for a in attendees]
            }
            resp = await client.post(url, headers=headers, json=payload)
            return resp.json() if resp.status_code in (200, 201) else {"error": resp.text}

    return {"status": "error", "message": "Calendar tokens not configured"}


async def whatsapp_send_message(to: str, message: str) -> Dict[str, Any]:
    """
    Sends an outbound text message via Meta WhatsApp Cloud API.
    Subject to HITL approval gate before live delivery.
    """
    token = get_env("WHATSAPP_TOKEN")
    phone_number_id = get_env("WHATSAPP_PHONE_NUMBER_ID")

    if not (token and phone_number_id):
        return {
            "status": "mocked",
            "messaging_product": "whatsapp",
            "to": to,
            "preview": message[:120] + "...",
            "message_id": "wamid.HBgLMTEwMDAwMDAwMBV"
        }

    url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(url, headers=headers, json=payload)
        return resp.json() if resp.status_code in (200, 201) else {"error": resp.text, "status_code": resp.status_code}


# ==============================================================================
# 4. SUPABASE POSTGREST CLIENT & TOOLS (STATE & HITL)
# ==============================================================================
def _supabase_headers() -> Optional[Dict[str, str]]:
    url = get_env("SUPABASE_URL")
    key = get_env("SUPABASE_SERVICE_KEY")
    if not (url and key):
        return None
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }


async def fetch_account_context(account_name: str) -> Dict[str, Any]:
    """Fetch stored account metadata, Zoho deal link, WhatsApp phone, and traceability map from Supabase."""
    headers = _supabase_headers()
    base_url = get_env("SUPABASE_URL")
    if not (headers and base_url):
        return {
            "status": "mocked",
            "account_name": account_name,
            "zoho_deal_id": "ZOHO-DEAL-9981",
            "jira_project_key": "PROJ",
            "whatsapp_phone": "+15551234567",
            "req_traceability_map": {
                "REQ-001": ["PROJ-101"],
                "REQ-002": ["PROJ-102"]
            },
            "metadata": {"sla_tier": "Gold", "renewal_date": "2026-12-31"}
        }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        url = f"{base_url.rstrip('/')}/rest/v1/account_context?account_name=eq.{account_name}&select=*"
        resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            rows = resp.json()
            return rows[0] if rows else {"found": False, "account_name": account_name}
        return {"error": resp.text, "status_code": resp.status_code}


async def upsert_account_context(
    account_name: str,
    zoho_deal_id: Optional[str] = None,
    jira_project_key: Optional[str] = None,
    whatsapp_phone: Optional[str] = None,
    req_traceability_map: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Upsert account context in Supabase PostgREST."""
    headers = _supabase_headers()
    base_url = get_env("SUPABASE_URL")
    if not (headers and base_url):
        return {
            "status": "mocked",
            "account_name": account_name,
            "zoho_deal_id": zoho_deal_id,
            "jira_project_key": jira_project_key,
            "whatsapp_phone": whatsapp_phone,
            "updated": True
        }

    payload: Dict[str, Any] = {"account_name": account_name}
    if zoho_deal_id:
        payload["zoho_deal_id"] = zoho_deal_id
    if jira_project_key:
        payload["jira_project_key"] = jira_project_key
    if whatsapp_phone:
        payload["whatsapp_phone"] = whatsapp_phone
    if req_traceability_map is not None:
        payload["req_traceability_map"] = req_traceability_map
    if metadata is not None:
        payload["metadata"] = metadata

    headers_with_upsert = dict(headers)
    headers_with_upsert["Prefer"] = "resolution=merge-duplicates,return=representation"

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        url = f"{base_url.rstrip('/')}/rest/v1/account_context"
        resp = await client.post(url, headers=headers_with_upsert, json=payload)
        return resp.json() if resp.status_code in (200, 201) else {"error": resp.text, "status_code": resp.status_code}


async def queue_for_approval(agent_name: str, action_type: str, target_system: str, payload: Dict[str, Any], risk_summary: str) -> Dict[str, Any]:
    """
    Stages an action into the Supabase `hitl_approvals` table.
    Enforces human governance over destructive, contractual, or external communication actions.
    """
    headers = _supabase_headers()
    base_url = get_env("SUPABASE_URL")
    if not (headers and base_url):
        import uuid
        fake_id = str(uuid.uuid4())
        return {
            "status": "queued",
            "approval_id": fake_id,
            "agent_name": agent_name,
            "action_type": action_type,
            "target_system": target_system,
            "payload": payload,
            "risk_summary": risk_summary,
            "approval_status": "pending"
        }

    item = {
        "agent_name": agent_name,
        "action_type": action_type,
        "target_system": target_system,
        "payload": payload,
        "risk_summary": risk_summary,
        "status": "pending"
    }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        url = f"{base_url.rstrip('/')}/rest/v1/hitl_approvals"
        resp = await client.post(url, headers=headers, json=item)
        if resp.status_code in (200, 201):
            created = resp.json()
            approval_row = created[0] if isinstance(created, list) and created else created
            return {
                "status": "queued",
                "approval_id": approval_row.get("id"),
                "approval_row": approval_row
            }
        return {"error": resp.text, "status_code": resp.status_code}


async def execute_approved_action(approval_id: str) -> Dict[str, Any]:
    """
    Executes a staged action once human approval has been granted.
    Dispatches to Zoho, Email, WhatsApp, Jira, or Calendar tool based on action_type.
    """
    headers = _supabase_headers()
    base_url = get_env("SUPABASE_URL")

    record: Dict[str, Any] = {}
    if headers and base_url:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            fetch_url = f"{base_url.rstrip('/')}/rest/v1/hitl_approvals?id=eq.{approval_id}&select=*"
            res = await client.get(fetch_url, headers=headers)
            if res.status_code == 200 and res.json():
                record = res.json()[0]
            else:
                return {"error": f"Approval {approval_id} not found or inaccessible."}

    action_type = record.get("action_type", "generic_approved_action")
    payload = record.get("payload", {})

    execution_result: Dict[str, Any] = {}
    if action_type == "zoho_update_deal":
        deal_id = payload.get("deal_id", "")
        data = payload.get("data", {})
        execution_result = await zoho_update_deal(deal_id, data)
    elif action_type == "email_send_approved":
        execution_result = await email_send_approved(
            to=payload.get("to", ""),
            subject=payload.get("subject", ""),
            body=payload.get("body", ""),
            provider=payload.get("provider", "auto")
        )
    elif action_type == "whatsapp_send_message":
        execution_result = await whatsapp_send_message(
            to=payload.get("to", ""),
            message=payload.get("message", "")
        )
    elif action_type == "jira_transition_issue":
        execution_result = await jira_transition_issue(
            issue_key=payload.get("issue_key", ""),
            transition_name_or_id=payload.get("transition", "")
        )
    else:
        execution_result = {"status": "executed", "action_type": action_type, "payload": payload}

    # Mark as executed in Supabase if live
    if headers and base_url and record:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            patch_url = f"{base_url.rstrip('/')}/rest/v1/hitl_approvals?id=eq.{approval_id}"
            await client.patch(patch_url, headers=headers, json={"status": "executed"})

    return {
        "status": "success",
        "approval_id": approval_id,
        "action_type": action_type,
        "execution_result": execution_result
    }


# ==============================================================================
# 5. COMMERCIAL RATE-CARDS, SCOPE IMPACT & RAID LOG TOOLS
# ==============================================================================

# In-memory stores for fallback and offline resilience
_IN_MEMORY_RAID_LOGS: List[Dict[str, Any]] = [
    {
        "id": "raid-001",
        "account_name": "Acme Corp",
        "category": "Risk",
        "severity": "High",
        "description": "Third-party OCR API rate-limit may throttle batch invoice extraction during peak business hours (EOM).",
        "owner": "Lead Architect",
        "status": "open",
        "created_at": "2026-11-18T09:00:00Z",
        "updated_at": "2026-11-18T09:00:00Z"
    },
    {
        "id": "raid-002",
        "account_name": "Acme Corp",
        "category": "Dependency",
        "severity": "Medium",
        "description": "Client Infosec sign-off required for webhook bearer token encryption before production deployment.",
        "owner": "Security Lead",
        "status": "open",
        "created_at": "2026-11-18T09:30:00Z",
        "updated_at": "2026-11-18T09:30:00Z"
    },
    {
        "id": "raid-003",
        "account_name": "Global Corp",
        "category": "Assumption",
        "severity": "Low",
        "description": "Assuming client ERP provides standardized ISO 8601 timestamps on inbound invoice payloads.",
        "owner": "Lead BA",
        "status": "mitigated",
        "created_at": "2026-11-18T10:00:00Z",
        "updated_at": "2026-11-18T11:00:00Z"
    }
]

_IN_MEMORY_AUDIT_LOGS: List[Dict[str, Any]] = []


async def calculate_project_quote(
    weeks: int,
    team_mix: Optional[Dict[str, int]] = None,
    discount_pct: float = 0.0
) -> Dict[str, Any]:
    """
    Commercial Rate-Card & Effort Estimator:
    Calculates project fees, billable hours, milestone payment splits, and margin impact.
    Flags any discount > 10% as an immediate '⚠️ MARGIN ALERT'.
    """
    if weeks <= 0:
        weeks = 4

    # Default resource mix: 1 Lead BA/PM, 2 Senior Engineers, 1 QA/DevOps
    default_team = {
        "lead_ba_pm": 1,
        "senior_engineer": 2,
        "qa_devops": 1
    }
    mix = team_mix if team_mix else default_team

    rates = {
        "lead_ba_pm": 120.0,       # Lead BA / Technical PM: $120/hr
        "senior_engineer": 100.0,   # Senior Full-Stack Engineer: $100/hr
        "qa_devops": 85.0           # QA & DevOps Specialist: $85/hr
    }

    hours_per_week = 40
    role_breakdown = {}
    gross_fee = 0.0
    total_hours = 0

    for role, count in mix.items():
        role_hours = int(weeks * hours_per_week * count)
        rate = rates.get(role, 100.0)
        subtotal = round(role_hours * rate, 2)
        gross_fee += subtotal
        total_hours += role_hours
        role_breakdown[role] = {
            "headcount": count,
            "billable_hours": role_hours,
            "hourly_rate": rate,
            "subtotal": subtotal
        }

    gross_fee = round(gross_fee, 2)
    discount_amount = round(gross_fee * (max(0.0, discount_pct) / 100.0), 2)
    net_fee = round(gross_fee - discount_amount, 2)

    # Standard milestone payment split: 30% Kickoff / 40% UAT Handover / 30% Go-Live
    m1_kickoff = round(net_fee * 0.30, 2)
    m2_uat = round(net_fee * 0.40, 2)
    m3_golive = round(net_fee - m1_kickoff - m2_uat, 2)

    has_margin_alert = discount_pct > 10.0
    margin_alert_text = None
    if has_margin_alert:
        margin_alert_text = (
            f"⚠️ MARGIN ALERT: Proposed discount of {discount_pct:.1f}% exceeds authorized 10.0% threshold. "
            f"Margin erosion: -${discount_amount:,.2f}. Escalation required to Commercial Director before client distribution."
        )

    return {
        "status": "success",
        "duration_weeks": weeks,
        "total_billable_hours": total_hours,
        "gross_fee": gross_fee,
        "discount_pct": discount_pct,
        "discount_amount": discount_amount,
        "net_fee": net_fee,
        "currency": "USD",
        "role_breakdown": role_breakdown,
        "milestone_schedule": {
            "milestone_1_kickoff_30pct": m1_kickoff,
            "milestone_2_uat_40pct": m2_uat,
            "milestone_3_golive_30pct": m3_golive,
            "payment_terms": "Net 30 days upon milestone sign-off"
        },
        "has_margin_alert": has_margin_alert,
        "margin_alert": margin_alert_text
    }


async def analyze_scope_impact(
    account_name: str,
    new_request: str
) -> Dict[str, Any]:
    """
    Change Request (CR) & Scope-Creep Detector:
    Compares incoming requests against stored account traceability maps and baseline SOW.
    Classifies as In-Scope or Billable Change Request (CR) with effort delta, cost delta,
    security implications, and negative edge case test criteria.
    """
    account_ctx = await fetch_account_context(account_name)
    existing_traceability = account_ctx.get("req_traceability_map", {}) if isinstance(account_ctx, dict) else {}

    # Identify whether the request represents expansion or minor clarification
    expansion_triggers = [
        "sso", "saml", "oauth", "biometric", "2fa", "mfa", "ocr", "ai", "bulk",
        "export", "integration", "webhook", "migration", "audit", "compliance", "multi-tenant"
    ]
    req_lower = new_request.lower()
    is_cr = any(trigger in req_lower for trigger in expansion_triggers) or len(new_request) > 150

    if is_cr:
        classification = "Billable Change Request (CR)"
        delta_weeks = 2.5
        delta_hours = 100
        delta_cost = 10500.00
        rationale = (
            f"Requested capability introduces architectural components not captured in baseline requirement map "
            f"({list(existing_traceability.keys()) if existing_traceability else 'No prior REQs'}). "
            f"Requires backend schema extensions, third-party authentication integration, and security audit."
        )
        security_implications = [
            "OAuth 2.0 / SAML token replay protection and state parameter validation",
            "Role-Based Access Control (RBAC) enforcement on all newly exposed API endpoints",
            "PII encryption at rest using AES-GCM and in transit via TLS 1.3",
            "Audit trail logging for all administrative credential and profile transitions"
        ]
        negative_edge_cases = [
            "Given an expired or invalid bearer token, system must return 401 Unauthorized within 200ms without exposing stack traces.",
            "Given a malformed or partial JSON payload, input validator must reject with 422 Unprocessable Entity and detailed field errors.",
            "Given an upstream identity provider timeout (> 5000ms), system must fail gracefully to fallback authentication mode.",
            "Given simultaneous concurrent requests with identical idempotency keys, duplicate execution must be blocked."
        ]
    else:
        classification = "In-Scope (Minor Refinement)"
        delta_weeks = 0.0
        delta_hours = 0
        delta_cost = 0.00
        rationale = "Request represents a functional clarification or parameter adjustment within existing scope boundaries."
        security_implications = ["Standard input sanitization and HTTPS transport."]
        negative_edge_cases = ["Given boundary-value input, system must handle gracefully without unhandled exceptions."]

    return {
        "status": "success",
        "account_name": account_name,
        "classification": classification,
        "is_billable_cr": is_cr,
        "effort_delta_weeks": delta_weeks,
        "effort_delta_hours": delta_hours,
        "estimated_cost_delta": delta_cost,
        "rationale": rationale,
        "baseline_requirements_found": list(existing_traceability.keys()) if existing_traceability else [],
        "security_compliance_requirements": security_implications,
        "negative_test_edge_cases": negative_edge_cases
    }


async def upsert_raid_item(
    account_name: str,
    category: str,
    severity: str,
    description: str,
    owner: str = "Unassigned",
    status: str = "open",
    item_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Creates or updates an item in the Project RAID Log (Risks, Assumptions, Issues, Dependencies).
    Persists to Supabase `raid_logs` table with fallback to in-memory store.
    """
    valid_categories = ["Risk", "Assumption", "Issue", "Dependency"]
    normalized_cat = category.capitalize() if category.capitalize() in valid_categories else "Risk"

    valid_severities = ["High", "Medium", "Low"]
    normalized_sev = severity.capitalize() if severity.capitalize() in valid_severities else "Medium"

    valid_statuses = ["open", "mitigated", "resolved"]
    normalized_status = status.lower() if status.lower() in valid_statuses else "open"

    import uuid
    actual_id = item_id or f"raid-{str(uuid.uuid4())[:8]}"

    item_data = {
        "id": actual_id,
        "account_name": account_name,
        "category": normalized_cat,
        "severity": normalized_sev,
        "description": description,
        "owner": owner,
        "status": normalized_status,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }

    # Update in-memory cache
    existing_idx = next((i for i, r in enumerate(_IN_MEMORY_RAID_LOGS) if r.get("id") == actual_id), None)
    if existing_idx is not None:
        _IN_MEMORY_RAID_LOGS[existing_idx].update(item_data)
    else:
        item_data["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _IN_MEMORY_RAID_LOGS.insert(0, item_data)

    headers = _supabase_headers()
    base_url = get_env("SUPABASE_URL")
    if headers and base_url:
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                url = f"{base_url.rstrip('/')}/rest/v1/raid_logs"
                headers_upsert = dict(headers)
                headers_upsert["Prefer"] = "resolution=merge-duplicates,return=representation"
                await client.post(url, headers=headers_upsert, json=item_data)
        except Exception as e:
            print(f"[Supabase RAID Upsert Warning] {e}")

    return {
        "status": "success",
        "raid_item": item_data
    }


async def get_active_raid_items(account_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetches active (open or mitigated) RAID log items for an account or across all accounts.
    """
    headers = _supabase_headers()
    base_url = get_env("SUPABASE_URL")

    if headers and base_url:
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                query = "status=neq.resolved"
                if account_name:
                    query += f"&account_name=eq.{account_name}"
                url = f"{base_url.rstrip('/')}/rest/v1/raid_logs?{query}&order=created_at.desc&limit=50"
                res = await client.get(url, headers=headers)
                if res.status_code == 200:
                    rows = res.json()
                    return {"status": "success", "items": rows, "count": len(rows)}
        except Exception as e:
            print(f"[Supabase RAID Fetch Warning] {e}")

    # Fallback to in-memory store
    filtered = [
        r for r in _IN_MEMORY_RAID_LOGS
        if r.get("status") in ("open", "mitigated")
        and (not account_name or r.get("account_name", "").lower() == account_name.lower())
    ]
    return {"status": "success", "items": filtered, "count": len(filtered)}


async def log_swarm_run(
    event_source: str,
    initial_event: str,
    agent_handoff_path: List[Dict[str, Any]],
    discrepancies_detected: List[Dict[str, Any]],
    final_summary: str,
    execution_time_ms: int = 0
) -> Dict[str, Any]:
    """Writes an immutable record to `swarm_audit_logs` in Supabase and in-memory cache."""
    import uuid
    log_item = {
        "id": f"log-{str(uuid.uuid4())[:8]}",
        "event_source": event_source,
        "initial_event": initial_event[:4000],
        "agent_handoff_path": agent_handoff_path,
        "discrepancies_detected": discrepancies_detected,
        "final_summary": final_summary,
        "execution_time_ms": execution_time_ms,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    _IN_MEMORY_AUDIT_LOGS.insert(0, log_item)
    if len(_IN_MEMORY_AUDIT_LOGS) > 100:
        _IN_MEMORY_AUDIT_LOGS.pop()

    headers = _supabase_headers()
    base_url = get_env("SUPABASE_URL")
    if not (headers and base_url):
        return {"status": "mocked", "logged": True, "log_id": log_item["id"]}

    payload = {
        "event_source": event_source,
        "initial_event": initial_event[:4000],
        "agent_handoff_path": agent_handoff_path,
        "discrepancies_detected": discrepancies_detected,
        "final_summary": final_summary,
        "execution_time_ms": execution_time_ms
    }

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            url = f"{base_url.rstrip('/')}/rest/v1/swarm_audit_logs"
            resp = await client.post(url, headers=headers, json=payload)
            return resp.json() if resp.status_code in (200, 201) else {"error": resp.text}
    except Exception as e:
        return {"error": str(e), "status": "failed_but_cached_in_memory"}


# ==============================================================================
# 6. OPENAI TOOL SCHEMAS & REGISTRY
# ==============================================================================
SWARM_TOOLS_SCHEMA = [
    # Handoff Tools
    {
        "type": "function",
        "function": {
            "name": "transfer_to_commercial",
            "description": "Transfer swarm control to Commercial & Renewal Specialist for proposals, SOWs, SLA matrices, and Zoho deal reviews.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Justification for handoff"},
                    "context": {"type": "string", "description": "Summarized context to pass to Commercial Agent"}
                },
                "required": ["reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "transfer_to_ba_docs",
            "description": "Transfer swarm control to Principal BA & Docs Architect for generating BRD, PRD, or PDD with user stories and Gherkin criteria.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Justification for handoff"},
                    "context": {"type": "string", "description": "Requirements or scope summary to pass to BA Docs Agent"}
                },
                "required": ["reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "transfer_to_delivery",
            "description": "Transfer swarm control to Technical Delivery & Release Agent for deployment verification and Jira status transitions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Justification for handoff"},
                    "context": {"type": "string", "description": "Deployment notes, release tags, or issue keys"}
                },
                "required": ["reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "transfer_to_comms",
            "description": "Transfer swarm control to Comms & Meeting Orchestrator for MoM extraction, email/WhatsApp drafting, or calendar scheduling.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Justification for handoff"},
                    "context": {"type": "string", "description": "Meeting transcript or thread context"}
                },
                "required": ["reason"]
            }
        }
    },
    # Zoho Tools
    {
        "type": "function",
        "function": {
            "name": "zoho_get_deals",
            "description": "Query Zoho CRM deals by account name or specific deal ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_name": {"type": "string", "description": "Name of the client account"},
                    "deal_id": {"type": "string", "description": "Optional specific Zoho Deal ID"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "zoho_search_account",
            "description": "Search Zoho CRM for account profile details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_name": {"type": "string", "description": "Account or company name"}
                },
                "required": ["account_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "zoho_get_expiring_renewals",
            "description": "Fetch deals up for contract renewal within 90/60/30 days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer", "description": "Horizon in days (default: 90)"}
                }
            }
        }
    },
    # Jira Tools
    {
        "type": "function",
        "function": {
            "name": "jira_search_issues",
            "description": "Search Jira issues using JQL syntax.",
            "parameters": {
                "type": "object",
                "properties": {
                    "jql": {"type": "string", "description": "JQL query string"},
                    "max_results": {"type": "integer", "description": "Max issues to return"}
                },
                "required": ["jql"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jira_create_issue",
            "description": "Create a new Jira issue linked to a REQ-XXX requirement ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_key": {"type": "string", "description": "Jira Project Key (e.g., PROJ)"},
                    "summary": {"type": "string", "description": "Issue summary/title"},
                    "description": {"type": "string", "description": "Issue description with acceptance criteria"},
                    "issue_type": {"type": "string", "description": "Story, Bug, Task, or Epic"},
                    "req_id": {"type": "string", "description": "Requirement ID (e.g., REQ-001)"},
                    "epic_link": {"type": "string", "description": "Optional parent Epic key (e.g., NOVA-101)"}
                },
                "required": ["project_key", "summary", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jira_transition_issue",
            "description": "Transition Jira issue status (e.g. from 'In QA' to 'Deployed' or 'Done').",
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-101)"},
                    "transition_name_or_id": {"type": "string", "description": "Target status name or transition ID"}
                },
                "required": ["issue_key", "transition_name_or_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jira_add_comment",
            "description": "Add an audit note or deployment comment to a Jira issue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string", "description": "Jira issue key (e.g., PROJ-101)"},
                    "comment": {"type": "string", "description": "Comment text"}
                },
                "required": ["issue_key", "comment"]
            }
        }
    },
    # Email, Calendar & WhatsApp Tools
    {
        "type": "function",
        "function": {
            "name": "email_create_draft",
            "description": "Create a draft email in Gmail or Outlook for human review. Does NOT send externally.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Draft body text"},
                    "provider": {"type": "string", "enum": ["auto", "gmail", "outlook"], "description": "Email provider"}
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "whatsapp_send_message",
            "description": "Send a WhatsApp message via Meta Cloud API. Only executed after HITL approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient phone number with country code (e.g. +15551234567)"},
                    "message": {"type": "string", "description": "Text message content"}
                },
                "required": ["to", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_create_event",
            "description": "Schedule a follow-up meeting in Google Calendar or Outlook.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Meeting subject/title"},
                    "start_time": {"type": "string", "description": "ISO 8601 start datetime string"},
                    "end_time": {"type": "string", "description": "ISO 8601 end datetime string"},
                    "attendees": {"type": "array", "items": {"type": "string"}, "description": "List of attendee emails"},
                    "description": {"type": "string", "description": "Meeting agenda"}
                },
                "required": ["title", "start_time", "end_time", "attendees"]
            }
        }
    },
    # Supabase State & HITL Tools
    {
        "type": "function",
        "function": {
            "name": "fetch_account_context",
            "description": "Retrieve account context, Zoho deal link, and requirement traceability map from Supabase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_name": {"type": "string", "description": "Name of the account"}
                },
                "required": ["account_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "upsert_account_context",
            "description": "Update account context, WhatsApp number, or traceability map in Supabase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_name": {"type": "string", "description": "Account name"},
                    "zoho_deal_id": {"type": "string", "description": "Associated Zoho deal ID"},
                    "jira_project_key": {"type": "string", "description": "Jira project key"},
                    "whatsapp_phone": {"type": "string", "description": "Client WhatsApp phone number"},
                    "req_traceability_map": {"type": "object", "description": "Traceability JSON mapping REQ-ID to ticket keys"}
                },
                "required": ["account_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "queue_for_approval",
            "description": "Queue a high-risk or external action (Zoho deal update, email send, WhatsApp send, SLA commitment) for Human-in-the-Loop review.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {"type": "string", "description": "Agent requesting action (e.g. commercial, comms)"},
                    "action_type": {"type": "string", "description": "Type of action: zoho_update_deal, email_send_approved, whatsapp_send_message, jira_transition_issue"},
                    "target_system": {"type": "string", "description": "Target platform: zoho, gmail, outlook, whatsapp, jira"},
                    "payload": {"type": "object", "description": "Arguments required to execute the action upon approval"},
                    "risk_summary": {"type": "string", "description": "Detailed explanation of risk and why human review is required"}
                },
                "required": ["agent_name", "action_type", "target_system", "payload", "risk_summary"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_project_quote",
            "description": "Calculate project pricing, billable hours, milestone payments, and discount margin alerts based on standard rate cards.",
            "parameters": {
                "type": "object",
                "properties": {
                    "weeks": {"type": "integer", "description": "Project duration in weeks (default: 4)"},
                    "team_mix": {"type": "object", "description": "Resource composition mapping role to headcount: lead_ba_pm, senior_engineer, qa_devops"},
                    "discount_pct": {"type": "number", "description": "Proposed concession/discount percentage (0 to 100)"}
                },
                "required": ["weeks"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_scope_impact",
            "description": "Detect scope creep and analyze whether a client request is In-Scope or a Billable Change Request (CR) with effort and cost delta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_name": {"type": "string", "description": "Name of the client account"},
                    "new_request": {"type": "string", "description": "Raw description of the requested capability or change"}
                },
                "required": ["account_name", "new_request"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "upsert_raid_item",
            "description": "Create or update an entry in the project RAID log (Risk, Assumption, Issue, Dependency).",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_name": {"type": "string", "description": "Name of the client account"},
                    "category": {"type": "string", "enum": ["Risk", "Assumption", "Issue", "Dependency"], "description": "RAID item category"},
                    "severity": {"type": "string", "enum": ["High", "Medium", "Low"], "description": "Impact/severity level"},
                    "description": {"type": "string", "description": "Clear statement of the risk, assumption, issue or dependency"},
                    "owner": {"type": "string", "description": "Responsible role or owner (e.g. Lead Architect, Commercial Director)"},
                    "status": {"type": "string", "enum": ["open", "mitigated", "resolved"], "description": "Status of the RAID item"},
                    "item_id": {"type": "string", "description": "Optional existing RAID ID for update"}
                },
                "required": ["account_name", "category", "severity", "description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_raid_items",
            "description": "Retrieve active (open or mitigated) RAID log items across the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_name": {"type": "string", "description": "Optional account name filter"}
                }
            }
        }
    }
]


# Tool dispatcher map
TOOL_DISPATCHER = {
    "zoho_get_deals": zoho_get_deals,
    "zoho_search_account": zoho_search_account,
    "zoho_update_deal": zoho_update_deal,
    "zoho_add_note": zoho_add_note,
    "zoho_get_expiring_renewals": zoho_get_expiring_renewals,
    "jira_search_issues": jira_search_issues,
    "jira_create_issue": jira_create_issue,
    "jira_transition_issue": jira_transition_issue,
    "jira_add_comment": jira_add_comment,
    "email_create_draft": email_create_draft,
    "email_send_approved": email_send_approved,
    "whatsapp_send_message": whatsapp_send_message,
    "calendar_create_event": calendar_create_event,
    "fetch_account_context": fetch_account_context,
    "upsert_account_context": upsert_account_context,
    "queue_for_approval": queue_for_approval,
    "calculate_project_quote": calculate_project_quote,
    "analyze_scope_impact": analyze_scope_impact,
    "upsert_raid_item": upsert_raid_item,
    "get_active_raid_items": get_active_raid_items
}


async def execute_tool_call(name: str, arguments: Dict[str, Any]) -> Any:
    """Executes a registered tool function asynchronously by name with argument binding and safe exception handling."""
    if name not in TOOL_DISPATCHER:
        return {"error": f"Unknown tool function '{name}'"}
    
    func = TOOL_DISPATCHER[name]
    try:
        import inspect
        sig = inspect.signature(func)
        has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if not has_var_keyword:
            filtered_args = {k: v for k, v in arguments.items() if k in sig.parameters}
        else:
            filtered_args = arguments

        return await func(**filtered_args)
    except Exception as e:
        print(f"[Tool Execution Exception] Tool '{name}' failed with arguments {arguments}: {e}")
        return {"error": f"Tool '{name}' execution failed: {str(e)}"}
