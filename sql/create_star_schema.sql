-- Modelo estrella para las salidas de etl/transform.py.
-- PostgreSQL 14+.
--
-- Las tablas finales tienen el mismo nombre y columnas que data/processed/*.csv,
-- por lo que pueden cargarse con COPY o con pandas.to_sql sin transformar de
-- nuevo los datos. La fact_sales conserva el grano order_id + order_item_id.

BEGIN;

CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.dim_customer (
	customer_key INTEGER PRIMARY KEY,
	customer_id VARCHAR(32) NOT NULL UNIQUE,
	customer_unique_id VARCHAR(32) NOT NULL,
	customer_zip_code_prefix INTEGER NOT NULL,
	customer_city VARCHAR(120) NOT NULL,
	customer_state CHAR(2) NOT NULL,
	customer_region VARCHAR(20) NOT NULL,
	CONSTRAINT dim_customer_region_ck
		CHECK (customer_region IN ('Norte', 'Nordeste', 'Centro-Oeste', 'Sudeste', 'Sul', 'unknown'))
);

CREATE TABLE IF NOT EXISTS analytics.dim_product (
	product_key INTEGER PRIMARY KEY,
	product_id VARCHAR(32) NOT NULL UNIQUE,
	product_category_name VARCHAR(100) NOT NULL,
	product_category_name_english VARCHAR(100) NOT NULL,
	product_name_lenght INTEGER,
	product_description_lenght INTEGER,
	product_photos_qty INTEGER,
	product_weight_g NUMERIC(12, 2),
	product_length_cm NUMERIC(10, 2),
	product_height_cm NUMERIC(10, 2),
	product_width_cm NUMERIC(10, 2),
	CONSTRAINT dim_product_non_negative_ck CHECK (
		COALESCE(product_name_lenght, 0) >= 0
		AND COALESCE(product_description_lenght, 0) >= 0
		AND COALESCE(product_photos_qty, 0) >= 0
		AND COALESCE(product_weight_g, 0) >= 0
		AND COALESCE(product_length_cm, 0) >= 0
		AND COALESCE(product_height_cm, 0) >= 0
		AND COALESCE(product_width_cm, 0) >= 0
	)
);

CREATE TABLE IF NOT EXISTS analytics.dim_seller (
	seller_key INTEGER PRIMARY KEY,
	seller_id VARCHAR(32) NOT NULL UNIQUE,
	seller_zip_code_prefix INTEGER NOT NULL,
	seller_city VARCHAR(120) NOT NULL,
	seller_state CHAR(2) NOT NULL,
	seller_region VARCHAR(20) NOT NULL,
	CONSTRAINT dim_seller_region_ck
		CHECK (seller_region IN ('Norte', 'Nordeste', 'Centro-Oeste', 'Sudeste', 'Sul', 'unknown'))
);

CREATE TABLE IF NOT EXISTS analytics.dim_date (
	date_key INTEGER PRIMARY KEY,
	date DATE NOT NULL UNIQUE,
	year SMALLINT NOT NULL,
	quarter SMALLINT NOT NULL CHECK (quarter BETWEEN 1 AND 4),
	month SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
	month_name VARCHAR(20) NOT NULL,
	week SMALLINT NOT NULL CHECK (week BETWEEN 1 AND 53),
	day SMALLINT NOT NULL CHECK (day BETWEEN 1 AND 31),
	day_of_week SMALLINT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
	day_name VARCHAR(20) NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics.fact_sales (
	order_id VARCHAR(32) NOT NULL,
	order_item_id SMALLINT NOT NULL,
	product_id VARCHAR(32) NOT NULL,
	seller_id VARCHAR(32) NOT NULL,
	customer_id VARCHAR(32) NOT NULL,
	order_status VARCHAR(20) NOT NULL,
	shipping_limit_date TIMESTAMPTZ,
	order_purchase_timestamp TIMESTAMPTZ NOT NULL,
	order_approved_at TIMESTAMPTZ,
	order_delivered_carrier_date TIMESTAMPTZ,
	order_delivered_customer_date TIMESTAMPTZ,
	order_estimated_delivery_date TIMESTAMPTZ NOT NULL,
	purchase_date_key INTEGER NOT NULL,
	estimated_delivery_date_key INTEGER NOT NULL,
	price NUMERIC(14, 2) NOT NULL CHECK (price >= 0),
	freight_value NUMERIC(14, 2) NOT NULL CHECK (freight_value >= 0),
	sales_value NUMERIC(14, 2) NOT NULL CHECK (sales_value >= 0),
	freight_total NUMERIC(14, 2) NOT NULL CHECK (freight_total >= 0),
	item_count SMALLINT NOT NULL CHECK (item_count > 0),
	delivery_days NUMERIC(12, 4),
	estimated_days NUMERIC(12, 4),
	is_late BOOLEAN,
	payment_value_total NUMERIC(14, 2),
	payment_count SMALLINT,
	payment_installments_max SMALLINT,
	payment_types VARCHAR(120),
	review_score_average NUMERIC(4, 2),
	review_count INTEGER,
	CONSTRAINT fact_sales_pk PRIMARY KEY (order_id, order_item_id),
	CONSTRAINT fact_sales_order_item_ck CHECK (order_item_id > 0),
	CONSTRAINT fact_sales_review_ck CHECK (
		review_score_average IS NULL OR review_score_average BETWEEN 1 AND 5
	),
	CONSTRAINT fact_sales_purchase_date_fk FOREIGN KEY (purchase_date_key)
		REFERENCES analytics.dim_date (date_key),
	CONSTRAINT fact_sales_estimated_date_fk FOREIGN KEY (estimated_delivery_date_key)
		REFERENCES analytics.dim_date (date_key),
	CONSTRAINT fact_sales_customer_fk FOREIGN KEY (customer_id)
		REFERENCES analytics.dim_customer (customer_id),
	CONSTRAINT fact_sales_product_fk FOREIGN KEY (product_id)
		REFERENCES analytics.dim_product (product_id),
	CONSTRAINT fact_sales_seller_fk FOREIGN KEY (seller_id)
		REFERENCES analytics.dim_seller (seller_id)
);

CREATE INDEX IF NOT EXISTS fact_sales_purchase_date_idx
	ON analytics.fact_sales (purchase_date_key);
CREATE INDEX IF NOT EXISTS fact_sales_customer_idx
	ON analytics.fact_sales (customer_id);
CREATE INDEX IF NOT EXISTS fact_sales_product_idx
	ON analytics.fact_sales (product_id);
CREATE INDEX IF NOT EXISTS fact_sales_seller_idx
	ON analytics.fact_sales (seller_id);
CREATE INDEX IF NOT EXISTS fact_sales_status_idx
	ON analytics.fact_sales (order_status);

-- Vista de métricas a nivel pedido. Se agrupa antes de sumar pagos porque
-- payment_value_total se repite en todas las líneas del mismo pedido.
CREATE OR REPLACE VIEW analytics.v_order_metrics AS
SELECT
	order_id,
	MIN(customer_id) AS customer_id,
	MIN(order_status) AS order_status,
	MIN(order_purchase_timestamp) AS order_purchase_timestamp,
	MAX(purchase_date_key) AS purchase_date_key,
	SUM(price) AS sales_value,
	SUM(freight_value) AS freight_value,
	MAX(item_count) AS item_count,
	MAX(payment_value_total) AS payment_value_total,
	MAX(payment_count) AS payment_count,
	MAX(review_score_average) AS review_score_average,
	MAX(delivery_days) AS delivery_days,
	MAX(estimated_days) AS estimated_days,
	BOOL_OR(is_late) AS is_late
FROM analytics.fact_sales
GROUP BY order_id;

-- Vista para consumo directo desde Power BI.
CREATE OR REPLACE VIEW analytics.v_sales_by_date_category AS
SELECT
	f.purchase_date_key,
	d.date,
	d.year,
	d.month,
	d.month_name,
	p.product_category_name_english AS product_category,
	COUNT(DISTINCT f.order_id) AS orders,
	COUNT(*) AS item_lines,
	SUM(f.sales_value) AS sales_value,
	SUM(f.freight_total) AS freight_value,
	AVG(f.review_score_average) AS average_review_score
FROM analytics.fact_sales AS f
JOIN analytics.dim_date AS d ON d.date_key = f.purchase_date_key
JOIN analytics.dim_product AS p ON p.product_id = f.product_id
GROUP BY
	f.purchase_date_key, d.date, d.year, d.month, d.month_name,
	p.product_category_name_english;

COMMIT;

-- Carga de las salidas generadas por etl/transform.py (ejecutar desde psql).
-- El orden respeta las claves foráneas:
-- \copy analytics.dim_customer FROM 'data/processed/dim_customer.csv' WITH (FORMAT csv, HEADER true)
-- \copy analytics.dim_product FROM 'data/processed/dim_product.csv' WITH (FORMAT csv, HEADER true)
-- \copy analytics.dim_seller FROM 'data/processed/dim_seller.csv' WITH (FORMAT csv, HEADER true)
-- \copy analytics.dim_date FROM 'data/processed/dim_date.csv' WITH (FORMAT csv, HEADER true)
-- \copy analytics.fact_sales FROM 'data/processed/fact_sales.csv' WITH (FORMAT csv, HEADER true)

-- Consultas de control posteriores a la carga:
-- SELECT COUNT(*) AS fact_rows FROM analytics.fact_sales;
-- SELECT COUNT(*) AS duplicate_line_keys
-- FROM (SELECT order_id, order_item_id FROM analytics.fact_sales
--       GROUP BY order_id, order_item_id HAVING COUNT(*) > 1) AS duplicated;
-- SELECT COUNT(*) AS orphan_products
-- FROM analytics.fact_sales f LEFT JOIN analytics.dim_product p USING (product_id)
-- WHERE p.product_id IS NULL;