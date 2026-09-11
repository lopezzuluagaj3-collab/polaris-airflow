"""Tests para etl/transform/clean_orders.py."""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest

from etl.transform.clean_orders import (
    CSV_FILES,
    ORDER_DATE_COLUMNS,
    REGION_BY_STATE,
    aggregate_payments,
    aggregate_reviews,
    build_dim_customer,
    build_dim_date,
    build_dim_product,
    build_dim_seller,
    build_fact_sales,
    parse_order_dates,
    transform_dataset,
)


# ---------------------------------------------------------------------------
# Fixtures y datos de ejemplo
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_customers() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "customer_id": ["c1", "c2", "c3"],
            "customer_unique_id": ["u1", "u2", "u3"],
            "customer_zip_code_prefix": [11111, 22222, 33333],
            "customer_city": ["Sao Paulo", "Rio de Janeiro", "Manaus"],
            "customer_state": ["SP", "RJ", "AM"],
        }
    )


@pytest.fixture
def sample_products() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "product_id": ["p1", "p2", "p3"],
            "product_category_name": ["cat_a", "cat_b", "unknown_cat"],
            "product_name_lenght": [10, 20, 5],
            "product_description_lenght": [100, 200, 50],
            "product_photos_qty": [3, 5, 1],
            "product_weight_g": ["100", "200", "50"],
            "product_length_cm": ["10", "20", "5"],
            "product_height_cm": ["5", "10", "3"],
            "product_width_cm": ["5", "10", "2"],
        }
    )


@pytest.fixture
def sample_translation() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "product_category_name": ["cat_a", "cat_b"],
            "product_category_name_english": ["category_a", "category_b"],
        }
    )


@pytest.fixture
def sample_sellers() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "seller_id": ["s1", "s2"],
            "seller_zip_code_prefix": [44444, 55555],
            "seller_city": ["Curitiba", "Recife"],
            "seller_state": ["PR", "PE"],
        }
    )


@pytest.fixture
def sample_orders() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": ["o1", "o2"],
            "customer_id": ["c1", "c2"],
            "order_status": ["delivered", "canceled"],
            "order_purchase_timestamp": ["2023-01-01 10:00:00", "2023-02-01 12:00:00"],
            "order_approved_at": ["2023-01-01 11:00:00", None],
            "order_delivered_carrier_date": ["2023-01-02 10:00:00", None],
            "order_delivered_customer_date": ["2023-01-03 10:00:00", None],
            "order_estimated_delivery_date": ["2023-01-10 10:00:00", "2023-02-20 10:00:00"],
        }
    )


@pytest.fixture
def sample_order_items() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": ["o1", "o1", "o2"],
            "order_item_id": [1, 2, 1],
            "product_id": ["p1", "p2", "p1"],
            "seller_id": ["s1", "s2", "s1"],
            "shipping_limit_date": ["2023-01-02 10:00:00", "2023-01-02 10:00:00", "2023-02-02 10:00:00"],
            "price": [100.0, 50.0, 75.0],
            "freight_value": [10.0, 5.0, 7.5],
        }
    )


@pytest.fixture
def sample_payments() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": ["o1", "o1", "o2"],
            "payment_sequential": [1, 2, 1],
            "payment_value": [80.0, 70.0, 75.0],
            "payment_installments": [1, 2, 3],
            "payment_type": ["credit_card", "boleto", "credit_card"],
        }
    )


@pytest.fixture
def sample_reviews() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": ["o1", "o1", "o2"],
            "review_id": ["r1", "r2", "r3"],
            "review_score": [5, 4, 3],
        }
    )


@pytest.fixture
def parsed_orders(sample_orders: pd.DataFrame) -> pd.DataFrame:
    return parse_order_dates(sample_orders)


# ---------------------------------------------------------------------------
# Tests de transformaciones unitarias
# ---------------------------------------------------------------------------

def test_parse_order_dates_converts_strings(parsed_orders, sample_orders):
    """parse_order_dates convierte columnas de fecha a datetime."""
    assert len(parsed_orders) == len(sample_orders)
    assert pd.api.types.is_datetime64_any_dtype(parsed_orders["order_purchase_timestamp"])
    assert pd.isna(parsed_orders.loc[1, "order_approved_at"])


def test_build_dim_customer(sample_customers: pd.DataFrame):
    """La dimensión de clientes incluye región y clave sustituta."""
    dim = build_dim_customer(sample_customers)

    assert list(dim.columns) == [
        "customer_key", "customer_id", "customer_unique_id",
        "customer_zip_code_prefix", "customer_city", "customer_state",
        "customer_region",
    ]
    assert len(dim) == 3
    assert set(dim["customer_key"]) == {1, 2, 3}
    assert dim.loc[dim["customer_state"] == "SP", "customer_region"].iloc[0] == "Sudeste"
    assert dim.loc[dim["customer_state"] == "AM", "customer_region"].iloc[0] == "Norte"


