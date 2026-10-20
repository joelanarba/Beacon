"""Build once, but give every real-service test its own volumes and database."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from integration_tests.stack import ROOT, DockerRun, Stack


@pytest.fixture(scope="session")
def docker_run():
    if sys.version_info[:2] != (3, 11):
        pytest.fail(
            "Real-service tests require Python 3.11; use the Codespaces dev container"
        )
    artifacts = Path(
        os.environ.get(
            "BEACON_INTEGRATION_ARTIFACTS",
            str(ROOT / "artifacts" / "integration" / uuid.uuid4().hex),
        )
    ).resolve()
    run = DockerRun(artifacts)
    run.preflight()
    run.build()
    print(f"Integration evidence: {artifacts}")
    return run


@pytest_asyncio.fixture
async def stack(docker_run, request):
    options = getattr(request, "param", {})
    instance = Stack(docker_run, **options)
    docker_run.record({"test": request.node.nodeid, "project": instance.project})
    try:
        await instance.prepare()
        yield instance
    finally:
        # Capture failure state before deleting only this project's resources.
        try:
            await instance.capture()
        finally:
            instance.cleanup()
