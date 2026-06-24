import logging
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .database import init_db
from . import auth
from .routers import auth as auth_router, api as api_router, webhook as webhook_router

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bridge")

# Create App
app = FastAPI(title="BridgeHC - NetHunt & HelpCrunch Integration Hub")

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Startup Handler
@app.on_event("startup")
def startup_event():
    init_db()
    auth.init_session_secret()
    logger.info("Database initialized successfully.")

# Setup static directories
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
static_dir = os.path.join(frontend_dir, "static")

# Serve Index Page
@app.get("/", response_class=HTMLResponse)
async def get_index():
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return "<h3>Error: index.html not found.</h3>"

@app.get("/favicon.ico")
async def get_favicon():
    fav_path = os.path.join(static_dir, "favicon.png")
    if os.path.exists(fav_path):
        return FileResponse(fav_path)
    raise HTTPException(status_code=404)

# Mount static folder
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Include routers
app.include_router(auth_router.router)
app.include_router(api_router.router)
app.include_router(webhook_router.router)
