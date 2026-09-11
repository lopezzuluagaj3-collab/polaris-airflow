"""Implementación de la carga idempotente del modelo estrella en PostgreSQL."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2 import sql
from psycopg2.extras import execute_values

PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_DIR / "data"
PROCESSED_DIR = DATA_DIR / "processed"
MODELS_SQL = PROJECT_DIR / "sql" / "models.sql"
load_dotenv(PROJECT_DIR / ".env")


def _validate_path(path: Path, base: Path) -> Path:
    """Valida que la ruta resuelta esté dentro del directorio base permitido."""
    resolved = path.resolve()
    base_resolved = base.resolve()
    if not str(resolved).startswith(str(base_resolved)):
        raise ValueError(f"Ruta no permitida: {path} (fuera de {base})")
    return resolved


TABLE_FILES = {
    "dim_customer": "dim_customer.csv",
    "dim_product": "dim_product.csv",
    "dim_seller": "dim_seller.csv",
    "dim_date": "dim_date.csv",
    "fact_sales": "fact_sales.csv",
}

TABLE_COLUMNS = {
    "dim_customer": ["customer_key", "customer_id", "customer_unique_id", "customer_zip_code_prefix", "customer_city", "customer_state", "customer_region"],
    "dim_product": ["product_key", "product_id", "product_category_name", "product_category_name_english", "product_name_lenght", "product_description_lenght", "product_photos_qty", "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"],
    "dim_seller": ["seller_key", "seller_id", "seller_zip_code_prefix", "seller_city", "seller_state", "seller_region"],
    "dim_date": ["date_key", "date", "year", "quarter", "month", "month_name", "week", "day", "day_of_week", "day_name"],
    "fact_sales": ["order_id", "order_item_id", "product_id", "seller_id", "customer_id", "order_status", "shipping_limit_date", "order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date", "order_estimated_delivery_date", "purchase_date_key", "estimated_delivery_date_key", "price", "freight_value", "sales_value", "freight_total", "item_count", "delivery_days", "estimated_days", "is_late", "payment_value_total", "payment_count", "payment_installments_max", "payment_types", "review_score_average", "review_count"],
}

REQUIRED_MODEL_OBJECTS = {
    "tables": {"dim_customer", "dim_product", "dim_seller", "dim_date", "fact_sales"},
    "views": {"v_order_metrics", "v_sales_by_date_category"},
}


def get_database_connection():
    """Conecta usando DATABASE_URL o las variables POSTGRES_* del entorno."""
    database_url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if database_url:
        return psycopg2.connect(database_url)
    variables = {
        "host": os.getenv("ETL_POSTGRES_HOST") or os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("ETL_POSTGRES_PORT") or os.getenv("POSTGRES_PORT", "5432"),
        "dbname": os.getenv("ETL_POSTGRES_DB") or os.getenv("POSTGRES_DB"),
        "user": os.getenv("ETL_POSTGRES_USER") or os.getenv("POSTGRES_USER"),
        "password": os.getenv("ETL_POSTGRES_PASSWORD") or os.getenv("POSTGRES_PASSWORD"),
    }
    missing = [name for name in ("dbname", "user", "password") if not variables[name]]
    if missing:
        raise EnvironmentError("Faltan variables de conexión PostgreSQL: " + ", ".join(missing))
    return psycopg2.connect(**variables)


def model_exists(connection) -> bool:
    """Comprueba que todas las tablas y vistas del modelo existan."""
    query = """
        SELECT table_name, 'TABLE' AS object_type FROM information_schema.tables
        WHERE table_schema = 'analytics' AND table_name = ANY(%s)
        UNION ALL
        SELECT table_name, 'VIEW' AS object_type FROM information_schema.views
        WHERE table_schema = 'analytics' AND table_name = ANY(%s)
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (list(REQUIRED_MODEL_OBJECTS["tables"]), list(REQUIRED_MODEL_OBJECTS["views"])))
        found = {(name, object_type) for name, object_type in cursor.fetchall()}
    tables = {name for name, object_type in found if object_type == "TABLE"}
    views = {name for name, object_type in found if object_type == "VIEW"}
    return REQUIRED_MODEL_OBJECTS["tables"] <= tables and REQUIRED_MODEL_OBJECTS["views"] <= views


def create_model_if_needed(connection, models_sql: Path = MODELS_SQL) -> None:
    """Ejecuta models.sql solo cuando el modelo todavía no está completo."""
    if model_exists(connection):
        print("Modelo analytics ya existe; se omite su creación.")
        return
    models_sql = _validate_path(models_sql, PROJECT_DIR)
    if not models_sql.exists():
        raise FileNotFoundError(f"No existe el SQL del modelo: {models_sql}")
    sql_content = models_sql.read_text(encoding="utf-8")
    statements = [stmt.strip() for stmt in sql_content.split(";") if stmt.strip()]
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    print(f"Modelo creado desde: {models_sql}")


def create_database_model(models_sql: Path = MODELS_SQL) -> None:
    """Abre una transacción y crea el modelo solo si todavía no existe."""
    connection = get_database_connection()
    try:
        connection.autocommit = False
        create_model_if_needed(connection, models_sql)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def load_csv_idempotently(connection, table_name: str, csv_path: Path, batch_size: int = 2_000) -> int:
    """Carga un CSV en lotes e ignora claves que ya estén en PostgreSQL."""
    dataframe = pd.read_csv(csv_path, keep_default_na=True)
    columns = TABLE_COLUMNS[table_name]
    missing = set(columns) - set(dataframe.columns)
    if missing:
        raise ValueError(f"{csv_path} no contiene columnas requeridas: {sorted(missing)}")
    dataframe = dataframe[columns].astype(object).where(pd.notna(dataframe[columns]), None)
    column_sql = sql.SQL(", ").join(sql.Identifier(column) for column in columns)
    statement = sql.SQL("INSERT INTO analytics.{table} ({columns}) VALUES %s ON CONFLICT DO NOTHING").format(
        table=sql.Identifier(table_name), columns=column_sql
    )
    inserted = 0
    with connection.cursor() as cursor:
        for start in range(0, len(dataframe), batch_size):
            values = list(dataframe.iloc[start:start + batch_size].itertuples(index=False, name=None))
            execute_values(cursor, statement.as_string(connection), values, page_size=batch_size)
            inserted += cursor.rowcount
    print(f"{table_name}: {inserted:,} nuevas; {len(dataframe) - inserted:,} ya existentes.")
    return inserted


def load_processed_data(processed_dir: Path = PROCESSED_DIR, models_sql: Path = MODELS_SQL, batch_size: int = 2_000) -> dict[str, int]:
    """Crea el modelo si hace falta y carga todas las salidas transformadas."""
    processed_dir = _validate_path(processed_dir, PROJECT_DIR)
    models_sql = _validate_path(models_sql, PROJECT_DIR)
    connection = get_database_connection()
    try:
        connection.autocommit = False
        create_model_if_needed(connection, models_sql)
        inserted = {}
        for table_name, file_name in TABLE_FILES.items():
            csv_path = processed_dir / file_name
            if not csv_path.exists():
                raise FileNotFoundError(f"No existe el archivo procesado: {csv_path}")
            inserted[table_name] = load_csv_idempotently(connection, table_name, csv_path, batch_size)
        connection.commit()
        print("Carga PostgreSQL confirmada correctamente.")
        return inserted
    except Exception:
        connection.rollback()
        print("La carga falló; se revirtió la transacción.")
        raise
    finally:
        connection.close()