import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main  # noqa: E402 - needs ROOT on the path


@pytest.fixture
def config():
    """English test config: stable wording, independent of config.yaml."""
    return main.load_config(ROOT / "tests" / "fixtures" / "config.yaml")


@pytest.fixture
def real_config():
    return main.load_config()
