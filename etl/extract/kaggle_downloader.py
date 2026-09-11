# etl/extract/kaggle_downloader.py

import os
import subprocess
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DATASET = "olistbr/brazilian-ecommerce"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
REQUIRED_DATA_FILES = {
    "olist_orders_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
    "olist_products_dataset.csv",
    "olist_customers_dataset.csv",
    "olist_sellers_dataset.csv",
    "olist_geolocation_dataset.csv",
    "product_category_name_translation.csv",
}


def download_dataset(dataset: str = DATASET, output_dir: Path = DATA_DIR) -> None:
    """Crea data y descarga el dataset si faltan archivos fuente."""
    output_dir.mkdir(parents=True, exist_ok=True)

    existing_files = {path.name for path in output_dir.glob("*.csv")}
    missing_files = REQUIRED_DATA_FILES - existing_files
    if not missing_files:
        print(f"Los {len(REQUIRED_DATA_FILES)} archivos ya existen en: {output_dir}")
        return

    token = os.environ.get("KAGGLE_API_TOKEN")
    if not token:
        raise EnvironmentError(
            "KAGGLE_API_TOKEN no está definido. "
            "Exporta la variable de entorno antes de correr este script."
        )

    subprocess.run(
        [
            "kaggle", "datasets", "download",
            "-d", dataset,
            "-p", str(output_dir),
            "--unzip",
        ],
        check=True,
    )
    print(f"Dataset descargado y descomprimido en: {output_dir}")


if __name__ == "__main__":
    download_dataset()