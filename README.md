# Beacon — Resilient Emergency Coordination Backend

Beacon is an emergency-coordination backend prototype. It normalizes app, SMS, and USSD reports, processes dispatch through RabbitMQ, and stores incidents and assignments in PostgreSQL. SMS and USSD use simulated provider callbacks; outbound SMS is recorded in a Redis simulator sink. Live telecom integration and production operation have not been verified.

Incident acceptance, assignment, and lifecycle updates now use a transactional outbox. Database constraints and row locks guard duplicate assignments and resource release. Delivery is subject to the guarantees and remaining limitations in [docs/RELIABILITY.md](docs/RELIABILITY.md); no exactly-once delivery or measured performance claim is made.

---

## Architecture Overview

Beacon's ingestion pipeline normalizes inbound reports from multiple channels into a unified event structure, decoupling ingestion logic from the downstream dispatch and notification engine.

```
[Web/Mobile App]   [USSD Menu (*920#)]   [SMS (e.g. "MED collapsed")]
       │                    │                     │
       └──────────┬─────────┴─────────────────────┘
                  ▼
         [FastAPI Ingestion] (Input normalization & validation)
                  │
                  ▼
         [RabbitMQ Bus] (Priority-ordered queues based on severity)
                  │
                  ▼
         [Dispatch Engine]
          ├── Redis GEO (Nearest available responder query)
          └── Postgres (Hospital specialty & bed capacity check)
                  │
                  ▼
         [Notification Engine] ──> WebSocket (App) or SMS (Feature phone)
```

### Core Features

* **Multi-Channel Ingestion & Normalization**: Normalizes HTTP app payloads (GPS coordinates, image URLs), simulated USSD callback sequences, and keyword-parsed SMS into a unified `IncidentEvent` model.
* **Severity Queuing**: Sets RabbitMQ message priorities from the reported severity. Priority applies to messages waiting in the broker; prefetch and processing concurrency affect observed order. SMS/USSD use type-based defaults, not clinical text classification.
* **Geospatial Responder Matching**: Employs **Redis GEO** commands for low-latency, real-time proximity lookups to match the closest available responder of the required type.
* **Hospital Capacity Locking**: Checks capabilities and reserves bed space in **PostgreSQL** (using SQLAlchemy 2.0 and asyncpg) under database transactions designed to prevent double-booking under high concurrency.
* **Outbound Notification Simulation**: Publishes dispatcher updates through a local WebSocket hub and records simulated SMS. SMS exceptions use durable retry/dead-letter storage. App responder delivery, reconnect replay, and multi-instance WebSocket fan-out remain incomplete.
* **Local Provider Simulator**: Includes a mock gateway integration that mimics the Africa's Talking callback contract, allowing local testing of USSD session navigation (`*920#`) and SMS handling.

---

## Tech Stack

* **API & Core Logic**: FastAPI (Asynchronous Python)
* **Message Broker**: RabbitMQ (using native priority queues)
* **Database**: PostgreSQL (SQLAlchemy 2.0 Async + Alembic)
* **Cache & Geo-indexing**: Redis (GEO commands & USSD state machine)
* **Auth**: JWT (with access and refresh token rotation)
* **Observability**: Prometheus & Grafana (real-time metrics dashboard)
* **Containerization**: Docker & Docker Compose

---

## Getting Started

### Prerequisites
* Docker and Docker Compose

### 1. Environment Setup
Clone the repository and copy the example environment configuration:
```bash
cp .env.example .env
```
*(Open `.env` and set a secure, random string for `SECRET_KEY`)*

### 2. Boot Services
Spin up the PostgreSQL, Redis, RabbitMQ, FastAPI, Prometheus, and Grafana containers:
```bash
docker compose up -d --build
```

### 3. Initialize Database & Seed Data
Apply the versioned migrations, then seed mock responders, hospitals, and local dispatcher credentials:
```bash
docker compose exec app alembic upgrade head
docker compose exec app python seed.py
```

### 4. Run Tests
For automated disposable PostgreSQL, Redis, and RabbitMQ tests in Codespaces,
see [the real-service test guide](docs/INTEGRATION_TESTING.md):
```bash
.venv-integration/bin/python scripts/run_integration.py
```

Run tests against explicitly configured disposable services as described in [docs/RELIABILITY.md](docs/RELIABILITY.md). With no test-service URLs configured, service-dependent tests are skipped:
```bash
docker compose exec app pytest -q
```

---

## Interactive Simulation Dashboard

To test the system without a live carrier gateway, open the simulator console in your browser:
**[http://localhost:8000/sim/](http://localhost:8000/sim/)**

The dashboard provides four panels to simulate realistic user and responder flows:
* **USSD Dialer**: Simulates dialing `*920#` on a feature phone and walking through the menu state machine.
* **Inbound SMS**: Sends custom SMS alerts (e.g., `MED collapsed at Osu`) to verify keyword-based ingestion.
* **Dispatcher Feed**: Displays live assignments and status transitions via WebSockets.
* **Outbound SMS**: Logs outgoing SMS alerts sent to responders who do not have internet connectivity.

### Port & Credential Reference
* **API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
* **Grafana Dashboard**: [http://localhost:3000](http://localhost:3000) (Credentials: `admin` / `admin`)
* **RabbitMQ Console**: [http://localhost:15672](http://localhost:15672) (Credentials: `beacon` / `beacon`)
* **Seeded Dispatcher**: `dispatcher@example.com` / `beacon-dispatch` (local demonstration only)

Only the app port is published by the tracked Compose file. Grafana and RabbitMQ console URLs require an explicit local port override; an ignored override on one developer's machine is not part of a clean checkout.

---

## AWS Pilot Deployment

For a controlled production pilot on AWS, use the Terraform and deployment scripts under `infra/aws` and `scripts/aws`. The full operational runbook is in [docs/production/aws.md](docs/production/aws.md).

---

## Project Roadmap

Future enhancements for the platform include:
* **Incident Timeline Replay**: Implementing event sourcing / append-only replay logs to reconstruct incident timelines for post-event audit.
* **Geocoding API Integration**: Integrating an external geocoding API to parse natural-language SMS/USSD locations into GPS coordinates.
* **Production Gateway Hook**: Directing incoming webhooks to live telecom gateway sandboxes.
* **Horizontal WebSocket Scaling**: Integrating Redis Pub/Sub to sync WebSocket events across multiple backend app instances.

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
