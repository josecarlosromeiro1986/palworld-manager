from fastapi import FastAPI

app = FastAPI(
    title="Palworld Manager Mock Services",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}
