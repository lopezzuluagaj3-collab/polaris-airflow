"""Transformaciones del dataset Brazilian E-Commerce Public Dataset by Olist.

El grano de ``fact_sales`` es una fila por ``order_id`` y ``order_item_id``.
Los pagos y las reseñas se agregan antes de incorporarse para no multiplicar
las líneas cuando un pedido tiene varios pagos o varias reseñas.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_DIR / "data"
DEFAULT_OUTPUT_DIR = DEFAULT_DATA_DIR / "processed"

CSV_FILES = {
	"orders": "olist_orders_dataset.csv",
	"order_items": "olist_order_items_dataset.csv",
	"order_payments": "olist_order_payments_dataset.csv",
	"order_reviews": "olist_order_reviews_dataset.csv",
	"products": "olist_products_dataset.csv",
	"customers": "olist_customers_dataset.csv",
	"sellers": "olist_sellers_dataset.csv",
	"category_translation": "product_category_name_translation.csv",
}

REGION_BY_STATE = {
	"AC": "Norte", "AP": "Norte", "AM": "Norte", "PA": "Norte",
	"RO": "Norte", "RR": "Norte", "TO": "Norte",
	"AL": "Nordeste", "BA": "Nordeste", "CE": "Nordeste",
	"MA": "Nordeste", "PB": "Nordeste", "PE": "Nordeste",
	"PI": "Nordeste", "RN": "Nordeste", "SE": "Nordeste",
	"DF": "Centro-Oeste", "GO": "Centro-Oeste", "MT": "Centro-Oeste",
	"MS": "Centro-Oeste",
	"ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
	"PR": "Sul", "RS": "Sul", "SC": "Sul",
}

ORDER_DATE_COLUMNS = [
	"order_purchase_timestamp",
	"order_approved_at",
	"order_delivered_carrier_date",
	"order_delivered_customer_date",
	"order_estimated_delivery_date",
]


def load_source_tables(data_dir: Path = DEFAULT_DATA_DIR) -> dict[str, pd.DataFrame]:
	"""Carga los ocho CSV necesarios para construir el modelo estrella."""
	tables = {}
	for table_name, file_name in CSV_FILES.items():
		path = data_dir / file_name
		if not path.exists():
			raise FileNotFoundError(f"No existe el archivo de entrada: {path}")
		tables[table_name] = pd.read_csv(path)
		print(f"Cargada {table_name}: {tables[table_name].shape}")
	return tables


def parse_order_dates(orders: pd.DataFrame) -> pd.DataFrame:
	"""Convierte las fechas de pedidos y conserva nulos de pedidos incompletos."""
	result = orders.copy()
	for column in ORDER_DATE_COLUMNS:
		result[column] = pd.to_datetime(result[column], errors="coerce")
	return result


def build_dim_customer(customers: pd.DataFrame) -> pd.DataFrame:
	"""Construye la dimensión de clientes y deriva la macro-región brasileña."""
	dimension = customers.copy()
	dimension["customer_region"] = dimension["customer_state"].map(REGION_BY_STATE).fillna("unknown")
	dimension["customer_key"] = pd.factorize(dimension["customer_id"], sort=True)[0] + 1
	return dimension[
		["customer_key", "customer_id", "customer_unique_id", "customer_zip_code_prefix",
		 "customer_city", "customer_state", "customer_region"]
	]


def build_dim_product(products: pd.DataFrame, translation: pd.DataFrame) -> pd.DataFrame:
	"""Construye productos y añade la categoría en inglés cuando existe traducción."""
	dimension = products.merge(
		translation,
		on="product_category_name",
		how="left",
		validate="many_to_one",
	)
	dimension["product_category_name"] = dimension["product_category_name"].fillna("unknown")
	dimension["product_category_name_english"] = dimension["product_category_name_english"].fillna("unknown")
	dimension["product_key"] = pd.factorize(dimension["product_id"], sort=True)[0] + 1
	return dimension[
		["product_key", "product_id", "product_category_name", "product_category_name_english",
		 "product_name_lenght", "product_description_lenght", "product_photos_qty",
		 "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"]
	]


def build_dim_seller(sellers: pd.DataFrame) -> pd.DataFrame:
	"""Construye la dimensión de vendedores y deriva la macro-región."""
	dimension = sellers.copy()
	dimension["seller_region"] = dimension["seller_state"].map(REGION_BY_STATE).fillna("unknown")
	dimension["seller_key"] = pd.factorize(dimension["seller_id"], sort=True)[0] + 1
	return dimension[
		["seller_key", "seller_id", "seller_zip_code_prefix", "seller_city", "seller_state", "seller_region"]
	]


def build_dim_date(orders: pd.DataFrame) -> pd.DataFrame:
	"""Construye una dimensión diaria a partir de todas las fechas de pedidos."""
	date_values = pd.concat([orders[column] for column in ORDER_DATE_COLUMNS]).dropna()
	date_range = pd.date_range(date_values.min().normalize(), date_values.max().normalize(), freq="D")
	dimension = pd.DataFrame({"date": date_range})
	dimension["date_key"] = dimension["date"].dt.strftime("%Y%m%d").astype(int)
	dimension["year"] = dimension["date"].dt.year
	dimension["quarter"] = dimension["date"].dt.quarter
	dimension["month"] = dimension["date"].dt.month
	dimension["month_name"] = dimension["date"].dt.month_name()
	dimension["week"] = dimension["date"].dt.isocalendar().week.astype(int)
	dimension["day"] = dimension["date"].dt.day
	dimension["day_of_week"] = dimension["date"].dt.dayofweek
	dimension["day_name"] = dimension["date"].dt.day_name()
	return dimension


def aggregate_payments(payments: pd.DataFrame) -> pd.DataFrame:
	"""Agrega pagos por pedido antes de unirlos con las líneas de venta."""
	aggregated = payments.groupby("order_id", as_index=False).agg(
		payment_value_total=("payment_value", "sum"),
		payment_count=("payment_sequential", "count"),
		payment_installments_max=("payment_installments", "max"),
	)
	payment_types = (
		payments.dropna(subset=["payment_type"])
		.drop_duplicates(["order_id", "payment_type"])
		.sort_values(["order_id", "payment_type"])
		.groupby("order_id", as_index=False)["payment_type"]
		.agg("|".join)
		.rename(columns={"payment_type": "payment_types"})
	)
	return aggregated.merge(payment_types, on="order_id", how="left", validate="one_to_one")


def aggregate_reviews(reviews: pd.DataFrame) -> pd.DataFrame:
	"""Agrega reseñas por pedido para evitar repetir la puntuación por línea."""
	return reviews.groupby("order_id", as_index=False).agg(
		review_score_average=("review_score", "mean"),
		review_count=("review_id", "nunique"),
	)


def build_fact_sales(
	orders: pd.DataFrame,
	order_items: pd.DataFrame,
	payments: pd.DataFrame,
	reviews: pd.DataFrame,
) -> pd.DataFrame:
	"""Construye la tabla de hechos a grano línea y valida sus cardinalidades."""
	order_attributes = orders[
		["order_id", "customer_id", "order_status", *ORDER_DATE_COLUMNS]
	].copy()
	order_attributes["purchase_date_key"] = order_attributes["order_purchase_timestamp"].dt.strftime("%Y%m%d").astype("Int64")
	order_attributes["estimated_delivery_date_key"] = order_attributes["order_estimated_delivery_date"].dt.strftime("%Y%m%d").astype("Int64")
	order_attributes["delivery_days"] = (
		order_attributes["order_delivered_customer_date"] - order_attributes["order_purchase_timestamp"]
	).dt.total_seconds() / 86400
	order_attributes["estimated_days"] = (
		order_attributes["order_estimated_delivery_date"] - order_attributes["order_purchase_timestamp"]
	).dt.total_seconds() / 86400
	order_attributes["is_late"] = (
		order_attributes["order_delivered_customer_date"] > order_attributes["order_estimated_delivery_date"]
	)

	fact = order_items.copy()
	fact["item_count"] = fact.groupby("order_id")["order_item_id"].transform("size")
	fact = fact.merge(order_attributes, on="order_id", how="left", validate="many_to_one")
	fact = fact.merge(aggregate_payments(payments), on="order_id", how="left", validate="many_to_one")
	fact = fact.merge(aggregate_reviews(reviews), on="order_id", how="left", validate="many_to_one")
	fact["sales_value"] = fact["price"]
	fact["freight_total"] = fact["freight_value"]
	return fact


def transform_dataset(data_dir: Path = DEFAULT_DATA_DIR, output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, pd.DataFrame]:
	"""Ejecuta toda la transformación y escribe las tablas listas para cargar."""
	tables = load_source_tables(data_dir)
	orders = parse_order_dates(tables["orders"])
	transformed = {
		"dim_customer": build_dim_customer(tables["customers"]),
		"dim_product": build_dim_product(tables["products"], tables["category_translation"]),
		"dim_seller": build_dim_seller(tables["sellers"]),
		"dim_date": build_dim_date(orders),
		"fact_sales": build_fact_sales(orders, tables["order_items"], tables["order_payments"], tables["order_reviews"]),
	}

	output_dir.mkdir(parents=True, exist_ok=True)
	for table_name, dataframe in transformed.items():
		output_path = output_dir / f"{table_name}.csv"
		dataframe.to_csv(output_path, index=False)
		print(f"Escrita {output_path}: {dataframe.shape}")

	expected_fact_rows = len(tables["order_items"])
	actual_fact_rows = len(transformed["fact_sales"])
	if actual_fact_rows != expected_fact_rows:
		raise ValueError(
			f"El grano de fact_sales cambió: se esperaban {expected_fact_rows:,} filas y quedaron {actual_fact_rows:,}."
		)
	print(f"Validación de grano OK: fact_sales conserva {actual_fact_rows:,} líneas de pedido.")
	return transformed


def parse_args() -> argparse.Namespace:
	"""Lee las rutas opcionales para ejecutar la transformación desde terminal."""
	parser = argparse.ArgumentParser(description="Transforma los CSV de Olist a tablas del modelo estrella.")
	parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
	parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
	return parser.parse_args()


if __name__ == "__main__":
	arguments = parse_args()
	transform_dataset(arguments.data_dir, arguments.output_dir)
