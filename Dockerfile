FROM apache/airflow:3.2.2

USER 0
# RUN apt-get update && apt-get install -y --no-install-recommends <paquete> && rm -rf /var/lib/apt/lists/*

USER 50000
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

COPY --chown=50000:0 etl/ /opt/airflow/etl/
COPY --chown=50000:0 sql/ /opt/airflow/sql/