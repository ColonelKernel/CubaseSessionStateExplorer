import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
FIXTURES = os.path.join(ROOT, "fixtures", "cubase")

if SRC not in sys.path:
    sys.path.insert(0, SRC)


@pytest.fixture(scope="session", autouse=True)
def _fixtures():
    """Ensure fixtures exist (generate them once if missing)."""
    if not os.path.exists(os.path.join(FIXTURES, "demo_session.dawproject")):
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_fixtures.py"),
                        FIXTURES], check=True)
    return FIXTURES


@pytest.fixture
def fixtures_dir():
    return FIXTURES
