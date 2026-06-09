# Beacon

Beacon is an emergency-response coordination backend I built to keep working when
the network is falling apart. The idea started from one observation: when mobile
data drops, most apps are useless, but USSD and SMS keep working because they ride
the GSM signaling channel instead of data. Someone on a basic feature phone with
one bar of signal can still report an emergency, so the system behind them should
still be able to coordinate a response.

I'm a computer science student at the University of Cape Coast in Ghana, and I
wanted a backend project grounded in a real constraint here instead of another
generic CRUD app. The part I find interesting isn't any single feature. It's that
the same incident can arrive over three very different channels and nothing
downstream has to care which one it came from.

## The idea

Connectivity isn't on or off, it's a gradient. Beacon accepts reports across that
whole range and keeps as much capability as the channel can give it:

| Channel | Connectivity | What it looks like | What it can capture |
|---------|--------------|--------------------|---------------------|
| Web / mobile app | mobile data | real-time, two-way (WebSocket) | everything: GPS, media, live status |
| USSD | GSM signaling | a menu session (`*920#`) | structured: the menu walk builds the report |
| SMS | GSM signaling | one store-and-forward text | minimal: a keyword plus free text |

Each channel is normalised into a single `IncidentEvent`. An app report carries
GPS and a photo URL; a USSD report carries a coarse area picked from the menu; an
SMS report might only have a keyword and a sentence. Those differences show up as
fields that are present or missing on the event, never as separate branches later
on. The dispatch engine only ever sees `IncidentEvent` and treats them all the
same.

USSD and SMS are simulated locally so I don't need a paid provider account, but
the simulator speaks the real Africa's Talking callback format (`sessionId`,
`phoneNumber`, `text` that accumulates menu choices as `"1*2*3"`, replies prefixed
`CON ` to continue or `END ` to finish). Swapping in a real provider later is a
drop-in change, nothing else moves.

## How it works

```
app / USSD / SMS
      |  each adapter normalises its input into one IncidentEvent
      v
Ingestion API (FastAPI)  -> validate, classify severity, save (REPORTED)
      |  publish incident.reported (fire-and-forget, off the request path)
      v
RabbitMQ  -> topic exchange, priority queues keyed to triage severity
      |
      v
Dispatch engine
   - triage: severity -> queue priority, type -> responder kind
   - responder match: Redis GEO, nearest AVAILABLE, expanding radius
   - hospital match: Postgres, capacity + specialty, bed reserved
   - write the Assignment, emit incident.assigned
      |
      v
Notification: reach each party on a channel they can actually use
   - dispatchers / app responders -> WebSocket
   - feature-phone responders     -> SMS (recorded in a sink for the demo)

Postgres: incidents, responders, hospitals, assignments, event log
Redis:    responder GEO positions, USSD session state, the SMS-out sink
Prometheus + Grafana for metrics
```

A report comes in through the FastAPI ingestion layer, which validates it, saves
the incident, and publishes an event to RabbitMQ without making the reporter wait
on dispatch. I picked RabbitMQ mainly for its priority queues: triage severity
maps straight onto message priority, so a cardiac arrest is pulled off the queue
before a minor injury that was already waiting (there's a test that proves this).
Kafka would have been overkill here; its strength is high-throughput replay, which
I list as a possible extension rather than something this needs.

The dispatch engine consumes those events, finds the nearest available responder
with Redis GEO, reserves a hospital bed for medical cases, writes the assignment,
and emits an `incident.assigned` event. Matching locks the responder row it claims
so two incidents arriving at once can't grab the same ambulance. The notification
service then fans the assignment out on whatever channel each party can reach: a
live WebSocket push for dispatchers, an SMS for a responder carrying a feature
phone. Every state change is written to an append-only event log.

## Stack

- FastAPI (async Python) for the API and WebSockets
- RabbitMQ via aio-pika for the priority event bus
- PostgreSQL with SQLAlchemy 2.0 async + asyncpg, migrations with Alembic
- Redis for responder geo positions, USSD session state, and the SMS sink
- JWT auth with access + refresh-token rotation
- Prometheus and Grafana for metrics
- structlog for JSON logging, pydantic-settings for config
- Docker Compose to run the whole thing

## Running it

You need Docker. A GitHub Codespace is the easiest way since it comes with Docker
and the right Python; local Docker works the same.

```bash
cp .env.example .env          # then set SECRET_KEY to something random
docker compose up -d --build  # postgres, redis, rabbitmq, app, prometheus, grafana

docker compose exec app alembic revision --autogenerate -m "initial schema"
docker compose exec app alembic upgrade head
docker compose exec app python seed.py   # demo responders, hospitals, a dispatcher
```

Then:

- Demo console: http://localhost:8000/sim/
- API docs: http://localhost:8000/docs
- Grafana: http://localhost:3000 (admin / admin), the "Beacon" dashboard
- RabbitMQ: http://localhost:15672 (beacon / beacon)

The seeded dispatcher login is `dispatcher@beacon.local` / `beacon-dispatch`.

Run the tests with `docker compose exec app pytest -q`. They cover the channel
normalisation, the USSD menu walk including abandonment and timeout, SMS parsing,
the priority ordering, responder and hospital matching, the no-responder case, and
the notification fan-out.

## The demo

Open the console at `/sim/`. It's one page with four panes. Dial `*920#` on the
USSD pane as if you were on a feature phone with no data, and walk the menu:
pick a type, type a location, confirm. The incident gets saved, published at a
priority matching its severity, and dispatched to the nearest available responder.
The dispatcher feed on the right shows the assignment arrive live over WebSocket,
and if the responder is on a feature phone the outbound SMS pane shows exactly what
they would have been texted. Sending an SMS like `MED collapsed at Osu` runs the
same pipeline. The Grafana dashboard shows incidents by channel, dispatch latency,
available responders, and notification outcomes as you go.

Demo video: _link coming soon_

## What I'd do next

- Replay the full timeline of an incident from the event log (this is where Kafka
  would earn its place).
- Real geocoding instead of the small area lookup I use to place USSD/SMS reports.
- Predict severity from the report text.
- Wire the simulator to a real Africa's Talking account.
- Broadcast WebSocket events across multiple workers with Redis pub/sub.

## License

MIT, see [LICENSE](LICENSE).
