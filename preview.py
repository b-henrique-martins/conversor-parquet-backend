import duckdb

from config import settings

MAX_PREVIEW_ROWS = 200
MAX_PREVIEW_COLUMNS = 50


class PreviewError(Exception):
    pass


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    con.execute("INSTALL httpfs;")
    con.execute("LOAD httpfs;")

    endpoint = settings.S3_ENDPOINT.replace("https://", "").replace("http://", "")
    con.execute(f"SET s3_endpoint='{endpoint}';")
    con.execute(f"SET s3_access_key_id='{settings.S3_ACCESS_KEY_ID}';")
    con.execute(f"SET s3_secret_access_key='{settings.S3_SECRET_ACCESS_KEY}';")
    con.execute(f"SET s3_region='{settings.S3_REGION}';")
    con.execute("SET s3_url_style='path';")
    con.execute(f"SET memory_limit='{settings.DUCKDB_MEMORY_LIMIT}';")
    con.execute(f"SET threads TO {settings.DUCKDB_THREADS};")

    return con


def _assert_safe_uri(uri: str):
    """Mesma defesa usada em converter.py -- as URIs aqui vêm sempre de
    chaves internas (uuid4 + extensão validada), nunca de texto arbitrário
    do cliente."""
    if "'" in uri or ";" in uri:
        raise PreviewError("URI de arquivo inválida")


def _serialize(value):
    """DuckDB pode devolver Decimal, date, datetime etc -- normaliza pra
    algo que o json padrão do FastAPI serializa sem reclamar."""
    if value is None or isinstance(value, (int, float, bool, str)):
        return value
    return str(value)


def preview_file(uri: str, ext: str, limit: int = 100) -> dict:
    """Lê só as primeiras `limit` linhas -- nunca baixa o arquivo inteiro.
    Para parquet, também tenta a contagem total (barata: usa metadata do
    arquivo). Para CSV, contar tudo exigiria ler o arquivo inteiro -- o
    custo que esse recurso existe pra evitar -- então fica None."""
    limit = max(1, min(limit, MAX_PREVIEW_ROWS))
    _assert_safe_uri(uri)

    con = _connect()
    try:
        if ext == "parquet":
            source = f"read_parquet('{uri}')"
        else:
            source = f"read_csv('{uri}', sample_size=-1, all_varchar=true)"

        rows = con.execute(f"SELECT * FROM {source} LIMIT {limit}").fetchall()
        columns = [d[0] for d in con.description]

        truncated_columns = False
        if len(columns) > MAX_PREVIEW_COLUMNS:
            columns = columns[:MAX_PREVIEW_COLUMNS]
            rows = [r[:MAX_PREVIEW_COLUMNS] for r in rows]
            truncated_columns = True

        total_rows = None
        if ext == "parquet":
            try:
                total_rows = con.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0]
            except Exception:
                total_rows = None

        return {
            "columns": columns,
            "rows": [[_serialize(v) for v in row] for row in rows],
            "row_count_returned": len(rows),
            "row_count_total": total_rows,
            "truncated_columns": truncated_columns,
        }
    finally:
        con.close()
