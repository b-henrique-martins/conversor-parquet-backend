import re

import duckdb

_DECIMAL_RE = re.compile(r"^-?\d+\.\d+$")
_SCIENTIFIC_RE = re.compile(r"[eE][+-]?\d+")

MAX_DECIMAL_PRECISION = 38


def detect_decimal_overrides(
    con: duckdb.DuckDBPyConnection,
    uri: str,
    sample_rows: int = 200_000,
) -> dict[str, str]:
    """Amostra as primeiras `sample_rows` linhas do CSV como VARCHAR e tenta
    identificar colunas que são números decimais de escala fixa (ex:
    "1234.50"). O sniffer padrão do DuckDB não detecta DECIMAL -- só
    considera BIGINT/DOUBLE/etc -- então uma coluna assim vira DOUBLE por
    padrão, o que introduz erro de ponto flutuante em valores monetários.

    Retorna um dict {coluna: 'DECIMAL(p, s)'} pronto para o parâmetro
    `types=` do read_csv. Em caso de amostra ambígua (notação científica,
    valores não uniformes, etc.) a coluna é omitida e cai no comportamento
    padrão do DuckDB.
    """
    try:
        rows = con.execute(
            f"SELECT * FROM read_csv('{uri}', all_varchar=true, sample_size={sample_rows})"
            f" LIMIT {sample_rows}"
        ).fetchall()
        columns = [d[0] for d in con.description]
    except Exception:
        # Amostragem falhou (arquivo vazio, formato inesperado etc.) --
        # não força nada, cai no comportamento padrão.
        return {}

    if not rows:
        return {}

    overrides: dict[str, str] = {}
    for col_idx, col_name in enumerate(columns):
        values = [r[col_idx] for r in rows if r[col_idx] is not None]
        if not values:
            continue

        if any(_SCIENTIFIC_RE.search(v) for v in values):
            continue  # notação científica -- não arriscar, deixa DOUBLE

        if not all(_DECIMAL_RE.match(v) for v in values):
            continue  # nem todo valor é decimal fixo (pode ser data, texto, inteiro etc.)

        max_scale = max(len(v.split(".")[1]) for v in values)
        max_int_digits = max(len(v.split(".")[0].lstrip("-")) for v in values)

        scale = min(max_scale, MAX_DECIMAL_PRECISION - 1)
        precision = min(MAX_DECIMAL_PRECISION, max(max_int_digits + scale, scale + 1))

        overrides[col_name] = f"DECIMAL({precision},{scale})"

    return overrides


def types_clause(overrides: dict[str, str]) -> str:
    """Monta o fragmento SQL `, types={...}` a partir do dict de overrides,
    ou string vazia se não houver overrides."""
    if not overrides:
        return ""
    # Nomes de coluna vêm do cabeçalho do CSV (não confiável) -- escapa
    # aspas simples dobrando-as, como no padrão de string literal do SQL.
    literal = ", ".join(
        f"'{col.replace(chr(39), chr(39) * 2)}': '{typ}'" for col, typ in overrides.items()
    )
    return f", types={{{literal}}}"