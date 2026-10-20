"""Run the Codespaces integration suite and retain evidence outside service volumes."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts", type=Path, default=ROOT / "artifacts" / "integration"
    )
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11):
        parser.error("Use Python 3.11 (provided by .devcontainer/devcontainer.json)")
    if not shutil.which("docker"):
        parser.error("Docker CLI missing; run in a Docker-enabled Codespace")
    missing = [
        name
        for name in (
            "pytest",
            "pytest_asyncio",
            "asyncpg",
            "aio_pika",
            "httpx",
            "redis",
            "passlib",
            "websockets",
        )
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        parser.error(
            "Install app/requirements.txt in a Python 3.11 virtual environment"
        )

    artifacts = (
        args.artifacts
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex)
    ).resolve()
    artifacts.mkdir(parents=True, exist_ok=False)
    print(f"Evidence directory: {artifacts}", flush=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("PYTEST_") and k != "PYTHONPATH"
    }
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["BEACON_INTEGRATION_ARTIFACTS"] = str(artifacts)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        "pyproject.toml",
        "integration_tests",
        "-q",
        "-ra",
        "-p",
        "no:cacheprovider",
        "-p",
        "pytest_asyncio.plugin",
        f"--junitxml={artifacts / 'junit.xml'}",
    ]
    manifest = {
        "started": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "command": command,
        "status": "running",
    }
    (artifacts / "result.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    try:
        result = subprocess.run(command, cwd=ROOT, env=env)
    except KeyboardInterrupt:
        manifest.update(finished=datetime.now(UTC).isoformat(), status="interrupted")
        (artifacts / "result.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        print(f"Interrupted; inspect cleanup messages and evidence: {artifacts}")
        return 130

    # Failure reports can contain the synthetic WebSocket token. Redact it too.
    for report in artifacts.rglob("*.xml"):
        content = report.read_text(encoding="utf-8")
        content = re.sub(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+", "[redacted-jwt]", content)
        report.write_text(content, encoding="utf-8")

    # Pytest success with skips cannot establish the requested integration coverage.
    junit = artifacts / "junit.xml"
    skipped = None
    total = failures = errors = real_service_tests = 0
    if junit.exists():
        suites = list(ET.parse(junit).getroot().iter("testsuite"))
        skipped = sum(int(s.attrib.get("skipped", 0)) for s in suites)
        total = sum(int(s.attrib.get("tests", 0)) for s in suites)
        failures = sum(int(s.attrib.get("failures", 0)) for s in suites)
        errors = sum(int(s.attrib.get("errors", 0)) for s in suites)
        real_service_tests = sum(
            case.attrib.get("classname", "").startswith(
                (
                    "integration_tests.test_pipeline",
                    "integration_tests.test_faults",
                    "integration_tests.test_regression",
                )
            )
            for case in ET.parse(junit).getroot().iter("testcase")
        )
    passed = (
        result.returncode == 0
        and skipped == 0
        and failures == errors == 0
        and real_service_tests >= 17
    )
    manifest.update(
        finished=datetime.now(UTC).isoformat(),
        pytest_exit_code=result.returncode,
        skipped=skipped,
        tests=total,
        failures=failures,
        errors=errors,
        real_service_tests=real_service_tests,
        status="passed" if passed else "failed",
    )
    (artifacts / "result.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"Integration {'PASSED' if passed else 'FAILED'}; evidence: {artifacts}")
    return 0 if passed else (result.returncode or 1)


if __name__ == "__main__":
    raise SystemExit(main())
