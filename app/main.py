from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.monitor_routes import router as monitor_router
from app.api.notification_routes import (
    router as notification_router,
)
from app.api.regulation_comparison_routes import (
    router as regulation_comparison_router,
)
from app.api.regulation_reference_routes import (
    router as regulation_reference_router,
)
from app.core.scheduler import start_scheduler, stop_scheduler
from app.services.email_service import is_email_configured


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Uygulama ayakta olduğu sürece tek bir scheduler çalışır.
    await start_scheduler()

    try:
        yield
    finally:
        await stop_scheduler()


app = FastAPI(
    title="Website Monitor AI",
    version="1.0.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(
    monitor_router
)

app.include_router(
    notification_router
)

app.include_router(
    regulation_reference_router
)

app.include_router(
    regulation_comparison_router
)


@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "service": "website-monitor-ai",
    }


@app.get("/api/capabilities")
def capabilities():
    """
    Deployment'ta hangi özelliklerin gerçekten
    kullanılabilir olduğunu bildirir.

    Frontend bu bilgiyle, yapılandırılmamış bir özelliği
    çalışıyormuş gibi göstermez.

    YALNIZCA boolean döner. SMTP host/kullanıcı/parola gibi
    değerler bu cevaba ASLA konulmaz.
    """

    return {
        "email_notifications": is_email_configured(),
    }