def test_build_dim_customer_unknown_state():
    """Los estados sin mapeo reciben 'unknown'."""
    customers = pd.DataFrame(
        {
            "customer_id": ["c1"],
            "customer_unique_id": ["u1"],
            "customer_zip_code_prefix": [11111],
            "customer_city": ["Test"],
            "customer_state": ["ZZ"],
        }
    )
    dim = build_dim_customer(customers)
    assert dim["customer_region"].iloc[0] == "unknown"


def test_build_dim_product_with_translation(sample_products, sample_translation):
    """La dimensión de productos fusiona la traducción de categorías."""
    dim = build_dim_product(sample_products, sample_translation)

    assert "product_key" in dim.columns
    assert "product_category_name_english" in dim.columns
    assert set(dim["product_key"]) == {1, 2, 3}
    row_p1 = dim.loc[dim["product_id"] == "p1"]
    assert row_p1["product_category_name_english"].iloc[0] == "category_a"
    row_p3 = dim.loc[dim["product_id"] == "p3"]
    assert row_p3["product_category_name"].iloc[0] == "unknown_cat"
    assert row_p3["product_category_name_english"].iloc[0] == "unknown"


def test_build_dim_seller(sample_sellers: pd.DataFrame):
    """La dimensión de vendedores incluye región y clave sustituta."""
    dim = build_dim_seller(sample_sellers)

    assert "seller_key" in dim.columns
    assert "seller_region" in dim.columns
    assert set(dim["seller_key"]) == {1, 2}
    assert dim.loc[dim["seller_state"] == "PR", "seller_region"].iloc[0] == "Sul"
    assert dim.loc[dim["seller_state"] == "PE", "seller_region"].iloc[0] == "Nordeste"


def test_build_dim_date(parsed_orders, sample_orders):
    """La dimensión de fechas genera un día por cada fecha única."""
    dim = build_dim_date(parsed_orders)

    assert "date_key" in dim.columns
    assert "day_name" in dim.columns
    min_date = parsed_orders["order_purchase_timestamp"].min().normalize()
    max_date = max(
        col.max() for col in (parsed_orders[col] for col in ORDER_DATE_COLUMNS)
    )
    expected_days = (max_date.normalize() - min_date).days + 1
    assert len(dim) == expected_days


def test_aggregate_payments_groups_by_order(sample_payments: pd.DataFrame):
    """Los pagos se agrupan por pedido sumando valores y contando."""
    agg = aggregate_payments(sample_payments)

    assert "payment_value_total" in agg.columns
    assert "payment_count" in agg.columns
    assert "payment_types" in agg.columns
    o1 = agg.loc[agg["order_id"] == "o1"].iloc[0]
    assert o1["payment_value_total"] == pytest.approx(150.0)
    assert o1["payment_count"] == 2
    assert "boleto" in o1["payment_types"]
    assert "credit_card" in o1["payment_types"]


def test_aggregate_reviews_groups_by_order(sample_reviews: pd.DataFrame):
    """Las reseñas se agrupan por pedido calculando promedio y conteo."""
    agg = aggregate_reviews(sample_reviews)

    o1 = agg.loc[agg["order_id"] == "o1"].iloc[0]
    assert o1["review_score_average"] == pytest.approx(4.5)
    assert o1["review_count"] == 2
    o2 = agg.loc[agg["order_id"] == "o2"].iloc[0]
    assert o2["review_score_average"] == pytest.approx(3.0)
    assert o2["review_count"] == 1


def test_build_fact_sales_preserves_grain(
    parsed_orders, sample_order_items, sample_payments, sample_reviews
):
    """fact_sales mantiene el grano de order_items (muchos-a-uno)."""
    fact = build_fact_sales(parsed_orders, sample_order_items, sample_payments, sample_reviews)

    assert len(fact) == len(sample_order_items)
    assert "sales_value" in fact.columns
    assert "freight_total" in fact.columns
    assert "item_count" in fact.columns
    o1_items = fact.loc[fact["order_id"] == "o1"]
    assert len(o1_items) == 2
    assert o1_items["item_count"].iloc[0] == 2


