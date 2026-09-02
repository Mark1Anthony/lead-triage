# Lead Triage

A small CRM-style lead intake tool. Leads come in via form or webhook, get classified
into **hot / warm / cold** with an industry category and a suggested next action, and
the dashboard shows everything as a kanban board.

Classification runs one of two ways: with `OPENAI_API_KEY` set, a GPT model does it.
Without a key the app falls back to a **deterministic keyword classifier** — no API
calls, no cost, no surprises. That is the default, so running this without any
configuration gives you keyword matching, not a model.

Built with FastAPI. Storage is SQLite by default, Postgres when `DATABASE_URL`
points at one, and DynamoDB when it runs on AWS Lambda — same application code
in all three cases.

![Schematic view of the dashboard](docs/screenshot.svg)

*The image above is a drawn schematic, not a real screenshot.*

## Live demo

**https://lead-triage-31jo.onrender.com**

Runs in demo mode on Render's free plan, backed by a Postgres instance — the
dashboard shows five seeded leads, and the form at the bottom of the page
creates real ones. Classification there is the keyword algorithm, not a model,
so nothing costs anything and the result is the same on every run.

Two things to expect from the free plan: the service sleeps after a while, so
the first request can take up to a minute to wake it, and the free database
expires 30 days after it is created — this one on 29 September 2026. When it
goes, the demo goes with it until a new one is provisioned.

