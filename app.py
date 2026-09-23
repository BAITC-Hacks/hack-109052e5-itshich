"""HTTP adapter for contractor matching."""
import json
from collections import Counter
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from matcher import pipeline
from matcher.api_types import MatchRequestDTO, MatchResponseDTO, MetaDTO
from matcher.model import CALENDAR_END, CALENDAR_START

app = FastAPI(title="Подбор подрядчиков", docs_url="/docs")
ROOT = Path(__file__).resolve().parent
DEMO_PATH = ROOT / "demo" / "queries.json"


@app.get("/", response_class=FileResponse)
def index():
    return FileResponse(ROOT / "web" / "qalau" / "openai.html", media_type="text/html")


@app.get("/legacy", response_class=FileResponse)
def legacy():
    return FileResponse(ROOT / "web" / "index.html", media_type="text/html")


@app.get("/docs-ui", response_class=FileResponse)
def docs_ui():
    return FileResponse(ROOT / "web" / "qalau" / "openai-docs.html", media_type="text/html")


@app.get("/tests", response_class=FileResponse)
def tests_ui():
    path = ROOT / "web" / "qalau" / "tests.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Страница проверок ещё не сформирована")
    return FileResponse(path, media_type="text/html")


@app.get("/api/tests")
def tests_report():
    try:
        return json.loads((ROOT / "data" / "test_report.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"runs": [], "note": "отчёт ещё не сформирован"}


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


# Keep the catch-all mount after every API and page route.
app.mount("/", StaticFiles(directory=ROOT / "web" / "qalau"), name="qalau")
