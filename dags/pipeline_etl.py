"""DAG ETL de Olist: extracción, transformación, modelo y carga."""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow import DAG
from airflow.operators.python import PythonOperator


PROJECT_ROOT = Path(os.getenv("AIRFLOW_PROJECT_ROOT", Path(__file__).resolve().parents[1]))
DATA_DIR = PROJECT_ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
MODELS_SQL = PROJECT_ROOT / "sql" / "create_star_schema.sql"


def _add_project_to_python_path() -> None:
	"""Permite importar los módulos del proyecto dentro del worker Airflow."""
	project_root = str(PROJECT_ROOT)
	if project_root not in sys.path:
		sys.path.insert(0, project_root)


def extract_data() -> None:
	"""Descarga los CSV originales de Olist en el volumen de datos."""
	_add_project_to_python_path()
	from etl.extract.kaggle_downloader import download_dataset

	download_dataset(output_dir=DATA_DIR)


def transform_data() -> None:
	"""Construye las dimensiones y la tabla fact_sales."""
	_add_project_to_python_path()
	from etl.transform.clean_orders import transform_dataset

	transform_dataset(data_dir=DATA_DIR, output_dir=PROCESSED_DIR)


def create_postgres_model() -> None:
	"""Crea el esquema y las tablas si todavía no existen."""
	_add_project_to_python_path()
	from etl.load.postgres_loader import create_database_model

	create_database_model(models_sql=MODELS_SQL)


def load_postgres_data() -> None:
	"""Carga las tablas transformadas con claves idempotentes."""
	_add_project_to_python_path()
	from etl.load.postgres_loader import load_processed_data

	load_processed_data(processed_dir=PROCESSED_DIR, models_sql=MODELS_SQL)


with DAG(
	dag_id="olist_etl_pipeline",
	description="Extrae, transforma y carga Olist en PostgreSQL.",
	start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
	schedule=None,
	catchup=False,
	max_active_runs=1,
	default_args={
		"owner": "data-engineering",
		"depends_on_past": False,
		"retries": 2,
		"retry_delay": timedelta(minutes=5),
	},
	tags=["olist", "etl", "postgres"],
) as dag:
	extract_task = PythonOperator(
		task_id="extraer_datos",
		python_callable=extract_data,
	)

	transform_task = PythonOperator(
		task_id="transformar_datos",
		python_callable=transform_data,
	)

	create_model_task = PythonOperator(
		task_id="crear_modelo_postgres",
		python_callable=create_postgres_model,
	)

	load_task = PythonOperator(
		task_id="cargar_datos_postgres",
		python_callable=load_postgres_data,
	)

	extract_task >> transform_task >> create_model_task >> load_task
