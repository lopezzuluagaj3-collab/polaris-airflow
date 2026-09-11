FROM apache/airflow:3.2.2

USER root
# RUN apt-get update && apt-get install -y --no-install-recommends <paquete> && rm -rf /var/lib/apt/lists/*

USER airflow
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir --user -r /requirements.txt

COPY --chown=airflow:root etl/ /opt/airflow/etl/
COPY --chown=airflow:root sql/ /opt/airflow/sql/