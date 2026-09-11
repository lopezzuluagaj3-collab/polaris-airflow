"""Tests para etl/load/postgres_loader.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from psycopg2 import sql as pg_sql

from etl.load.postgres_loader import (
    MODELS_SQL,
    REQUIRED_MODEL_OBJECTS,
    TABLE_COLUMNS,
    TABLE_FILES,
    create_database_model,
    create_model_if_needed,
    get_database_connection,
    load_csv_idempotently,
    load_processed_data,
    model_exists,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_connection(rowcount: int = 1) -> MagicMock:
    """Crea un mock de conexión que respeta el protocolo ``with cursor()``."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.rowcount = rowcount
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = False
    return conn


@pytest.fixture
def sample_processed_dir(tmp_path: Path) -> Path:
    """Crea los 5 CSV de salida con datos mínimos pero válidos."""
    processed = tmp_path / "processed"
    processed.mkdir()

    samples = {
        "dim_customer": pd.DataFrame(
            {
                "customer_key": [1],
                "customer_id": ["c1"],
                "customer_unique_id": ["u1"],
                "customer_zip_code_prefix": [11111],
                "customer_city": ["Sao Paulo"],
                "customer_state": ["SP"],
                "customer_region": ["Sudeste"],
            }
        ),
        "dim_product": pd.DataFrame(
            {
                "product_key": [1],
                "product_id": ["p1"],
                "product_category_name": ["cat_a"],
                "product_category_name_english": ["category_a"],
                "product_name_lenght": [10],
                "product_description_lenght": [100],
                "product_photos_qty": [3],
                "product_weight_g": ["100"],
                "product_length_cm": ["10"],
                "product_height_cm": ["5"],
                "product_width_cm": ["5"],
            }
        ),
        "dim_seller": pd.DataFrame(
            {
                "seller_key": [1],
                "seller_id": ["s1"],
                "seller_zip_code_prefix": [44444],
                "seller_city": ["Curitiba"],
                "seller_state": ["PR"],
                "seller_region": ["Sul"],
            }
        ),
        "dim_date": pd.DataFrame(
            {
                "date_key": [20230101],
                "date": ["2023-01-01"],
                "year": [2023],
                "quarter": [1],
                "month": [1],
                "month_name": ["January"],
                "week": [1],
                "day": [1],
                "day_of_week": [6],
                "day_name": ["Sunday"],
            }
        ),
        "fact_sales": pd.DataFrame(
            {
                "order_id": ["o1"],
                "order_item_id": [1],
                "product_id": ["p1"],
                "seller_id": ["s1"],
                "customer_id": ["c1"],
                "order_status": ["delivered"],
                "shipping_limit_date": ["2023-01-02 10:00:00"],
                "order_purchase_timestamp": ["2023-01-01 10:00:00"],
                "order_approved_at": ["2023-01-01 11:00:00"],
                "order_delivered_carrier_date": ["2023-01-02 10:00:00"],
                "order_delivered_customer_date": ["2023-01-03 10:00:00"],
                "order_estimated_delivery_date": ["2023-01-10 10:00:00"],
                "purchase_date_key": [20230101],
                "estimated_delivery_date_key": [20230110],
                "price": [100.0],
                "freight_value": [10.0],
                "sales_value": [100.0],
                "freight_total": [10.0],
                "item_count": [1],
                "delivery_days": [2.0],
                "estimated_days": [9.0],
                "is_late": [False],
                "payment_value_total": [100.0],
                "payment_count": [1],
                "payment_installments_max": [1],
                "payment_types": ["credit_card"],
                "review_score_average": [5.0],
                "review_count": [1],
            }
        ),
    }

    for table_name, df in samples.items():
        file_name = TABLE_FILES[table_name]
        df.to_csv(processed / file_name, index=False)

    return processed


@pytest.fixture
def sample_sql_file(tmp_path: Path) -> Path:
    """Crea un archivo SQL temporal con contenido válido."""
    sql_file = tmp_path / "create_star_schema.sql"
    sql_file.write_text("CREATE SCHEMA IF NOT EXISTS analytics;")
    return sql_file


def _all_db_env_vars():
    return [
        "DATABASE_URL", "POSTGRES_URL",
        "ETL_POSTGRES_HOST", "POSTGRES_HOST",
        "ETL_POSTGRES_PORT", "POSTGRES_PORT",
        "ETL_POSTGRES_DB", "POSTGRES_DB",
        "ETL_POSTGRES_USER", "POSTGRES_USER",
        "ETL_POSTGRES_PASSWORD", "POSTGRES_PASSWORD",
    ]


# ---------------------------------------------------------------------------
# Tests de get_database_connection
# ---------------------------------------------------------------------------

