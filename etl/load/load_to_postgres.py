"""Carga idempotente de las tablas transformadas en PostgreSQL."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
	from .postgres_loader import MODELS_SQL, PROCESSED_DIR, create_database_model, load_processed_data
except ImportError:
	from postgres_loader import MODELS_SQL, PROCESSED_DIR, create_database_model, load_processed_data


def parse_args() -> argparse.Namespace:
	"""Lee rutas y tamaño de lote para la carga desde terminal."""
	parser = argparse.ArgumentParser(
		description="Crea el modelo analytics y carga data/processed de forma idempotente."
	)
	parser.add_argument("--processed-dir", type=Path, default=PROCESSED_DIR)
	parser.add_argument("--models-sql", type=Path, default=MODELS_SQL)
	parser.add_argument("--batch-size", type=int, default=2_000)
	return parser.parse_args()


def main() -> None:
	"""Ejecuta la carga idempotente y deja que los errores detengan el proceso."""
	arguments = parse_args()
	if arguments.batch_size <= 0:
		raise ValueError("--batch-size debe ser mayor que cero")
	load_processed_data(
		processed_dir=arguments.processed_dir,
		models_sql=arguments.models_sql,
		batch_size=arguments.batch_size,
	)


if __name__ == "__main__":
	main()
