"""Upgrade legacy data without silently repairing or deleting inconsistent rows."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from events.schemas import IncidentReported
from models.db import Assignment, EventOutbox, Hospital


async def migrate(connection, direction, revision):
    def run(sync_connection):
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        config.set_main_option(
            "script_location", str(Path(__file__).parents[1] / "alembic")
        )
        config.attributes["connection"] = sync_connection
        getattr(command, direction)(config, revision)

    await connection.run_sync(run)


async def test_upgrade_queues_legacy_reports_and_preserves_released_capacity(
    test_engine, session_factory
):
    ids = {name: str(uuid.uuid4()) for name in ("reported", "resolved", "r", "h", "a")}
    async with test_engine.begin() as connection:
        # This schema was created only for this test and contains no application data.
        await migrate(connection, "downgrade", "20260701_0001")
        for status in ("reported", "resolved"):
            await connection.execute(
                text(
                    """
                INSERT INTO incidents
                    (id, reporter_contact, emergency_type, severity, status,
                     source_channel, description)
                VALUES (:id, 'test', 'FIRE', 'CRITICAL', :status, 'APP', 'legacy')
            """
                ),
                {"id": ids[status], "status": status.upper()},
            )
        await connection.execute(
            text(
                """
            INSERT INTO hospitals
                (id, name, latitude, longitude, total_capacity,
                 available_capacity, specialties)
            VALUES (:h, 'legacy', 0, 0, 1, 1, ARRAY['TRAUMA'])
        """
            ),
            {"h": ids["h"]},
        )
        await connection.execute(
            text(
                """
            INSERT INTO responders (id, name, type, status, contact, reachable_channel)
            VALUES (:r, 'legacy', 'FIRE', 'AVAILABLE', 'test', 'SMS')
        """
            ),
            {"r": ids["r"]},
        )
        await connection.execute(
            text(
                """
            INSERT INTO assignments (id, incident_id, responder_id, hospital_id)
            VALUES (:a, :resolved, :r, :h)
        """
            ),
            ids,
        )
        await migrate(connection, "upgrade", "head")

    async with session_factory() as db:
        event = (await db.execute(select(EventOutbox))).scalar_one()
        payload = IncidentReported.model_validate(event.payload)
        assert payload.incident_id == ids["reported"]
        assert event.published_at is None
        assert event.priority == 10
        assert (await db.get(Assignment, ids["a"])).released_at is not None
        assert (await db.get(Hospital, ids["h"])).available_capacity == 1


async def test_upgrade_refuses_invalid_legacy_capacity_without_repair(test_engine):
    hospital_id = str(uuid.uuid4())
    async with test_engine.begin() as connection:
        await migrate(connection, "downgrade", "20260701_0001")
        await connection.execute(
            text(
                """
            INSERT INTO hospitals
                (id, name, latitude, longitude, total_capacity,
                 available_capacity, specialties)
            VALUES (:id, 'invalid legacy', 0, 0, 1, 2, ARRAY['TRAUMA'])
        """
            ),
            {"id": hospital_id},
        )

    with pytest.raises(IntegrityError):
        async with test_engine.begin() as connection:
            await migrate(connection, "upgrade", "head")
    async with test_engine.connect() as connection:
        available = await connection.scalar(
            text("SELECT available_capacity FROM hospitals WHERE id = :id"),
            {"id": hospital_id},
        )
        assert available == 2
        assert await connection.scalar(
            text("SELECT version_num FROM alembic_version")
        ) == ("20260701_0001")
