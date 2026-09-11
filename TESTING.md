# Testing en polaris-airflow

## Tipos de pruebas: ¿estáticas o dinámicas?

Las pruebas creadas en este proyecto **son pruebas dinámicas** (también llamadas pruebas de ejecución o *runtime tests*).

| Tipo | ¿Qué significa? | ¿Este proyecto? |
|---|---|---|
| **Estáticas** | Analizan el código **sin ejecutarlo**. Ej: linters (`ruff`, `flake8`), type-checkers (`mypy`), análisis de dependencias. No detectan errores de lógica en tiempo de ejecución. | No. |
| **Dinámicas** | **Ejecutan** el código real y verifican su comportamiento en runtime. Capturan errores de lógica, integración y comportamiento inesperado. | **Sí**. Usamos `pytest` con `unittest.mock`. |

Las pruebas son **dinámicas** porque:
- Importan y llaman las funciones reales de los módulos ETL.
- Ejecutan código Python (pandas, psycopg2, subprocess) contra datos de prueba.
- Verifican resultados con `assert` en tiempo de ejecución.

## Arquitectura de las pruebas

```
tests/
├── test_extract.py         → etl/extract/kaggle_downloader.py
├── test_transform.py       → etl/transform/clean_orders.py
├── test_postgres_loader.py → etl/load/postgres_loader.py
└── test_load.py            → etl/load/load_to_postgres.py
```

Cada archivo de test cubre un módulo del pipeline ETL. Se ejecutan con:

```bash
python -m pytest tests/ -v
```

### Framework usado

| Herramienta | Propósito |
|---|---|
| **pytest** (`9.1.1`) | Orquestador de tests: descubre, ejecuta y reporta. Proporciona `tmp_path`, `monkeypatch`, `pytest.raises`, `pytest.mark.parametrize`. |
| **unittest.mock** (stdlib) | `patch`, `MagicMock` — reemplazan dependencias externas (psycopg2, subprocess, variables de entorno) sin tocar código real. |
| **pandas** | Creación de DataFrames de ejemplo y lectura/escritura de CSVs temporales. |

No se requiere `pytest-mock` (plugin externo); `unittest.mock` de la stdlib es suficiente.

## Cómo funcionan internamente

### 1. Fixtures de pytest (`@pytest.fixture`)

Las fixtures son funciones que proporcionan datos o contexto a los tests. Se identifican por su nombre como parámetro de la función de test:

```python
@pytest.fixture
def sample_customers() -> pd.DataFrame:
    return pd.DataFrame({"customer_id": ["c1", "c2"], ...})

def test_build_dim_customer(sample_customers):
    dim = build_dim_customer(sample_customers)
    assert len(dim) == 2
```

`pytest` resuelve `sample_customers` ejecutando la fixture antes del test. La fixture `tmp_path` (integrada) crea un directorio temporal único por test y lo limpia al finalizar.

### 2. Mocking con `unittest.mock`

El patrón básico:

```python
from unittest.mock import patch, MagicMock

with patch("etl.load.postgres_loader.get_database_connection", return_value=mock_conn):
    create_database_model(models_sql=sql_file)
```

- `patch("ruta.completa.del.objeto")` **reemplaza** el objeto durante el `with` y lo restaura al salir.
- `return_value` hace que la función mockeada devuelva `mock_conn` (un `MagicMock`).
- `mock_conn.cursor.return_value.__enter__.return_value = mock_cursor` configura el protocolo de contexto (`with connection.cursor() as cursor:`) para devolver un cursor mockeado.

#### Por qué se mock

| Dependencia | Razón para mockear |
|---|---|
| `subprocess.run` (kaggle) | No se debe descargar nada de internet en tests. |
| `psycopg2.connect` | No hay servidor PostgreSQL en CI. |
| `execute_values` | No se debe escribir a una BD real. |
| `os.environ` / `sys.argv` | Control total del entorno y argumentos CLI. |
| `sql.Composed.as_string` | `psycopg2` valida que el contexto sea una conexión/cursor real (`TypeError` si no). Como usamos un `MagicMock`, se mockea para evitar el error. |

### 3. Parametrización con `@pytest.mark.parametrize`

Genera múltiples casos de test a partir de una sola función:

```python
@pytest.mark.parametrize("batch_size_arg", ["0", "-1", "-100"])
def test_main_raises_on_non_positive_batch_size(monkeypatch, batch_size_arg):
    monkeypatch.setattr(sys, "argv", ["x", "--batch-size", batch_size_arg])
    with pytest.raises(ValueError, match="mayor que cero"):
        main()
```

