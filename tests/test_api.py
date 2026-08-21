"""API surface: which endpoints are open, which need the shared secret."""

FORM = {
    "name": "Sarah Lang",
    "company": "Nord Capital",
    "email": "sarah@nordcapital.de",
    "message": "Budget approved, we need this live before Q2.",
}


class TestPublicEndpoints:
    def test_health_is_open(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["mode"] == "demo"

    def test_dashboard_is_open(self, client):
        assert client.get("/").status_code == 200

    def test_dashboard_shows_the_seeded_leads(self, client, auth):
        assert len(client.get("/api/leads", headers=auth).json()["leads"]) == 5


class TestTokenGuard:
    def test_create_lead_without_token_is_rejected(self, client):
        assert client.post("/leads", data=FORM).status_code == 401

    def test_create_lead_with_wrong_token_is_rejected(self, client):
        response = client.post("/leads", data=FORM, headers={"X-Api-Token": "wrong"})
        assert response.status_code == 401

    def test_create_lead_with_token_succeeds_and_persists(self, client, auth):
        before = len(client.get("/api/leads", headers=auth).json()["leads"])

        response = client.post("/leads", data=FORM, headers=auth)
        assert response.status_code == 200
        assert response.json()["classification"]["priority"] == "hot"

        leads = client.get("/api/leads", headers=auth).json()["leads"]
        assert len(leads) == before + 1
        assert leads[0]["name"] == "Sarah Lang"
        assert leads[0]["company"] == "Nord Capital"

    def test_delete_without_token_is_rejected(self, client):
        assert client.delete("/leads/1").status_code == 401

    def test_delete_with_token_succeeds(self, client, auth):
        before = len(client.get("/api/leads", headers=auth).json()["leads"])
        assert client.delete("/leads/1", headers=auth).status_code == 200
        assert len(client.get("/api/leads", headers=auth).json()["leads"]) == before - 1

    def test_status_update_without_token_is_rejected(self, client):
        response = client.post("/leads/1/status", json={"status": "won"})
        assert response.status_code == 401

    def test_status_update_with_token_succeeds(self, client, auth):
        assert client.post("/leads/1/status", json={"status": "won"}, headers=auth).status_code == 200

    def test_invalid_status_is_rejected(self, client, auth):
        response = client.post("/leads/1/status", json={"status": "nonsense"}, headers=auth)
        assert response.status_code == 400

    def test_webhook_without_token_is_rejected(self, client):
        response = client.post("/webhook", json={"name": "A", "company": "B", "message": "hi"})
        assert response.status_code == 401

    def test_webhook_with_token_succeeds(self, client, auth):
        response = client.post(
            "/webhook",
            json={"name": "A", "company": "B", "message": "Budget approved, urgent"},
            headers=auth,
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_lead_list_without_token_is_rejected(self, client):
        # Returns full records including email addresses.
        assert client.get("/api/leads").status_code == 401


class TestUnconfiguredServer:
    def test_write_endpoints_fail_closed_without_a_configured_token(self, client, monkeypatch):
        monkeypatch.delenv("LEAD_TRIAGE_TOKEN", raising=False)
        response = client.post("/leads", data=FORM, headers={"X-Api-Token": "anything"})
        assert response.status_code == 503


class TestDemoEndpoint:
    def test_accepts_submissions_without_a_token(self, client, auth):
        before = len(client.get("/api/leads", headers=auth).json()["leads"])
        assert client.post("/demo-lead", data=FORM).status_code == 200
        assert len(client.get("/api/leads", headers=auth).json()["leads"]) == before + 1

    def test_missing_fields_are_rejected(self, client):
        assert client.post("/demo-lead", data={"name": "A"}).status_code == 422

    def test_honeypot_silently_discards_the_submission(self, client, auth):
        before = len(client.get("/api/leads", headers=auth).json()["leads"])

        response = client.post("/demo-lead", data={**FORM, "website": "http://spam.example"})

        # Answers as if it worked - telling the bot would make it rename the field.
        assert response.status_code == 200
        assert len(client.get("/api/leads", headers=auth).json()["leads"]) == before

    def test_rate_limit_kicks_in_after_five_submissions(self, client):
        codes = [client.post("/demo-lead", data=FORM).status_code for _ in range(7)]
        assert codes == [200, 200, 200, 200, 200, 429, 429]

    def test_demo_endpoint_cannot_delete(self, client):
        # /demo-lead only accepts POST for creation; there is no token-free
        # path to change or remove a lead.
        assert client.delete("/demo-lead").status_code == 405
