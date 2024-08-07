import enum


class BackendDependencies(enum.Enum):
    AIRFLOW = "AIRFLOW"
    AWS = "AWS"
    AWS_GLUE = "AWS_GLUE"
    ATHENA = "ATHENA"
    AZURE = "AZURE"
    BIGQUERY = "BIGQUERY"
    GCS = "GCS"
    MYSQL = "MYSQL"
    MSSQL = "MSSQL"
    PANDAS = "PANDAS"
    PENDULUM = "PENDULUM"
    POSTGRESQL = "POSTGRESQL"
    REDSHIFT = "REDSHIFT"
    SPARK = "SPARK"
    SQLALCHEMY = "SQLALCHEMY"
    SNOWFLAKE = "SNOWFLAKE"
    TRINO = "TRINO"
