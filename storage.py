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
    


import datetime as dt

from enums import JobStatus


def _list_all(client, prefix: str):
    continuation = None
    while True:
        kwargs = {"Bucket": settings.S3_BUCKET, "Prefix": prefix}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            yield obj
        if resp.get("IsTruncated"):
            continuation = resp.get("NextContinuationToken")
        else:
            break


def sweep_expired():
    """Limpeza oportunista do bucket -- não é um cron de verdade, roda no
    melhor esforço sempre que alguém acessa o site (/health) ou começa uma
    conversão nova (/api/uploads/presign). Isso faz o bucket convergir pra
    vazio mesmo que o processo do backend tenha reiniciado entre o fim de
    uma conversão e a limpeza."""
    client = get_s3_client()
    now = dt.datetime.now(dt.timezone.utc)
    output_cutoff = now - dt.timedelta(seconds=settings.OUTPUT_RETENTION_SECONDS)
    upload_cutoff = now - dt.timedelta(seconds=settings.ORPHAN_UPLOAD_RETENTION_SECONDS)

    # outputs/: a idade do próprio arquivo já é um proxy seguro -- só
    # existe depois que a conversão termina de verdade.
    for obj in _list_all(client, "outputs/"):
        if obj["LastModified"] < output_cutoff:
            try:
                client.delete_object(Bucket=settings.S3_BUCKET, Key=obj["Key"])
            except Exception:
                pass

    # uploads/: janela bem maior, só pra pegar órfãos (upload feito mas
    # /api/convert nunca chamado, ou o processo caiu no meio do caminho).
    # O caso normal -- conversão concluída -- já é limpo direto pelo
    # worker.py, sem depender dessa varredura.
    for obj in _list_all(client, "uploads/"):
        if obj["LastModified"] < upload_cutoff:
            try:
                client.delete_object(Bucket=settings.S3_BUCKET, Key=obj["Key"])
            except Exception:
                pass

    # jobs/: só apaga registros já finalizados (done/error) e expirados --
    # nunca um job pending/processing, mesmo que a data de criação pareça
    # "velha" (conversão de arquivo grande pode legitimamente demorar).
    for obj in _list_all(client, "jobs/"):
        if obj["LastModified"] >= output_cutoff:
            continue
        job = get_json(obj["Key"])
        if job and job.get("status") in (JobStatus.DONE.value, JobStatus.ERROR.value):
            try:
                client.delete_object(Bucket=settings.S3_BUCKET, Key=obj["Key"])
            except Exception:
                pass
