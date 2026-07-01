"""initial schema

Revision ID: 20260701_0001
Revises:
Create Date: 2026-07-01 11:35:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260701_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hospitals",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("total_capacity", sa.Integer(), nullable=False),
        sa.Column("available_capacity", sa.Integer(), nullable=False),
        sa.Column("specialties", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "incidents",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("reporter_contact", sa.String(length=64), nullable=False),
        sa.Column("emergency_type", sa.String(length=20), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_channel", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("area_label", sa.String(length=255), nullable=True),
        sa.Column("media_url", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "emergency_type IN ('MEDICAL', 'FIRE', 'SECURITY', 'OTHER')",
            name="ck_incidents_emergency_type",
        ),
        sa.CheckConstraint(
            "severity IN ('CRITICAL', 'URGENT', 'STANDARD')",
            name="ck_incidents_severity",
        ),
        sa.CheckConstraint(
            "status IN ('REPORTED', 'ASSIGNED', 'EN_ROUTE', 'RESOLVED')",
            name="ck_incidents_status",
        ),
        sa.CheckConstraint(
            "source_channel IN ('APP', 'USSD', 'SMS')",
            name="ck_incidents_source_channel",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "responders",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("contact", sa.String(length=64), nullable=False),
        sa.Column("reachable_channel", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "type IN ('AMBULANCE', 'FIRE', 'POLICE', 'VOLUNTEER')",
            name="ck_responders_type",
        ),
        sa.CheckConstraint(
            "status IN ('AVAILABLE', 'ASSIGNED', 'OFFLINE')",
            name="ck_responders_status",
        ),
        sa.CheckConstraint(
            "reachable_channel IN ('APP', 'SMS')",
            name="ck_responders_reachable_channel",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "event_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("incident_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_event_log_created_at"), "event_log", ["created_at"])
    op.create_index(op.f("ix_event_log_event_type"), "event_log", ["event_type"])
    op.create_index(op.f("ix_event_log_incident_id"), "event_log", ["incident_id"])

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=128), nullable=True),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('DISPATCHER', 'ADMIN')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "assignments",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("incident_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("responder_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("hospital_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["hospital_id"], ["hospitals.id"]),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"]),
        sa.ForeignKeyConstraint(["responder_id"], ["responders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_assignments_incident_id"), "assignments", ["incident_id"])
    op.create_index(
        op.f("ix_assignments_responder_id"), "assignments", ["responder_id"]
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("jti", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index(op.f("ix_assignments_responder_id"), table_name="assignments")
    op.drop_index(op.f("ix_assignments_incident_id"), table_name="assignments")
    op.drop_table("assignments")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_event_log_incident_id"), table_name="event_log")
    op.drop_index(op.f("ix_event_log_event_type"), table_name="event_log")
    op.drop_index(op.f("ix_event_log_created_at"), table_name="event_log")
    op.drop_table("event_log")
    op.drop_table("responders")
    op.drop_table("incidents")
    op.drop_table("hospitals")
