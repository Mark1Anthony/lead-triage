"""Classifier logic. Pure functions, no network, no database."""

import pytest

from triage import (
    LeadInput,
    _demo_classify,
    _has_hot_signal,
    _seeded_random,
    classify_lead,
    current_mode,
)


def lead(message: str, company: str = "Nord Capital", email: str = "a@b.de") -> LeadInput:
    return LeadInput(
        name="Sarah Lang",
        company=company,
        email=email,
        source="website",
        message=message,
    )


class TestPriority:
    def test_hot_on_clear_buying_signal(self):
        result = _demo_classify(lead("Budget is approved, we need this live before Q2."))
        assert result.priority == "hot"

    def test_warm_on_exploratory_message(self):
        result = _demo_classify(lead("Interested in a demo, comparing a few providers."))
        assert result.priority == "warm"

    def test_cold_on_vague_message(self):
        result = _demo_classify(lead("Just looking around."))
        assert result.priority == "cold"


class TestNegation:
    def test_negated_keyword_is_not_hot(self):
        assert _has_hot_signal("this is not urgent at all") is False

    def test_every_negation_counts(self):
        assert _has_hot_signal("no budget, no deadline, just looking") is False

    def test_second_occurrence_is_found_when_first_is_negated(self):
        # Regression: list.index() only returned the first position, so this
        # was classified cold despite being a clear buying signal.
        text = "no budget last year, but budget is approved now"
        assert _has_hot_signal(text) is True
        assert _demo_classify(lead(text)).priority == "hot"

    def test_unnegated_keyword_after_a_negated_one(self):
        assert _has_hot_signal("we have no deadline but the contract is ready") is True

    def test_partial_words_do_not_match(self):
        # "urgent" must not fire inside another word.
        assert _has_hot_signal("we discussed detergents") is False


class TestSeededRandom:
    def test_same_seed_gives_same_sequence(self):
        a = _seeded_random("Nord Capital" + "sarah@nordcapital.de")
        b = _seeded_random("Nord Capital" + "sarah@nordcapital.de")
        assert [a.random() for _ in range(5)] == [b.random() for _ in range(5)]

    def test_different_seeds_differ(self):
        a = _seeded_random("Nord Capital" + "sarah@nordcapital.de")
        b = _seeded_random("Kurz Retail" + "petra@kurzretail.de")
        assert [a.random() for _ in range(5)] != [b.random() for _ in range(5)]

    def test_same_company_and_email_give_same_category(self):
        first = _demo_classify(lead("Just looking", "Studio Weiss", "maria@studioweiss.com"))
        second = _demo_classify(lead("Different text entirely", "Studio Weiss", "maria@studioweiss.com"))
        assert first.category == second.category


class TestMode:
    def test_falls_back_to_demo_without_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("LEAD_TRIAGE_MODE", "live")
        # No key means demo, whatever the mode variable says.
        assert current_mode() == "demo"

    def test_demo_mode_by_default(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("LEAD_TRIAGE_MODE", raising=False)
        assert current_mode() == "demo"

    def test_classify_lead_reports_demo_mode(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert classify_lead(lead("Budget approved")).mode == "demo"


class TestClassificationShape:
    @pytest.mark.parametrize(
        "message",
        ["Budget approved, urgent", "Interested in a demo", "Just browsing"],
    )
    def test_every_field_is_populated(self, message):
        result = _demo_classify(lead(message))
        assert result.priority in {"hot", "warm", "cold"}
        assert result.category
        assert result.next_action
        assert result.summary
        assert result.reasoning
        assert result.mode == "demo"
