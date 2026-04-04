"""
OppIntelAI
AI-powered sales intelligence platform.
Module 1: Prospector (Proactive) — Find leads
Module 2: Hydrator (Reactive) — Enrich inbound leads
Module 3: Fit Check (Prospect-Facing) — Self-serve fit analysis widget
"""
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from api.routes import router as api_router
from core.cache import init_db
from core.config import HOST, PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("OppIntelAI starting...")
    await init_db()
    logger.info("Database initialized")
    yield
    logger.info("OppIntelAI shutting down")


app = FastAPI(
    title="OppIntelAI",
    description="AI-powered sales intelligence platform. "
                "Module 1: Prospector (proactive lead finding). "
                "Module 2: Hydrator (reactive lead enrichment). "
                "Module 3: Fit Check (prospect-facing fit analysis widget).",
    version="0.2.0",
    lifespan=lifespan,
)

static_dir = os.path.join(os.path.dirname(__file__), "static")
templates_dir = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(static_dir, exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)

app.include_router(api_router)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/fit", response_class=HTMLResponse)
async def fit_widget(request: Request):
    """Prospect-facing fit check widget. Vendor links: /fit?solution=URL&name=Name"""
    return templates.TemplateResponse("fit-widget.html", {"request": request})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)