Ejecuta 3 tests: uno por cada valor de `batch_size_arg`.

### 4. `pytest.raises` — assert de excepciones

```python
with pytest.raises(FileNotFoundError, match="No existe"):
    transform_dataset(data_dir=empty_dir, output_dir=tmp_path)
```

Verifica que la función lance la excepción esperada y que el mensaje coincida con el patrón.

### 5. `monkeypatch` — fixture integrada de pytest

```python
monkeypatch.setenv("KAGGLE_API_TOKEN", "fake-token")  # set env var
monkeypatch.delenv("DATABASE_URL", raising=False)       # delete env var
monkeypatch.setattr(sys, "argv", ["script.py", "--batch-size", "100"])  # modify argv
```

Restaura automáticamente los valores originales al finalizar el test.

## Patrones de test por tipo

### Tests unitarios (funciones individuales)

Verifican una función aislada con datos de entrada controlados:

```python
def test_build_dim_customer(sample_customers):
    dim = build_dim_customer(sample_customers)
    assert set(dim["customer_key"]) == {1, 2, 3}
    assert dim["customer_region"].iloc[0] == "Sudeste"  # SP → Sul
```

**Qué se prueba:** Que la función transforma los datos correctamente: genera claves sustitutas (`customer_key`), mapea regiones, conserva columnas esperadas.

### Tests de integración (pipeline completo)

Ejecutan el pipeline end-to-end con datos mínimos pero válidos:

```python
def test_transform_dataset_end_to_end(sample_data_dir, tmp_path):
    output_dir = tmp_path / "processed"
    result = transform_dataset(data_dir=sample_data_dir, output_dir=output_dir)
    assert set(result.keys()) == {"dim_customer", "dim_product", ...}
    # Verifica archivos de salida
    for table_name in result:
        assert (output_dir / f"{table_name}.csv").exists()
```

**Qué se prueba:** Que todo el pipeline (carga de CSVs → transformaciones → escritura de salidas) funciona de forma integrada y respeta el grano de `fact_sales`.

### Tests de transacciones (commit/rollback)

Verifican el comportamiento de PostgreSQL transaccional:

```python
def test_create_database_model_commits_on_success(self, sample_sql_file):
    conn = _make_mock_connection()
    with patch(..., return_value=conn):
        create_database_model(models_sql=sample_sql_file)
    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()

def test_create_database_model_rolls_back_on_error(self, sample_sql_file):
    conn = _make_mock_connection()
    with patch(..., side_effect=RuntimeError("schema error")):
        with pytest.raises(RuntimeError):
            create_database_model(models_sql=sample_sql_file)
    conn.rollback.assert_called_once()
```

**Qué se prueba:** Que en caso de éxito se hace `commit`, y ante error se hace `rollback` (atomicidad ACID).

### Tests de CLI (argument parsing)

Simulan la línea de comandos modificando `sys.argv`:

```python
def test_main_raises_on_non_positive_batch_size(monkeypatch, batch_size_arg):
    monkeypatch.setattr(sys, "argv", ["x", "--batch-size", batch_size_arg])
    with pytest.raises(ValueError, match="mayor que cero"):
        main()
```

**Qué se prueba:** Que los argumentos de línea de comandos se parsean correctamente y que las validaciones de entrada funcionan.

## Convenciones del proyecto

1. **Imports absolutos desde la raíz del proyecto:** Los tests importan como `from etl.extract.kaggle_downloader import download_dataset`. El DAG inserta `PROJECT_ROOT` en `sys.path`; los tests confían en que pytest ejecuta desde la raíz (`rootdir`).

2. **Datos de ejemplo mínimos pero válidos:** Cada fixture crea DataFrames con las columnas exactas que los módulos esperan. Los datos no necesitan ser realistas; deben ser suficientemente consistentes para pasar las validaciones de `merge`, `validate="many_to_one"`, y los `groupby`.

3. **Aislamiento:** Cada test usa `tmp_path` (directorio temporal) o fixtures nuevas. No se comparten estados entre tests.

4. **Mocking selectivo:** Solo se mockan las dependencias externas (BD, internet, env vars). La lógica de negocio (transformaciones de pandas) se ejecuta con datos reales.

5. **Clases organizativas:** Tests relacionados se agrupan en clases (`TestModelExists`, `TestLoadCsvIdempotently`) para mejorar la legibilidad del reporte de pytest.