class TestGetDatabaseConnection:
    """Tests para get_database_connection."""

    def test_raises_without_credentials(self, monkeypatch):
        for var in _all_db_env_vars():
            monkeypatch.delenv(var, raising=False)

        with pytest.raises(EnvironmentError, match="Faltan variables"):
            get_database_connection()

    def test_uses_database_url(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_URL", "postgresql://user:pass@host:5432/db")
        for var in _all_db_env_vars():
            if var not in ("POSTGRES_URL",):
                monkeypatch.delenv(var, raising=False)

        with patch("etl.load.postgres_loader.psycopg2.connect") as mock_connect:
            get_database_connection()

        mock_connect.assert_called_once_with("postgresql://user:pass@host:5432/db")

    def test_uses_individual_env_vars(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        monkeypatch.delenv("ETL_POSTGRES_HOST", raising=False)
        monkeypatch.setenv("POSTGRES_DB", "testdb")
        monkeypatch.setenv("POSTGRES_USER", "testuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "testpass")

        with patch("etl.load.postgres_loader.psycopg2.connect") as mock_connect:
            get_database_connection()

        mock_connect.assert_called_once()
        kwargs = mock_connect.call_args.kwargs
        assert kwargs["dbname"] == "testdb"
        assert kwargs["user"] == "testuser"
        assert kwargs["password"] == "testpass"
        assert kwargs["host"] == "localhost"
        assert kwargs["port"] == "5432"

    def test_etl_env_overrides_postgres(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        monkeypatch.setenv("ETL_POSTGRES_DB", "etldb")
        monkeypatch.setenv("POSTGRES_DB", "testdb")
        monkeypatch.setenv("ETL_POSTGRES_USER", "etluser")
        monkeypatch.setenv("POSTGRES_USER", "testuser")
        monkeypatch.setenv("ETL_POSTGRES_PASSWORD", "etlpw")
        monkeypatch.setenv("POSTGRES_PASSWORD", "testpass")

        with patch("etl.load.postgres_loader.psycopg2.connect") as mock_connect:
            get_database_connection()

        kwargs = mock_connect.call_args.kwargs
        assert kwargs["dbname"] == "etldb"
        assert kwargs["user"] == "etluser"
        assert kwargs["password"] == "etlpw"


# ---------------------------------------------------------------------------
# Tests de model_exists
# ---------------------------------------------------------------------------

class TestModelExists:
    def test_true_when_all_tables_and_views_present(self):
        conn = _make_mock_connection()
        all_objs = [
            (t, "TABLE") for t in REQUIRED_MODEL_OBJECTS["tables"]
        ] + [
            (v, "VIEW") for v in REQUIRED_MODEL_OBJECTS["views"]
        ]
        conn.cursor.return_value.__enter__.return_value.fetchall.return_value = all_objs
        conn.server_version = 140000

        assert model_exists(conn) is True

    def test_false_when_table_missing(self):
        conn = _make_mock_connection()
        incomplete = [("dim_customer", "TABLE"), ("v_order_metrics", "VIEW")]
        conn.cursor.return_value.__enter__.return_value.fetchall.return_value = incomplete

        assert model_exists(conn) is False

    def test_false_when_view_missing(self):
        conn = _make_mock_connection()
        incomplete = [
            (t, "TABLE") for t in REQUIRED_MODEL_OBJECTS["tables"]
        ] + [("v_order_metrics", "VIEW")]
        conn.cursor.return_value.__enter__.return_value.fetchall.return_value = incomplete

        assert model_exists(conn) is False


# ---------------------------------------------------------------------------
# Tests de create_model_if_needed
# ---------------------------------------------------------------------------

class TestCreateModelIfNeeded:
    def test_skips_when_model_already_exists(self, sample_sql_file):
        conn = _make_mock_connection()
        with patch(
            "etl.load.postgres_loader.model_exists", return_value=True
        ):
            create_model_if_needed(conn, sample_sql_file)

        conn.cursor.return_value.__enter__.return_value.execute.assert_not_called()

    def test_executes_sql_when_model_missing(self, sample_sql_file):
        conn = _make_mock_connection()
        expected_sql = sample_sql_file.read_text(encoding="utf-8")
        with patch(
            "etl.load.postgres_loader.model_exists", return_value=False
        ):
            create_model_if_needed(conn, sample_sql_file)

        conn.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
            expected_sql
        )

    def test_raises_when_sql_file_missing(self, tmp_path):
        conn = _make_mock_connection()
        missing_file = tmp_path / "does_not_exist.sql"
        with patch(
            "etl.load.postgres_loader.model_exists", return_value=False
        ):
            with pytest.raises(FileNotFoundError, match="does_not_exist.sql"):
                create_model_if_needed(conn, missing_file)


# ---------------------------------------------------------------------------
# Tests de load_csv_idempotently
# ---------------------------------------------------------------------------

class TestLoadCsvIdempotently:
    def test_inserts_and_returns_rowcount(self, tmp_path):
        csv_path = tmp_path / "dim_customer.csv"
        df = pd.DataFrame(
            {
                "customer_key": [1, 2],
                "customer_id": ["c1", "c2"],
                "customer_unique_id": ["u1", "u2"],
                "customer_zip_code_prefix": [11111, 22222],
                "customer_city": ["SP", "RJ"],
                "customer_state": ["SP", "RJ"],
                "customer_region": ["Sudeste", "Sudeste"],
            }
        )
        df.to_csv(csv_path, index=False)

        conn = _make_mock_connection(rowcount=2)

        with patch("etl.load.postgres_loader.execute_values") as mock_ev, \
             patch.object(pg_sql.Composed, "as_string", return_value="INSERT ..."):
            result = load_csv_idempotently(conn, "dim_customer", csv_path)

        assert result == 2
        mock_ev.assert_called_once()

    def test_raises_on_missing_columns(self, tmp_path):
        csv_path = tmp_path / "dim_customer.csv"
        df = pd.DataFrame({"customer_key": [1], "customer_id": ["c1"]})
        df.to_csv(csv_path, index=False)

        conn = _make_mock_connection()

        with pytest.raises(ValueError, match="no contiene columnas requeridas"):
            load_csv_idempotently(conn, "dim_customer", csv_path)

    def test_batches_large_files(self, tmp_path):
        csv_path = tmp_path / "dim_customer.csv"
        rows = 5
        df = pd.DataFrame(
            {
                "customer_key": range(1, rows + 1),
                "customer_id": [f"c{i}" for i in range(rows)],
                "customer_unique_id": [f"u{i}" for i in range(rows)],
                "customer_zip_code_prefix": [11111] * rows,
                "customer_city": ["SP"] * rows,
                "customer_state": ["SP"] * rows,
                "customer_region": ["Sudeste"] * rows,
            }
        )
        df.to_csv(csv_path, index=False)

        conn = _make_mock_connection(rowcount=2)

        with patch("etl.load.postgres_loader.execute_values") as mock_ev, \
             patch.object(pg_sql.Composed, "as_string", return_value="INSERT ..."):
            result = load_csv_idempotently(conn, "dim_customer", csv_path, batch_size=2)

        assert result == 6  # 3 batches * rowcount=2
        assert mock_ev.call_count == 3  # ceil(5/2) = 3 batches


# ---------------------------------------------------------------------------
# Tests de create_database_model
# ---------------------------------------------------------------------------

class TestCreateDatabaseModel:
    def test_commits_on_success(self, sample_sql_file):
        conn = _make_mock_connection()
        with patch(
            "etl.load.postgres_loader.get_database_connection",
            return_value=conn,
        ), patch(
            "etl.load.postgres_loader.model_exists", return_value=True
        ):
            create_database_model(models_sql=sample_sql_file)

        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()
        conn.close.assert_called_once()

    def test_rolls_back_on_error(self, sample_sql_file):
        conn = _make_mock_connection()
        with patch(
            "etl.load.postgres_loader.get_database_connection",
            return_value=conn,
        ), patch(
            "etl.load.postgres_loader.create_model_if_needed",
            side_effect=RuntimeError("schema error"),
        ):
            with pytest.raises(RuntimeError, match="schema error"):
                create_database_model(models_sql=sample_sql_file)

        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
        conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# Tests de load_processed_data
# ---------------------------------------------------------------------------

class TestLoadProcessedData:
    def test_loads_all_tables_and_commits(
        self, sample_processed_dir: Path, sample_sql_file: Path
    ):
        conn = _make_mock_connection(rowcount=1)
        with patch(
            "etl.load.postgres_loader.get_database_connection",
            return_value=conn,
        ), patch(
            "etl.load.postgres_loader.model_exists", return_value=True
        ), patch(
            "etl.load.postgres_loader.execute_values"
        ), patch.object(
            pg_sql.Composed, "as_string", return_value="INSERT ..."
        ):
            result = load_processed_data(
                processed_dir=sample_processed_dir,
                models_sql=sample_sql_file,
            )

        assert set(result.keys()) == set(TABLE_FILES.keys())
        assert all(v == 1 for v in result.values())
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_rolls_back_when_csv_missing(
        self, tmp_path: Path, sample_sql_file: Path
    ):
        conn = _make_mock_connection()
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with patch(
            "etl.load.postgres_loader.get_database_connection",
            return_value=conn,
        ), patch(
            "etl.load.postgres_loader.model_exists", return_value=True
        ):
            with pytest.raises(FileNotFoundError, match="No existe el archivo procesado"):
                load_processed_data(
                    processed_dir=empty_dir,
                    models_sql=sample_sql_file,
                )

        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_rolls_back_on_load_error(
        self, sample_processed_dir: Path, sample_sql_file: Path
    ):
        conn = _make_mock_connection()
        with patch(
            "etl.load.postgres_loader.get_database_connection",
            return_value=conn,
        ), patch(
            "etl.load.postgres_loader.model_exists", return_value=True
        ), patch(
            "etl.load.postgres_loader.load_csv_idempotently",
            side_effect=ValueError("bad data"),
        ):
            with pytest.raises(ValueError, match="bad data"):
                load_processed_data(
                    processed_dir=sample_processed_dir,
                    models_sql=sample_sql_file,
                )

        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
