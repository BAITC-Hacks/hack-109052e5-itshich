"""HTTP adapter for contractor matching."""
import json
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from matcher import pipeline
from matcher.api_types import MatchRequestDTO, MatchResponseDTO, MetaDTO
from matcher.model import CALENDAR_END, CALENDAR_START

app = FastAPI(title="Подбор подрядчиков", docs_url="/docs")
ROOT = Path(__file__).resolve().parent
DEMO_PATH = ROOT / "demo" / "queries.json"


@app.get("/", response_class=FileResponse)
def index():
    return FileResponse(ROOT / "web" / "index.html", media_type="text/html")


@app.post("/api/match", response_model=MatchResponseDTO)
def match(request: MatchRequestDTO):
    try:
        return pipeline.answer(request.to_domain())
    except pipeline.RequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/meta", response_model=MetaDTO)
def meta():
    contractors = pipeline.get_contractors()
    cities = sorted({item.city for item in contractors})
    categories = sorted({category for item in contractors for category in item.categories})
    counts = Counter((item.city, category) for item in contractors for category in item.categories)
    return MetaDTO(
        cities=cities, categories=categories,
        formats=sorted({value for item in contractors for value in item.event_formats}),
        languages=sorted({value for item in contractors for value in item.languages}),
        calendar_start=CALENDAR_START, calendar_end=CALENDAR_END,
        counts={city: {category: counts[city, category] for category in categories} for city in cities},
    )


@app.get("/api/demo")
def demo():
    try:
        return json.loads(DEMO_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
