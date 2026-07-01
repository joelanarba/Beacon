# Beacon — Resilient Emergency Coordination Backend

Beacon is a resilient, multi-channel emergency coordination system designed to maintain service reliability across a connectivity gradient. In critical situations where mobile data connections are unavailable or drop, Beacon utilizes GSM signaling (SMS and USSD) to ensure emergency incident reports are successfully ingested, prioritized, and dispatched to responders and healthcare facilities.

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

* **Multi-Channel Ingestion & Normalization**: Normalizes inputs ranging from rich WebSocket payloads (GPS coordinates, image URLs) to basic USSD callback sequences and keyword-parsed SMS text into a unified `IncidentEvent` model.
* **Triage Severity Queuing**: Utilizes **RabbitMQ priority queues** (via `aio-pika`) to ensure critical, life-threatening incidents (e.g., cardiac arrest) automatically bypass standard tickets.
* **Geospatial Responder Matching**: Employs **Redis GEO** commands for low-latency, real-time proximity lookups to match the closest available responder of the required type.
* **Hospital Capacity Locking**: Checks capabilities and reserves bed space in **PostgreSQL** (using SQLAlchemy 2.0 and asyncpg) under database transactions designed to prevent double-booking under high concurrency.
* **Resilient Outbound Notifications**: Directs dispatch alerts to responders through whichever channel they can reach (WebSocket push for mobile apps, or SMS for basic GSM feature phones).
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
Generate database migrations and apply them to construct the schema, then seed the database with mock responders, hospitals, and dispatch credentials:
```bash
docker compose exec app alembic revision --autogenerate -m "initial schema"
docker compose exec app alembic upgrade head
docker compose exec app python seed.py
```

### 4. Run Tests
Verify the entire backend functionality and priority dispatch queue logic:
```bash
docker compose exec app pytest -q
```

---

## Interactive Simulation Dashboard

To test the system without a live carrier gateway, open the simulator console in your browser:
👉 **[http://localhost:8000/sim/](http://localhost:8000/sim/)**

The dashboard provides four panels to simulate realistic user and responder flows:
* **USSD Dialer**: Simulates dialing `*920#` on a feature phone and walking through the menu state machine.
* **Inbound SMS**: Sends custom SMS alerts (e.g., `MED collapsed at Osu`) to verify keyword-based ingestion.
* **Dispatcher Feed**: Displays live assignments and status transitions via WebSockets.
* **Outbound SMS**: Logs outgoing SMS alerts sent to responders who do not have internet connectivity.

### Port & Credential Reference
* **API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
* **Grafana Dashboard**: [http://localhost:3000](http://localhost:3000) (Credentials: `admin` / `admin`)
* **RabbitMQ Console**: [http://localhost:15672](http://localhost:15672) (Credentials: `beacon` / `beacon`)
* **Seeded Dispatcher**: `dispatcher@beacon.local` / `beacon-dispatch`

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