# ---------------------------------------------------------------------------
# Tests end-to-end de transform_dataset
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_data_dir() -> Path:
    """Crea los 8 CSV de origen en un directorio temporal dentro del proyecto."""
    test_dir = Path(__file__).resolve().parents[1] / "tests" / "temp" / "data"
    test_dir.mkdir(parents=True, exist_ok=True)

    files = {
        CSV_FILES["orders"]: pd.DataFrame(
            {
                "order_id": ["o1", "o2"],
                "customer_id": ["c1", "c2"],
                "order_status": ["delivered", "canceled"],
                "order_purchase_timestamp": ["2023-01-01 10:00:00", "2023-02-01 12:00:00"],
                "order_approved_at": ["2023-01-01 11:00:00", None],
                "order_delivered_carrier_date": ["2023-01-02 10:00:00", None],
                "order_delivered_customer_date": ["2023-01-03 10:00:00", None],
                "order_estimated_delivery_date": ["2023-01-10 10:00:00", "2023-02-20 10:00:00"],
            }
        ),
        CSV_FILES["order_items"]: pd.DataFrame(
            {
                "order_id": ["o1", "o1", "o2"],
                "order_item_id": [1, 2, 1],
                "product_id": ["p1", "p2", "p1"],
                "seller_id": ["s1", "s2", "s1"],
                "shipping_limit_date": ["2023-01-02 10:00:00", "2023-01-02 10:00:00", "2023-02-02 10:00:00"],
                "price": [100.0, 50.0, 75.0],
                "freight_value": [10.0, 5.0, 7.5],
            }
        ),
        CSV_FILES["order_payments"]: pd.DataFrame(
            {
                "order_id": ["o1", "o1", "o2"],
                "payment_sequential": [1, 2, 1],
                "payment_value": [80.0, 70.0, 75.0],
                "payment_installments": [1, 2, 3],
                "payment_type": ["credit_card", "boleto", "credit_card"],
            }
        ),
        CSV_FILES["order_reviews"]: pd.DataFrame(
            {
                "order_id": ["o1", "o1", "o2"],
                "review_id": ["r1", "r2", "r3"],
                "review_score": [5, 4, 3],
            }
        ),
        CSV_FILES["products"]: pd.DataFrame(
            {
                "product_id": ["p1", "p2"],
                "product_category_name": ["cat_a", "cat_b"],
                "product_name_lenght": [10, 20],
                "product_description_lenght": [100, 200],
                "product_photos_qty": [3, 5],
                "product_weight_g": ["100", "200"],
                "product_length_cm": ["10", "20"],
                "product_height_cm": ["5", "10"],
                "product_width_cm": ["5", "10"],
            }
        ),
        CSV_FILES["customers"]: pd.DataFrame(
            {
                "customer_id": ["c1", "c2"],
                "customer_unique_id": ["u1", "u2"],
                "customer_zip_code_prefix": [11111, 22222],
                "customer_city": ["Sao Paulo", "Rio"],
                "customer_state": ["SP", "RJ"],
            }
        ),
        CSV_FILES["sellers"]: pd.DataFrame(
            {
                "seller_id": ["s1", "s2"],
                "seller_zip_code_prefix": [44444, 55555],
                "seller_city": ["Curitiba", "Recife"],
                "seller_state": ["PR", "PE"],
            }
        ),
        CSV_FILES["category_translation"]: pd.DataFrame(
            {
                "product_category_name": ["cat_a", "cat_b"],
                "product_category_name_english": ["category_a", "category_b"],
            }
        ),
    }

    for name, df in files.items():
        df.to_csv(test_dir / name, index=False)

    yield test_dir

    # Limpieza
    import shutil
    shutil.rmtree(test_dir.parent, ignore_errors=True)


def test_transform_dataset_end_to_end(sample_data_dir: Path):
    """Ejecuta el pipeline completo y verifica las salidas."""
    output_dir = Path(__file__).resolve().parents[1] / "tests" / "temp" / "processed"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = transform_dataset(data_dir=sample_data_dir, output_dir=output_dir)

        expected_tables = {"dim_customer", "dim_product", "dim_seller", "dim_date", "fact_sales"}
        assert set(result.keys()) == expected_tables

        for table_name in expected_tables:
            output_path = output_dir / f"{table_name}.csv"
            assert output_path.exists(), f"Falta el archivo de salida: {output_path}"

        fact = pd.read_csv(output_dir / "fact_sales.csv")
        order_items = pd.read_csv(sample_data_dir / CSV_FILES["order_items"])
        assert len(fact) == len(order_items), "fact_sales debe conservar el grano de order_items"
    finally:
        import shutil
        shutil.rmtree(output_dir, ignore_errors=True)


def test_transform_dataset_missing_file_raises():
    """Si falta un CSV de entrada, lanza FileNotFoundError."""
    test_dir = Path(__file__).resolve().parents[1] / "tests" / "temp" / "data_missing"
    test_dir.mkdir(parents=True, exist_ok=True)

    try:
        with pytest.raises(FileNotFoundError, match="olist_orders_dataset.csv"):
            transform_dataset(data_dir=test_dir, output_dir=test_dir.parent / "out")
    finally:
        import shutil
        shutil.rmtree(test_dir.parent, ignore_errors=True)


def test_region_by_state_coverage():
    """Verifica que todos los estados brasileños estén mapeados."""
    brazilian_states = {
        "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
        "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
        "SP", "SE", "TO",
    }
    mapped_states = set(REGION_BY_STATE.keys())
    missing = brazilian_states - mapped_states
    assert not missing, f"Estados sin mapeo regional: {missing}"
