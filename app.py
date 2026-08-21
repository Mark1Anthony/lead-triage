"""
Lead Triage — FastAPI app.

A small CRM-style intake tool that accepts leads (via form or webhook),
runs them through the triage classifier (GPT in live mode, deterministic
mock in demo mode), stores them in SQLite and shows a kanban board.

Run locally:
    uvicorn app:app --reload
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from security import client_ip, demo_lead_limiter, require_token
from triage import LeadInput, classify_lead, current_mode

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "leads.db"

app = FastAPI(title="Lead Triage")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ─── Database ────────────────────────────────────────────────────

def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at  TEXT NOT NULL,
            name         TEXT NOT NULL,
            company      TEXT NOT NULL,
            email        TEXT NOT NULL,
            source       TEXT NOT NULL,
            message      TEXT NOT NULL,
            priority     TEXT NOT NULL,
            category     TEXT NOT NULL,
            next_action  TEXT NOT NULL,
            summary      TEXT NOT NULL,
            reasoning    TEXT NOT NULL,
            mode         TEXT NOT NULL,
            status       TEXT DEFAULT 'new'
        );
        """
    )
    return conn


def seed_demo_leads() -> None:
    """Drop a few example leads into an empty database so the dashboard
    isn't blank on a fresh deploy. Only runs when the table is empty."""
    conn = db()
    count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    if count > 0:
        conn.close()
        return

    examples = [
        LeadInput(
            name="Sarah Lang",
            company="Nord Capital",
            email="sarah@nordcapital.de",
            source="website",
            message="We need a CRM integration live before end of Q2. Budget is approved. Can we schedule a call this week?",
        ),
        LeadInput(
            name="Tom Becker",
            company="Becker & Partner",
            email="tom@beckerpartner.de",
            source="linkedin",
            message="Interested in your lead automation work. We're comparing a few providers — can we see a demo?",
        ),
        LeadInput(
            name="Maria Weiß",
            company="studioweiss",
            email="maria@studioweiss.com",
            source="referral",
            message="Hi, we're a small design agency. Just looking around, not urgent.",
        ),
        LeadInput(
            name="Jonas Richter",
            company="Bitfarm GmbH",
            email="jonas@bitfarm.io",
            source="website",
            message="Urgent: our current CRM goes offline next week. Need replacement with working webhooks ASAP.",
        ),
        LeadInput(
            name="Petra Kurz",
            company="Kurz Retail",
            email="petra@kurzretail.de",
            source="event",
            message="Met at the trade fair. Exploring tools to automate customer follow-ups. No specific timeline.",
        ),
    ]

    for lead in examples:
        result = classify_lead(lead)
        conn.execute(
            """INSERT INTO leads
               (received_at, name, company, email, source, message,
                priority, category, next_action, summary, reasoning, mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.utcnow().isoformat(),
                lead.name, lead.company, lead.email, lead.source, lead.message,
                result.priority, result.category, result.next_action,
                result.summary, result.reasoning, result.mode,
            ),
        )
    conn.commit()
    conn.close()


seed_demo_leads()


# ─── Routes ──────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM leads ORDER BY id DESC LIMIT 100"
    ).fetchall()
    conn.close()

    grouped = {"hot": [], "warm": [], "cold": []}
    for r in rows:
        grouped.setdefault(r["priority"], []).append(dict(r))

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "leads": [dict(r) for r in rows],
            "grouped": grouped,
            "mode": current_mode(),
            "total": len(rows),
        },
    )


def lead_from_form(name: str, company: str, email: str, source: str, message: str) -> LeadInput:
    lead = LeadInput(
        name=name.strip(),
        company=company.strip(),
        email=email.strip(),
        source=source.strip() or "website",
        message=message.strip(),
    )
    if not lead.name or not lead.company or not lead.message:
        raise HTTPException(status_code=400, detail="name, company and message are required")
    return lead


def store_lead(lead: LeadInput):
    """Classify a lead and persist it. Shared by /leads, /webhook and /demo-lead."""
    result = classify_lead(lead)

    conn = db()
    cur = conn.execute(
        """INSERT INTO leads
           (received_at, name, company, email, source, message,
            priority, category, next_action, summary, reasoning, mode)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.utcnow().isoformat(),
            lead.name, lead.company, lead.email, lead.source, lead.message,
            result.priority, result.category, result.next_action,
            result.summary, result.reasoning, result.mode,
        ),
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()

    return new_id, result


@app.post("/leads", dependencies=[Depends(require_token)])
async def create_lead(
    name: str = Form(...),
    company: str = Form(...),
    email: str = Form(...),
    source: str = Form("website"),
    message: str = Form(...),
):
    lead = lead_from_form(name, company, email, source, message)
    new_id, result = store_lead(lead)

    return {
        "id": new_id,
        "classification": asdict(result),
    }


@app.post("/demo-lead")
async def create_demo_lead(
    request: Request,
    name: str = Form(...),
    company: str = Form(...),
    email: str = Form(""),
    source: str = Form("website"),
    message: str = Form(...),
    website: str = Form(""),
):
    """Token-free intake for the public form on /.

    Can only create, never change or delete. Protected by a honeypot field and
    an IP rate limit instead of a token, so the demo stays usable without
    handing out write access to the whole database.
    """
    # Honeypot: invisible to humans, bots like filling in URL fields. Answer as
    # if it worked - telling the bot would only make it rename the field.
    if website:
        return {"ok": True}

    if not await demo_lead_limiter.allow(client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded, try again in a minute",
        )

    lead = lead_from_form(name, company, email, source, message)
    new_id, result = store_lead(lead)

    return {
        "id": new_id,
        "classification": asdict(result),
    }


# Webhook endpoint that takes JSON instead of form data. Same logic.
class WebhookLead(BaseModel):
    name: str
    company: str
    email: str = Field(default="")
    source: str = Field(default="webhook")
    message: str


@app.post("/webhook", dependencies=[Depends(require_token)])
def webhook(lead: WebhookLead):
    result = classify_lead(LeadInput(**lead.model_dump()))
    conn = db()
    conn.execute(
        """INSERT INTO leads
           (received_at, name, company, email, source, message,
            priority, category, next_action, summary, reasoning, mode)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.utcnow().isoformat(),
            lead.name, lead.company, lead.email, lead.source, lead.message,
            result.priority, result.category, result.next_action,
            result.summary, result.reasoning, result.mode,
        ),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "classification": asdict(result)}


@app.post("/leads/{lead_id}/status", dependencies=[Depends(require_token)])
async def update_status(lead_id: int, request: Request):
    body = await request.json()
    status = body.get("status", "new")
    if status not in {"new", "contacted", "won", "lost"}:
        raise HTTPException(status_code=400, detail="invalid status")
    conn = db()
    conn.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/leads/{lead_id}", dependencies=[Depends(require_token)])
def delete_lead(lead_id: int):
    conn = db()
    conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/leads", dependencies=[Depends(require_token)])
def list_leads():
    conn = db()
    rows = conn.execute(
        "SELECT * FROM leads ORDER BY id DESC LIMIT 100"
    ).fetchall()
    conn.close()
    return {"leads": [dict(r) for r in rows]}


@app.get("/health")
def health():
    return {"status": "ok", "mode": current_mode(), "time": datetime.utcnow().isoformat()}
