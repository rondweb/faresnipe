"""Tests for the multiuser CLI."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from faresnipe_multiuser.cli import _run_migrations


def test_run_migrations_uses_project_root_for_alembic() -> None:
    expected_root = Path(__file__).resolve().parents[1]

    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        _run_migrations()

    run.assert_called_once_with(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=expected_root,
    )
