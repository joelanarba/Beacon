"""Run the existing suite with all three disposable services explicitly supplied."""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

import pytest

from integration_tests.stack import PASSWORD, ROOT


@pytest.mark.integration
@pytest.mark.parametrize("stack", [{"seed": False}], indirect=True)
async def test_existing_regressions_with_real_services(stack):
    await stack.sql("CREATE DATABASE beacon_test_regression")
    env = os.environ.copy()
    env.update(
        BEACON_TEST_DATABASE_URL=(
            f"postgresql+asyncpg://beacon_test:{PASSWORD}"
            f"@127.0.0.1:{stack.ports['pg']}/beacon_test_regression"
        ),
        BEACON_TEST_REDIS_URL=f"redis://127.0.0.1:{stack.ports['redis']}/14",
        BEACON_TEST_AMQP_URL=(
            f"amqp://beacon_test:{PASSWORD}" f"@127.0.0.1:{stack.ports['amqp']}/"
        ),
        EVENT_EXCHANGE="beacon.regression.events",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
    )
    env.pop("PYTEST_ADDOPTS", None)
    report = stack.artifacts / "regression.xml"
    result = stack.run.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(ROOT / "pyproject.toml"),
            "app/tests",
            "-q",
            "-ra",
            "-p",
            "no:cacheprovider",
            "-p",
            "pytest_asyncio.plugin",
            f"--junitxml={report}",
        ],
        env=env,
        check=False,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout[-6000:]
    suites = list(ET.parse(report).getroot().iter("testsuite"))
    assert sum(int(s.attrib["tests"]) for s in suites) >= 78
    assert sum(int(s.attrib.get("skipped", 0)) for s in suites) == 0
