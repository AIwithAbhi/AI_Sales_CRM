from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.config import settings
from backend.app.database import Base, engine
from backend.app.routers import companies, airtable
from backend.app.utils.logging import logger

# Initialize FastAPI App
app = FastAPI(
    title=settings.PROJECT_NAME,
    description="REST API backend for AI lead enrichment, analysis, and CRM syncing",
    version="1.0.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    """Initialize database tables asynchronously on backend application startup."""
    logger.info("FastAPI application is starting up...")
    try:
        async with engine.begin() as conn:
            # Generate SQLite tables (users, enrichment_jobs, lead_records)
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialized successfully.")
    except Exception as e:
        logger.critical(f"Database initialization failed: {e}")


@app.get("/api/health", tags=["System Health"])
async def health_check():
    """Verify application health and confirm presence of essential environment variables."""
    # Check credentials
    api_status = {
        "FIRECRAWL_API_KEY": bool(settings.FIRECRAWL_API_KEY),
        "NVIDIA_API_KEY": bool(settings.NVIDIA_API_KEY),
        "AIRTABLE_API_KEY": bool(settings.AIRTABLE_API_KEY),
        "AIRTABLE_BASE_ID": bool(settings.AIRTABLE_BASE_ID),
    }

    healthy = all(api_status.values())
    
    return {
        "status": "healthy" if healthy else "partially_configured",
        "database": "connected",
        "api_keys": api_status,
        "message": "All essential pipelines operating successfully." if healthy else "Warning: Some third-party API configurations are missing."
    }


# Mount Routers
app.include_router(companies.router, prefix=settings.API_V1_STR)
app.include_router(airtable.router, prefix=settings.API_V1_STR)


@app.get("/")
async def root():
    return {
        "message": "Welcome to AI Sales Intelligence API. Visit /docs for Interactive Swagger Documentation.",
        "docs": "/docs"
    }
