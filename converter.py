import duckdb

from config import settings
from enums import Direction
from decimal_detect import detect_decimal_overrides, types_clause


class ConversionError(Exception):
    pass


def _connect() -> duckdb.DuckDBPyConnection:
    """Abre uma conexão DuckDB configurada para ler/escrever direto no B2
    (S3-compatible) e com limites de memória seguros para instâncias pequenas."""
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
    con.execute("SET preserve_insertion_order=false;")

    return con


def _assert_safe_uri(uri: str):
    """Defesa extra: as URIs aqui são sempre montadas internamente a partir
    de chaves geradas por storage.py (uuid4 + extensão validada), nunca a
    partir de texto arbitrário do cliente. Isso é só um cinto-e-suspensório
    contra interpolação de string em SQL -- se algo escapar desse formato,
    aborta em vez de deixar seguir para o DuckDB."""
    if "'" in uri or ";" in uri:
        raise ConversionError("URI de arquivo inválida")


def convert(
    input_uri: str,
    output_uri: str,
    direction: Direction,
    preserve_decimals: bool = True,
) -> tuple[int, int, dict]:
    """Executa a conversão em uma única passada (sem re-ler o arquivo de
    entrada nem o de saída para contar linhas -- a própria instrução COPY
    já devolve o número de linhas escritas).

    Nota importante: essa garantia de "nenhuma linha perdida" depende de
    NÃO usar `ignore_errors=true` no read_csv. Sem essa opção, qualquer
    linha malformada faz o DuckDB levantar uma exceção em vez de descartar
    silenciosamente -- então row_count_in == row_count_out por construção
    quando a conversão termina sem erro. Se um dia precisar tolerar linhas
    malformadas com ignore_errors, volte a validar a contagem de saída
    separadamente.

    Retorna (row_count_in, row_count_out, decimal_overrides_aplicados).
    """
    _assert_safe_uri(input_uri)
    _assert_safe_uri(output_uri)

    con = _connect()
    try:
        decimal_overrides: dict[str, str] = {}

        if direction == Direction.PARQUET_TO_CSV:
            copy_result = con.execute(f"""
                COPY (SELECT * FROM read_parquet('{input_uri}'))
                TO '{output_uri}' (HEADER, DELIMITER ',', FORCE_QUOTE *);
            """).fetchone()
        else:
            if preserve_decimals:
                decimal_overrides = detect_decimal_overrides(con, input_uri)
            extra = types_clause(decimal_overrides)

            copy_result = con.execute(f"""
                COPY (SELECT * FROM read_csv('{input_uri}', sample_size=-1{extra}))
                TO '{output_uri}' (FORMAT PARQUET, COMPRESSION ZSTD);
            """).fetchone()

        if not copy_result:
            raise ConversionError("COPY não retornou contagem de linhas")

        row_count = copy_result[0]
        return row_count, row_count, decimal_overrides
    finally:
        con.close()