Anything that writes beyond that public form is closed. Without `X-Api-Token`
the write endpoints answer 401 — see [Authentication](#authentication).

## Features

- **Form intake** — add leads from a simple web form
- **Webhook intake** — POST JSON to `/webhook` for external systems
- **GPT classification** — priority, category, summary, next action, reasoning
- **Kanban dashboard** — hot / warm / cold columns, colour-coded cards
- **Three storage backends** — SQLite by default, Postgres via `DATABASE_URL`,
  DynamoDB via `DYNAMODB_TABLE`
- **Dual mode** — `live` (OpenAI) or `demo` (mock). Falls back to demo on errors.
- **Mark as won / lost / delete** — basic lifecycle
- **5 example leads** — seeded on first run so the board isn't empty

## Tech stack

- **Python 3.10+**
- **FastAPI** + **Uvicorn**
- **OpenAI SDK** (v1.x, `gpt-4o-mini` by default)
- **SQLite** (stdlib), **Postgres** (psycopg 3) or **DynamoDB** (boto3)
- **Mangum** on AWS Lambda, behind an HTTP API Gateway
- **Jinja2** templates

## Quick start

```bash
git clone https://github.com/Mark1Anthony/lead-triage.git
cd lead-triage

python3 -m venv .venv
source .venv/bin/activate       # macOS / Linux
# .venv\Scripts\activate        # Windows

pip install -r requirements.txt

uvicorn app:app --reload
```

Open http://127.0.0.1:8000 and start adding leads.

### Enable live mode (real OpenAI calls)

```bash
export OPENAI_API_KEY=sk-...
export LEAD_TRIAGE_MODE=live   # optional, defaults to demo even if key is set
uvicorn app:app --reload
```

The badge in the top right of the dashboard shows the current mode.

### Run with Docker

```bash
docker compose up --build
```

Two containers: the app and a Postgres 18 database — the same major
version the deployment runs, so local behaviour matches it. Dashboard on
http://127.0.0.1:8000 once both are up.

No configuration needed for a first look — without `LEAD_TRIAGE_TOKEN` the app
generates one and logs it, and the public form works without a token anyway. For
a stable token and a real database password, copy `.env.example` to `.env` and
fill it in before starting; compose reads that file automatically.

The credentials you see in this repository — `leads:leads` in the compose file,
the CI workflow and the examples below — are placeholders for a database that
only exists inside the compose network. They are not secrets, and secret
scanners will occasionally flag them anyway. The two values that are real,
`LEAD_TRIAGE_TOKEN` and `OPENAI_API_KEY`, appear nowhere in the repository: they
come from `.env` locally and from the platform's own settings in deployment.

**Why two containers.** The app talks to Postgres over the compose network at
the hostname `db` — that is the service name, nothing else configures it.
Postgres takes a few seconds longer to accept connections than to start its
container, and the app creates its schema during startup, so the app waits for
`service_healthy` rather than for the container to exist. The health condition
is `pg_isready`, which is what the database itself uses to report readiness.

The database lives in a named volume, so leads survive a restart:

```bash
docker compose down          # stop, keep the data
docker compose down -v       # drop the volume too, database is empty again
```

Both containers restart unless stopped. The app runs as an unprivileged user,
and its health is polled through the app's own `/health` endpoint — so
`docker ps` shows `healthy` only once it really answers. Postgres publishes no
port to the host; only the app needs to reach it.

To check which backend is live:

```bash
curl -s localhost:8000/health
# {"status":"ok","mode":"demo","database":"postgres","time":"..."}
```

### Without Docker, against Postgres

`DATABASE_URL` is the only switch. Unset, the app uses SQLite at `data/leads.db`:

```bash
export DATABASE_URL=postgresql://leads:leads@localhost:5432/leads
uvicorn app:app --reload
```

The schema is created on startup either way — there is one table, so there are
no migrations to run.

## Routes

| Method | Path                       | Purpose                                    | Auth        |
|--------|----------------------------|--------------------------------------------|-------------|
| GET    | `/`                        | Dashboard (kanban + form)                  | public      |
| GET    | `/health`                  | Health check + current mode                | public      |
| POST   | `/demo-lead`               | Create lead from the public form           | rate limited |
| POST   | `/leads`                   | Create lead from form data                 | token       |
| POST   | `/webhook`                 | Create lead from JSON payload              | token       |
| POST   | `/leads/{id}/status`       | Mark as new / contacted / won / lost       | token       |
| DELETE | `/leads/{id}`              | Delete a lead                              | token       |
| GET    | `/api/leads`               | JSON list of all leads                     | token       |

## Authentication

This is built to be deployed publicly, so everything that writes to the database
— and `/api/leads`, which returns full records including email addresses —
requires a shared secret in the `X-Api-Token` header.

Set it via `LEAD_TRIAGE_TOKEN`. **If you don't, the app still runs**: it
generates a token at startup and prints it to the log, so a fresh deployment
works without configuration while the write endpoints stay closed to everyone
who cannot read that log. A generated token changes on every restart — set the
variable to keep it stable.

```bash
curl -X POST http://localhost:8000/leads \
  -H "X-Api-Token: $LEAD_TRIAGE_TOKEN" \
  -F "name=Sarah Lang" \
  -F "company=Nord Capital" \
  -F "email=sarah@nordcapital.de" \
  -F "message=Need a CRM live by Q2, budget approved."
```

Without a valid token:

```bash
curl -i -X DELETE http://localhost:8000/leads/1
# HTTP/1.1 401 Unauthorized
# {"detail":"invalid or missing X-Api-Token"}
```

The dashboard form does **not** carry the token — it posts to `/demo-lead`,
which can only create leads, never change or delete them. That endpoint is
limited to 5 submissions per IP per minute and carries a honeypot field, so a
public deployment stays usable without handing out write access.

### Webhook example

```bash
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -H "X-Api-Token: $LEAD_TRIAGE_TOKEN" \
  -d '{
    "name": "Sarah Lang",
    "company": "Nord Capital",
    "email": "sarah@nordcapital.de",
    "source": "webhook",
    "message": "Need a CRM live by Q2, budget approved."
  }'
```

## Demo mode vs live mode

| Aspect        | Demo                                               | Live                                 |
|---------------|----------------------------------------------------|--------------------------------------|
| Classification| Keyword-based, deterministic                       | GPT model via OpenAI API             |
| Needs API key | No                                                 | Yes (`OPENAI_API_KEY`)               |
| Cost          | Free                                               | ~$0.0001–0.001 per lead (gpt-4o-mini)|
| Latency       | <1ms                                               | 500–1500ms                           |
| Fallback      | —                                                  | Falls back to demo on errors         |

Demo mode is the default, so the app works out of the box and costs nothing.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

81 tests, no network and no cloud account required — they run against SQLite in
a temporary directory and force demo mode, so nothing reaches the OpenAI API.

To run the same tests against another backend:

```bash
export TEST_DATABASE_URL=postgresql://leads:leads@localhost:5432/leads
pytest

export TEST_DYNAMODB=1     # moto answers botocore in-process
pytest
```

The DynamoDB run needs no AWS account and touches no network — the code under
test is the code the deployed Lambda runs. `tests/test_lambda_handler.py` goes
one step further and invokes the handler with an API Gateway payload.

Every test then talks to Postgres, with the table dropped between tests so each
one starts clean. CI does both on each push, plus a third job that builds the
image, brings the compose stack up and checks that the running container serves
the dashboard, keeps the write endpoints closed and reports `"database":
"postgres"` on `/health`.

## Project structure

```
lead-triage/
├── README.md
├── LICENSE
├── requirements.txt        # Runtime dependencies
├── requirements-dev.txt    # + pytest, httpx and ruff
├── Dockerfile              # App image
├── docker-compose.yml      # App + Postgres
├── .dockerignore
├── .env.example
├── pytest.ini
├── render.yaml             # Render deployment (web service + Postgres)
├── app.py                  # FastAPI app + routes, logging setup
├── db.py                   # The three backends behind six functions
├── lambda_handler.py       # Mangum entry point for AWS Lambda
├── Dockerfile.lambda       # Lambda image, separate from the server image
├── requirements-lambda.txt # mangum + boto3, only for the AWS deployment
├── terraform/              # AWS: ECR, Lambda, API Gateway, DynamoDB, IAM
├── triage.py               # Classifier (live + demo)
├── security.py             # Token guard + IP rate limiter
├── templates/
│   └── index.html          # Dashboard UI
├── tests/
│   ├── test_triage.py      # Classifier logic
│   ├── test_db.py          # Dialect handling, no database needed
│   ├── test_dynamodb.py    # Id counter, reserved words, scan filtering
│   ├── test_lambda_handler.py  # Invoked with a real API Gateway payload
│   ├── test_logging.py     # Startup lines actually reach the log
│   └── test_api.py         # Endpoint access rules, runs on both backends
├── .github/workflows/
│   └── ci.yml              # Tests on both backends + container smoke test
└── docs/
    ├── AWS-ARCHITEKTUR.md  # Architecture, trade-offs, costs, teardown
    └── screenshot.svg      # Drawn schematic, not a capture
```

## Deployment & persistence

Which database is used comes down to one variable:

| Environment                       | Backend  | Where the data lives            |
|-----------------------------------|----------|---------------------------------|
| nothing set                       | SQLite   | `data/leads.db` next to the code |
| `DATABASE_URL=postgresql://…`     | Postgres | on that server                   |
| `DYNAMODB_TABLE=…`                | DynamoDB | in that table                    |

Nothing else changes — same routes, same tests, same code. `db.py` holds the
two differences that actually exist between the dialects: the placeholder style
(`?` vs `%s`) and how an auto-incrementing primary key is declared. There is one
table and no ORM, which is why this is a file and not a framework.

**Compose** sets `DATABASE_URL` to the `db` service and mounts a named volume,
so a local run keeps its data across restarts.

**Render** (`render.yaml`) is a Blueprint: applying it creates the web service
and a Postgres instance together and wires the connection string in through
`fromDatabase`, so no credential is ever typed in by hand or committed here.

That split is the whole point. Render's free plan gives the web service an
ephemeral filesystem, so the earlier SQLite setup lost every lead on each
redeploy and restart — the seed rows came back and everything else was gone.
With the database as its own service the data outlives the container.

Two limits of the free plan, worth knowing before relying on it:

- the web service sleeps after inactivity, so the first request afterwards can
  take up to a minute
- the free database expires 30 days after creation and is then deleted, unless
  it is moved to a paid plan

The live instance was deployed from a public Git URL rather than a connected
GitHub account, which keeps Render out of the account's permissions but also
means pushes do **not** redeploy it. Connect the repository in Render's
dashboard if you want automatic deploys.

**AWS** (`terraform/`) runs the same application as a Lambda container behind an
HTTP API Gateway, with DynamoDB underneath — everything in the tier that stays
free rather than the one that lasts twelve months, so an idle demo costs
nothing instead of costing less. The pipeline authenticates by OIDC, so no
access key exists to leak or expire.

That stack has **not been applied**. The Terraform is validated, linted and
security-scanned on every push, and the Lambda entry point and DynamoDB backend
are tested locally against moto — but nothing here has run against a real AWS
account. [`docs/AWS-ARCHITEKTUR.md`](docs/AWS-ARCHITEKTUR.md) has the
architecture, the trade-offs, the costs and the two commands to tear it down.

## Why I built this

While building a production CRM at TERO, I wrote a lot of lead-routing code by hand — if/else
chains and regex rules that started clean and got messier as the real world showed up. This
is the same idea but with an LLM doing the classification: it handles vague wording,
multilingual text and unexpected formats way better than a rule tree. The demo mode keeps
the fallback deterministic so nothing ever breaks when the API is down or the key is missing.

## License

MIT — see [LICENSE](LICENSE)
