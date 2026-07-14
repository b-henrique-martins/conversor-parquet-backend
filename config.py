import os


class Settings:
    # --- Storage (Backblaze B2, compatível com S3) ---
    S3_ENDPOINT = os.getenv("S3_ENDPOINT")
    S3_BUCKET = os.getenv("S3_BUCKET")
    S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID")
    S3_SECRET_ACCESS_KEY = os.getenv("S3_SECRET_ACCESS_KEY")
    S3_REGION = os.getenv("S3_REGION", "auto")

    # --- Limites do DuckDB (importante p/ caber no free/starter tier do Render) ---
    DUCKDB_MEMORY_LIMIT = os.getenv("DUCKDB_MEMORY_LIMIT", "400MB")
    DUCKDB_THREADS = os.getenv("DUCKDB_THREADS", "2")

    # --- Presigned URLs ---
    PRESIGN_EXPIRES_SECONDS = int(os.getenv("PRESIGN_EXPIRES_SECONDS", "3600"))

    # --- CORS / validação de origem ---
    ALLOWED_ORIGINS = [
        o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    ]

    # --- Limites de abuso / tamanho ---
    # Tamanho máximo aceito por conversão. Ajuste conforme seu plano de storage.
    MAX_UPLOAD_SIZE_BYTES = int(os.getenv("MAX_UPLOAD_SIZE_BYTES", str(25 * 1024 * 1024 * 1024)))  # 25GB

    # Rate limit básico (mitiga abuso, não substitui autenticação de verdade)
    RATE_LIMIT_PRESIGN = os.getenv("RATE_LIMIT_PRESIGN", "20/minute")
    RATE_LIMIT_CONVERT = os.getenv("RATE_LIMIT_CONVERT", "10/minute")

    # --- Precisão decimal ---
    # Se true, tenta detectar colunas decimais em CSV->Parquet para evitar
    # que o DuckDB infira DOUBLE (introduzindo erro de ponto flutuante).
    PRESERVE_DECIMALS_DEFAULT = os.getenv("PRESERVE_DECIMALS_DEFAULT", "true").lower() == "true"


settings = Settings()