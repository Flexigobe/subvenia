"""FastAPI entrypoint."""

from fastapi import FastAPI

app = FastAPI(title="Buscador de subvenciones")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
