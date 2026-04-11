"""
Lead triage — classify an incoming lead into priority + category,
suggest a next action and generate a short summary.

Two modes:
    - live: calls the OpenAI API (requires OPENAI_API_KEY env var)
    - demo: returns deterministic, plausible mock results — no API key, no cost,
            safe for the public demo. Mode is controlled by the LEAD_TRIAGE_MODE
            environment variable. Defaults to "demo".

The public function is `classify_lead(...)`. The rest of the module is either
the OpenAI client wiring or the mock logic.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from dataclasses import dataclass


# ─── Public types ────────────────────────────────────────────────

@dataclass
class LeadInput:
    name: str
    company: str
    email: str
    source: str
    message: str


@dataclass
class Classification:
    priority: str        # "hot" | "warm" | "cold"
    category: str        # e.g. "SaaS", "Agency", "E-commerce"
    next_action: str     # one-line suggestion
    summary: str         # 1-2 sentence summary
    reasoning: str       # short explanation
    mode: str            # "live" or "demo"


# ─── Mode detection ──────────────────────────────────────────────

def current_mode() -> str:
    # If no key is set, force demo mode — this is the public-safe default.
    if not os.getenv("OPENAI_API_KEY"):
        return "demo"
    return os.getenv("LEAD_TRIAGE_MODE", "demo").lower()


# ─── Live mode (OpenAI) ──────────────────────────────────────────

SYSTEM_PROMPT = """You are a sales lead triage assistant. Given a new lead,
return a strict JSON object with these fields:

- priority: one of "hot", "warm", "cold"
- category: a short industry / segment label (1-3 words)
- next_action: a concrete, one-sentence next step for the sales team
- summary: a 1-2 sentence summary of what this lead is asking for
- reasoning: one sentence explaining why you picked this priority

Signals:
- HOT: clear budget, decision-maker, specific timeline or urgent pain point
- WARM: interested, has a real problem, but no strong buying signals yet
- COLD: vague, early-stage, info-gathering, or not a fit

Respond with only the JSON object, no prose, no code fences."""


def _live_classify(lead: LeadInput) -> Classification:
    from openai import OpenAI  # lazy import so demo mode works without the lib

    client = OpenAI()
    user_msg = (
        f"Name: {lead.name}\n"
        f"Company: {lead.company}\n"
        f"Email: {lead.email}\n"
        f"Source: {lead.source}\n"
        f"Message: {lead.message}"
    )
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    content = resp.choices[0].message.content or "{}"
    data = json.loads(content)
    return Classification(
        priority=(data.get("priority") or "warm").lower(),
        category=data.get("category") or "Unknown",
        next_action=data.get("next_action") or "Follow up by email.",
        summary=data.get("summary") or "",
        reasoning=data.get("reasoning") or "",
        mode="live",
    )


# ─── Demo mode (deterministic, no API) ───────────────────────────

HOT_KEYWORDS = [
    "urgent", "asap", "deadline", "budget", "ready", "decision", "sign",
    "contract", "go-live", "q1", "q2", "q3", "q4", "next week", "tomorrow",
]
WARM_KEYWORDS = [
    "interested", "exploring", "evaluating", "comparing", "pricing",
    "demo", "trial", "pilot", "roadmap",
]
CATEGORIES = ["SaaS", "Agency", "E-commerce", "FinTech", "Health", "Manufacturing", "Media", "Retail", "Consulting"]

NEXT_ACTIONS = {
    "hot":  "Call within 24h and send a calendar link.",
    "warm": "Send a tailored pitch + case study by email.",
    "cold": "Add to the nurture sequence and check back in 2 weeks.",
}


def _seeded_random(seed_str: str) -> random.Random:
    h = hashlib.sha256(seed_str.encode("utf-8")).digest()
    seed_int = int.from_bytes(h[:8], "big")
    return random.Random(seed_int)


def _contains_word(text: str, keyword: str) -> bool:
    """Whole-word / whole-phrase match (no partial hits like 'urgent' in 'not urgent')."""
    pattern = r"\b" + re.escape(keyword) + r"\b"
    return re.search(pattern, text) is not None


NEGATIONS = {"not", "no", "without", "never"}


def _has_hot_signal(text: str) -> bool:
    """Check for hot keywords that aren't preceded by a negation."""
    tokens = re.findall(r"[a-z0-9]+", text)
    token_set = set(tokens)

    for keyword in HOT_KEYWORDS:
        if " " in keyword:
            # Multi-word phrase (e.g. "next week")
            if keyword in text:
                return True
            continue
        if keyword not in token_set:
            continue
        # Single-word keyword found — check that the preceding token isn't a negation
        idx = tokens.index(keyword)
        if idx > 0 and tokens[idx - 1] in NEGATIONS:
            continue
        return True
    return False


def _has_warm_signal(text: str) -> bool:
    return any(_contains_word(text, k) for k in WARM_KEYWORDS)


def _demo_classify(lead: LeadInput) -> Classification:
    text = f"{lead.company} {lead.message}".lower()

    if _has_hot_signal(text):
        priority = "hot"
    elif _has_warm_signal(text) or len(lead.message) > 120:
        priority = "warm"
    else:
        priority = "cold"

    rng = _seeded_random(lead.company + lead.email)
    category = rng.choice(CATEGORIES)

    if priority == "hot":
        reasoning = "Message contains urgency or buying signals."
        summary = (
            f"{lead.name} at {lead.company} is actively looking for a solution "
            f"and signals a clear intent to move forward."
        )
    elif priority == "warm":
        reasoning = "Interested and exploratory, but no hard buying signal yet."
        summary = (
            f"{lead.name} at {lead.company} is evaluating options and wants more "
            f"information before deciding."
        )
    else:
        reasoning = "Early-stage, vague or information-gathering lead."
        summary = (
            f"{lead.name} at {lead.company} is in an early discovery phase."
        )

    return Classification(
        priority=priority,
        category=category,
        next_action=NEXT_ACTIONS[priority],
        summary=summary,
        reasoning=reasoning,
        mode="demo",
    )


# ─── Public API ──────────────────────────────────────────────────

def classify_lead(lead: LeadInput) -> Classification:
    """Classify a lead using whichever mode is currently active."""
    mode = current_mode()
    if mode == "live":
        try:
            return _live_classify(lead)
        except Exception as e:
            # Fall back to demo mode on any error so the UI never breaks.
            result = _demo_classify(lead)
            result.reasoning = f"(live call failed: {e.__class__.__name__}) " + result.reasoning
            return result
    return _demo_classify(lead)
