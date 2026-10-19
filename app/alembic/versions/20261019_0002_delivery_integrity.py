"""Guard assignment integrity and persist publication/retry intent.

Revision ID: 20261019_0002
Revises: 20260701_0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261019_0002"
down_revision = "20260701_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("ingest_key", sa.String(64), nullable=True))
    op.add_column("incidents", sa.Column("request_hash", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_incidents_ingest_key", "incidents", ["ingest_key"])
    op.add_column(
        "assignments",
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Existing terminal assignments are historical. Do not alter capacity counts:
    # inconsistent legacy data must be repaired explicitly before constraints pass.
    op.execute(
        "UPDATE assignments AS a SET released_at = i.updated_at "
        "FROM incidents AS i WHERE i.id = a.incident_id AND i.status = 'RESOLVED'"
    )
    op.create_unique_constraint(
        "uq_assignment_incident", "assignments", ["incident_id"]
    )
    op.create_index(
        "uq_assignment_active_responder",
        "assignments",
        ["responder_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_check_constraint(
        "ck_hospitals_capacity",
        "hospitals",
        "total_capacity >= 0 AND available_capacity >= 0 "
        "AND available_capacity <= total_capacity",
    )
    op.create_table(
        "event_outbox",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("routing_key", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("delivery_attempt", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publish_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(128), nullable=True),
        sa.UniqueConstraint("event_id", "delivery_attempt", name="uq_outbox_delivery"),
        sa.CheckConstraint("delivery_attempt >= 0", name="ck_outbox_attempt"),
    )
    op.create_index(
        "ix_outbox_pending", "event_outbox", ["published_at", "available_at"]
    )
    op.create_table(
        "dead_letters",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("consumer", sa.String(32), nullable=False),
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("routing_key", sa.String(64), nullable=False),
        sa.Column("body", sa.LargeBinary(), nullable=False),
        sa.Column("delivery_attempt", sa.Integer(), nullable=False),
        sa.Column("error_type", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("consumer", "event_id", name="uq_dead_letter_event"),
    )
    # Recover previously accepted reports that never reached assignment. Replays
    # racing an existing broker delivery are safe because dispatch claims the row.
    op.execute(
        """
        INSERT INTO event_outbox
            (id, event_id, routing_key, payload, priority,
             delivery_attempt, publish_attempts)
        SELECT gen_random_uuid(), 'incident.reported:' || id::text,
            'incident.reported', jsonb_build_object(
                'incident_id', id::text, 'source_channel', source_channel,
                'emergency_type', emergency_type, 'severity', severity,
                'reporter_contact', reporter_contact, 'description', description,
                'latitude', latitude, 'longitude', longitude,
                'area_label', area_label, 'media_url', media_url,
                'reported_at', created_at),
            CASE severity WHEN 'CRITICAL' THEN 10 WHEN 'URGENT' THEN 5 ELSE 1 END,
            0, 0
        FROM incidents WHERE status = 'REPORTED'
    """
    )


def downgrade() -> None:
    op.drop_table("dead_letters")
    op.drop_index("ix_outbox_pending", table_name="event_outbox")
    op.drop_table("event_outbox")
    op.drop_constraint("ck_hospitals_capacity", "hospitals", type_="check")
    op.drop_index("uq_assignment_active_responder", table_name="assignments")
    op.drop_constraint("uq_assignment_incident", "assignments", type_="unique")
    op.drop_column("assignments", "released_at")
    op.drop_constraint("uq_incidents_ingest_key", "incidents", type_="unique")
    op.drop_column("incidents", "request_hash")
    op.drop_column("incidents", "ingest_key")
