import logging

import converter
import jobs
import storage
from config import settings
from enums import Direction, JobStatus

logger = logging.getLogger("worker")


def run_conversion_job(
    job_id: str,
    input_key: str,
    original_filename: str,
    direction: Direction,
    preserve_decimals: bool,
):
    jobs.update(job_id, status=JobStatus.PROCESSING.value)

    ext = "csv" if direction == Direction.PARQUET_TO_CSV else "parquet"
    output_key = storage.new_output_key(ext)

    try:
        size = storage.object_size(input_key)
        if size is None:
            raise RuntimeError("Arquivo de entrada não encontrado no storage")
        if size > settings.MAX_UPLOAD_SIZE_BYTES:
            raise RuntimeError(
                f"Arquivo excede o limite de {settings.MAX_UPLOAD_SIZE_BYTES // (1024**3)}GB"
            )

        input_uri = storage.s3_uri(input_key)
        output_uri = storage.s3_uri(output_key)

        row_count_in, row_count_out, decimal_overrides = converter.convert(
            input_uri, output_uri, direction, preserve_decimals=preserve_decimals
        )

        jobs.update(
            job_id,
            status=JobStatus.DONE.value,
            original_filename=original_filename,
            direction=direction.value if hasattr(direction, "value") else direction,
            row_count_in=row_count_in,
            row_count_out=row_count_out,
            decimal_columns_preserved=list(decimal_overrides.keys()),
            download_url=storage.presign_get(output_key),
            output_key=output_key,
        )

    except Exception as e:
        logger.exception("Falha ao processar job %s", job_id)
        if storage.object_exists(output_key):
            try:
                storage.delete_object(output_key)
            except Exception:
                pass
        jobs.update(job_id, status=JobStatus.ERROR.value, error=str(e))

    finally:
        # o arquivo de entrada não serve mais pra nada, sucesso ou não --
        # antes só era apagado no caminho feliz, deixando lixo no bucket
        # sempre que uma conversão falhava.
        try:
            storage.delete_object(input_key)
        except Exception:
            pass
