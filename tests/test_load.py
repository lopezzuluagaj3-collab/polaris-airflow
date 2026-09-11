"""Tests para etl/load/load_to_postgres.py (CLI wrapper)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from etl.load.load_to_postgres import main, parse_args
from etl.load.postgres_loader import MODELS_SQL, PROCESSED_DIR


def test_parse_args_defaults(monkeypatch):
    """Sin argumentos, se usan los valores por defecto del módulo."""
    monkeypatch.setattr(sys, "argv", ["load_to_postgres.py"])
    args = parse_args()

    assert args.batch_size == 2_000
    assert args.processed_dir == PROCESSED_DIR
    assert args.models_sql == MODELS_SQL


def test_parse_args_custom(monkeypatch, tmp_path):
    """Los argumentos de línea de comandos se parsean correctamente."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "load_to_postgres.py",
            "--processed-dir", str(tmp_path / "custom"),
            "--models-sql", str(tmp_path / "custom.sql"),
            "--batch-size", "500",
        ],
    )
    args = parse_args()

    assert args.batch_size == 500
    assert args.processed_dir == tmp_path / "custom"
    assert args.models_sql == tmp_path / "custom.sql"


def test_main_calls_load_processed_data(monkeypatch):
    """main() delega en load_processed_data con los argumentos parseados."""
    monkeypatch.setattr(sys, "argv", ["load_to_postgres.py"])

    with patch("etl.load.load_to_postgres.load_processed_data") as mock_load:
        main()

    mock_load.assert_called_once_with(
        processed_dir=PROCESSED_DIR,
        models_sql=MODELS_SQL,
        batch_size=2_000,
    )


def test_main_passes_custom_args(monkeypatch, tmp_path):
    """main() reenvía argumentos personalizados a load_processed_data."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "load_to_postgres.py",
            "--processed-dir", str(tmp_path),
            "--batch-size", "100",
        ],
    )

    with patch("etl.load.load_to_postgres.load_processed_data") as mock_load:
        main()

    mock_load.assert_called_once_with(
        processed_dir=tmp_path,
        models_sql=MODELS_SQL,
        batch_size=100,
    )


@pytest.mark.parametrize("batch_size_arg", ["0", "-1", "-100"])
def test_main_raises_on_non_positive_batch_size(monkeypatch, batch_size_arg):
    """Un batch_size <= 0 lanza ValueError antes de cargar."""
    monkeypatch.setattr(
        sys, "argv", ["load_to_postgres.py", "--batch-size", batch_size_arg]
    )

    with patch("etl.load.load_to_postgres.load_processed_data") as mock_load:
        with pytest.raises(ValueError, match="mayor que cero"):
            main()

    mock_load.assert_not_called()
