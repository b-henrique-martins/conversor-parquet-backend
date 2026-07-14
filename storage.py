import json
import uuid

import boto3
from botocore.client import Config

from config import settings

_s3_client = None

# Únicas extensões que o pipeline sabe converter. Qualquer coisa fora disso
# é rejeitada -- isso também impede que uma extensão maliciosa vinda do
# cliente vire parte de uma URI interpolada em SQL do DuckDB mais adiante.
ALLOWED_EXTENSIONS = {"csv", "parquet"}


def get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            region_name=settings.S3_REGION,
            config=Config(signature_version="s3v4"),
        )
    return _s3_client


def sanitize_extension(original_filename: str) -> str:
    """Valida a extensão contra uma whitelist. Levanta ValueError se não for
    .csv ou .parquet -- nunca interpole a extensão crua do cliente em uma
    chave de storage sem passar por aqui."""
    ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Extensão de arquivo não suportada (use .csv ou .parquet)")
    return ext


def new_object_key(original_filename: str) -> str:
    """Gera uma chave única e segura: uuid4 (hex/dashes apenas) + extensão
    validada. Nunca usa texto arbitrário do cliente na chave."""
    ext = sanitize_extension(original_filename)
    return f"uploads/{uuid.uuid4()}.{ext}"


def new_output_key(ext: str) -> str:
    assert ext in ALLOWED_EXTENSIONS
    return f"outputs/{uuid.uuid4()}.{ext}"


def presign_put(key: str, content_type: str = "application/octet-stream") -> str:
    client = get_s3_client()
    return client.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=settings.PRESIGN_EXPIRES_SECONDS,
    )


def presign_get(key: str) -> str:
    client = get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": key},
        ExpiresIn=settings.PRESIGN_EXPIRES_SECONDS,
    )


def object_exists(key: str) -> bool:
    client = get_s3_client()
    try:
        client.head_object(Bucket=settings.S3_BUCKET, Key=key)
        return True
    except client.exceptions.ClientError:
        return False


def object_size(key: str) -> int | None:
    """Retorna o tamanho em bytes do objeto, ou None se não existir."""
    client = get_s3_client()
    try:
        resp = client.head_object(Bucket=settings.S3_BUCKET, Key=key)
        return resp["ContentLength"]
    except client.exceptions.ClientError:
        return None


def delete_object(key: str):
    client = get_s3_client()
    client.delete_object(Bucket=settings.S3_BUCKET, Key=key)


def s3_uri(key: str) -> str:
    return f"s3://{settings.S3_BUCKET}/{key}"


# --------------------------------------------------------------------------
# Armazenamento simples de estado de job em JSON, reaproveitando o mesmo
# bucket. Evita depender de um banco/Redis separado para um MVP -- se o
# volume de conversões crescer muito, trocar por Redis é o próximo passo.
# --------------------------------------------------------------------------
def put_json(key: str, data: dict):
    client = get_s3_client()
    client.put_object(
        Bucket=settings.S3_BUCKET,
        Key=key,
        Body=json.dumps(data, default=str).encode("utf-8"),
        ContentType="application/json",
    )


def get_json(key: str) -> dict | None:
    client = get_s3_client()
    try:
        obj = client.get_object(Bucket=settings.S3_BUCKET, Key=key)
        return json.loads(obj["Body"].read())
    except client.exceptions.ClientError:
        return None