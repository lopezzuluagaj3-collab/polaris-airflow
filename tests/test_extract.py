"""Tests para etl/extract/kaggle_downloader.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from etl.extract.kaggle_downloader import (
    DATASET,
    REQUIRED_DATA_FILES,
    download_dataset,
)


def _touch_all_required_files(output_dir: Path) -> None:
    """Crea archivos vacíos para cada CSV requerido en *output_dir*."""
    for file_name in REQUIRED_DATA_FILES:
        (output_dir / file_name).touch()


def test_download_dataset_skips_when_all_files_exist(tmp_path: Path) -> None:
    """Si todos los CSV ya existen, no se llama a kaggle."""
    _touch_all_required_files(tmp_path)

    with patch("etl.extract.kaggle_downloader.subprocess.run") as mock_run:
        download_dataset(output_dir=tmp_path)

    mock_run.assert_not_called()


def test_download_dataset_raises_without_token(tmp_path: Path, monkeypatch) -> None:
    """Si faltan archivos y no hay token, lanza EnvironmentError."""
    monkeypatch.delenv("KAGGLE_API_TOKEN", raising=False)

    with pytest.raises(EnvironmentError, match="KAGGLE_API_TOKEN"):
        download_dataset(output_dir=tmp_path)


def test_download_dataset_calls_kaggle_when_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """Si faltan archivos y hay token, descarga vía kaggle."""
    monkeypatch.setenv("KAGGLE_API_TOKEN", "fake-token")

    with patch("etl.extract.kaggle_downloader.subprocess.run") as mock_run:
        download_dataset(output_dir=tmp_path)

    mock_run.assert_called_once_with(
        [
            "kaggle", "datasets", "download",
            "-d", DATASET,
            "-p", str(tmp_path),
            "--unzip",
        ],
        check=True,
    )


def test_download_dataset_uses_custom_dataset(tmp_path: Path, monkeypatch) -> None:
    """El dataset se puede sobreescribir y se pasa correctamente a kaggle."""
    monkeypatch.setenv("KAGGLE_API_TOKEN", "fake-token")

    with patch("etl.extract.kaggle_downloader.subprocess.run") as mock_run:
        download_dataset(dataset="custom/dataset", output_dir=tmp_path)

    mock_run.assert_called_once()
    args, _ = mock_run.call_args
    cmd = args[0]
    assert cmd[cmd.index("-d") + 1] == "custom/dataset"
    assert cmd[cmd.index("-p") + 1] == str(tmp_path)
