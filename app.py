"""
Lead Triage — FastAPI app.

A small CRM-style intake tool that accepts leads (via form or webhook),
runs them through the triage classifier (GPT in live mode, deterministic
mock in demo mode), stores them in SQLite or Postgres (see db.py) and shows
a kanban board.

Run locally:
    uvicorn app:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import db
from security import client_ip, demo_lead_limiter, effective_token, require_token
from triage import LeadInput, classify_lead, current_mode

log = logging.getLogger("lead_triage")

BASE_DIR = db.BASE_DIR


# ─── Seed data ───────────────────────────────────────────────────

def seed_demo_leads() -> None:
    """Drop a few example leads into an empty database so the dashboard
    isn't blank on a fresh deploy. Only runs when the table is empty."""
    with db.connect() as conn:
        if db.one_value(conn, "SELECT COUNT(*) FROM leads") > 0:
            return

        _insert_examples(conn)


def _insert_examples(conn) -> None:
    examples = [
        LeadInput(
            name="Sarah Lang",
            company="Nord Capital",
            email="sarah@nordcapital.de",
            source="website",
            message=(
                "We need a CRM integration live before end of Q2. Budget is approved. "
                "Can we schedule a call this week?"
            ),
        ),
        LeadInput(
            name="Tom Becker",
            company="Becker & Partner",
            email="tom@beckerpartner.de",
            source="linkedin",
            message=(
                "Interested in your lead automation work. We're comparing a few "
                "providers — can we see a demo?"
            ),
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
            message=(
                "Urgent: our current CRM goes offline next week. Need replacement "
                "with working webhooks ASAP."
            ),
        ),
        LeadInput(
            name="Petra Kurz",
            company="Kurz Retail",
            email="petra@kurzretail.de",
            source="event",
            message=(
                "Met at the trade fair. Exploring tools to automate customer "
                "follow-ups. No specific timeline."
            ),
        ),
    ]

    for lead in examples:
        result = classify_lead(lead)
        db.execute(
            conn,
            """INSERT INTO leads
               (received_at, name, company, email, source, message,
                priority, category, next_action, summary, reasoning, mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                lead.name, lead.company, lead.email, lead.source, lead.message,
                result.priority, result.category, result.next_action,
                result.summary, result.reasoning, result.mode,
            ),
        )
    conn.commit()


# ─── App ─────────────────────────────────────────────────────────

def configure_logging() -> None:
    """Make this app's own log lines visible.

    Uvicorn configures its own loggers and leaves the root logger untouched,
    and Python's fallback handler only prints WARNING and above. Without this,
    everything logged here at INFO is silently dropped in production - which is
    exactly what happened on the first deployment: the warning about the
    generated token appeared, the line naming the database backend did not.

    Borrowing uvicorn's handler keeps the format identical to the surrounding
    server output rather than introducing a second one. It normally sits on the
    "uvicorn" logger, which "uvicorn.error" only inherits from - but under
    gunicorn's uvicorn worker it is the other way round, so check both. Outside
    uvicorn (tests, a plain interpreter) there is nothing to borrow, so fall
    back to the standard setup.
    """
    handlers = (
        logging.getLogger("uvicorn.error").handlers
        or logging.getLogger("uvicorn").handlers
    )
    if handlers:
        # A copy: assigning the list itself would leave both loggers sharing one
        # object, so adding a handler to either would silently change the other.
        log.handlers = list(handlers)
        log.propagate = False
    else:
        logging.basicConfig(level=logging.INFO)
    log.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Set up the schema and the demo rows once, at startup."""
    configure_logging()
    db.init()
    log.info("Database backend: %s", db.backend())
    seed_demo_leads()

    token, generated = effective_token()
    if generated:
        log.warning(
            "LEAD_TRIAGE_TOKEN is unset - generated one for this process: %s\n"
            "Write endpoints accept it as the X-Api-Token header. It changes on "
            "every restart; set LEAD_TRIAGE_TOKEN to keep it stable.",
            token,
        )
    else:
        log.info("Using LEAD_TRIAGE_TOKEN from the environment.")

    yield


app = FastAPI(title="Lead Triage", lifespan=lifespan)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ─── Routes ──────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with db.connect() as conn:
        rows = db.all_rows(conn, "SELECT * FROM leads ORDER BY id DESC LIMIT 100")

    grouped = {"hot": [], "warm": [], "cold": []}
    for r in rows:
        grouped.setdefault(r["priority"], []).append(r)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "leads": rows,
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

    with db.connect() as conn:
        new_id = db.insert_returning_id(
            conn,
            """INSERT INTO leads
               (received_at, name, company, email, source, message,
                priority, category, next_action, summary, reasoning, mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                lead.name, lead.company, lead.email, lead.source, lead.message,
                result.priority, result.category, result.next_action,
                result.summary, result.reasoning, result.mode,
            ),
        )
        conn.commit()

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
    _, result = store_lead(LeadInput(**lead.model_dump()))
    return {"ok": True, "classification": asdict(result)}


@app.post("/leads/{lead_id}/status", dependencies=[Depends(require_token)])
async def update_status(lead_id: int, request: Request):
    body = await request.json()
    status = body.get("status", "new")
    if status not in {"new", "contacted", "won", "lost"}:
        raise HTTPException(status_code=400, detail="invalid status")
    with db.connect() as conn:
        db.execute(conn, "UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
        conn.commit()
    return {"ok": True}


@app.delete("/leads/{lead_id}", dependencies=[Depends(require_token)])
def delete_lead(lead_id: int):
    with db.connect() as conn:
        db.execute(conn, "DELETE FROM leads WHERE id = ?", (lead_id,))
        conn.commit()
    return {"ok": True}


@app.get("/api/leads", dependencies=[Depends(require_token)])
def list_leads():
    with db.connect() as conn:
        rows = db.all_rows(conn, "SELECT * FROM leads ORDER BY id DESC LIMIT 100")
    return {"leads": rows}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "mode": current_mode(),
        "database": db.backend(),
        "time": datetime.now(timezone.utc).isoformat(),
    }
