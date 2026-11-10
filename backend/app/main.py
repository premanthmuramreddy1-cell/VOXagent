import json
import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, routes, ws
from app.config import settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"ts": self.formatTime(record), "level": record.levelname, "msg": record.getMessage()}
        entry.update(getattr(record, "fields", {}))
        return json.dumps(entry)


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
log = logging.getLogger("vox")

app = FastAPI(title="VoxAgent")
app.add_middleware(
    CORSMiddleware, allow_origins=settings().cors_origins, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)
for module in (auth, routes, ws):
    app.include_router(module.router)


@app.middleware("http")
async def access_log(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    ms = round((time.perf_counter() - start) * 1000)
    log.info("http", extra={"fields": {"path": request.url.path, "status": response.status_code, "ms": ms}})
    return response


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
