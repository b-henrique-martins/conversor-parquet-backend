from fastapi import FastAPI, Depends, HTTPException, Header, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import settings
from enums import Direction
import storage
import jobs
import worker

app = FastAPI(title="Parquet <-> CSV Converter")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------
# Rate limit básico por IP. Não substitui autenticação, mas encarece abuso
# automatizado (ex.: alguém disparando presigns/conversões em loop).
# --------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# --------------------------------------------------------------------------
# Validação de origem -- NÃO é autenticação de verdade. Bloqueia navegadores
# de outros sites, não bloqueia scripts que forjam o header diretamente.
# Combinado com o rate limit acima, é uma barreira razoável para um MVP,
# mas não para dados sensíveis.
# --------------------------------------------------------------------------
def require_origin(
    origin: str = Header(default=None),
    referer: str = Header(default=None),
) -> str:
    candidate = origin or referer
    if not candidate:
        raise HTTPException(status_code=403, detail="Origem não identificada")

    candidate_origin = "/".join(candidate.split("/")[:3])
    if candidate_origin not in settings.ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="Origem não autorizada")

    return candidate_origin


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class PresignRequest(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"


class ConvertRequest(BaseModel):
    input_key: str
    original_filename: str
    direction: Direction
    preserve_decimals: bool = settings.PRESERVE_DECIMALS_DEFAULT


# --------------------------------------------------------------------------
# 1. Presign de upload -- o navegador manda o arquivo direto pro B2,
#    sem passar pela RAM/CPU limitada do Render.
# --------------------------------------------------------------------------
@app.post("/api/uploads/presign")
@limiter.limit(settings.RATE_LIMIT_PRESIGN)
def presign_upload(request: Request, req: PresignRequest, origin: str = Depends(require_origin)):
    try:
        key = storage.new_object_key(req.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    url = storage.presign_put(key, req.content_type)
    return {"upload_url": url, "object_key": key}


# --------------------------------------------------------------------------
# 2. Conversão assíncrona -- a requisição só enfileira o job e devolve
#    imediatamente. A conversão em si roda em background (fora do ciclo
#    request/response), então arquivos grandes não esbarram no timeout de
#    requisição do Render. O frontend consulta /api/jobs/{id} via polling.
# --------------------------------------------------------------------------
@app.post("/api/convert", status_code=202)
@limiter.limit(settings.RATE_LIMIT_CONVERT)
def start_convert(
    request: Request,
    req: ConvertRequest,
    background_tasks: BackgroundTasks,
    origin: str = Depends(require_origin),
):
    if not req.input_key.startswith("uploads/"):
        raise HTTPException(status_code=400, detail="Chave de entrada inválida")

    if not storage.object_exists(req.input_key):
        raise HTTPException(status_code=400, detail="Arquivo de entrada não encontrado no storage")

    job_id = jobs.new_job_id()
    jobs.create(job_id, {
        "direction": req.direction.value,
        "original_filename": req.original_filename,
    })

    background_tasks.add_task(
        worker.run_conversion_job,
        job_id,
        req.input_key,
        req.original_filename,
        req.direction,
        req.preserve_decimals,
    )

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return job


@app.get("/health")
def health():
    return {"status": "ok"}