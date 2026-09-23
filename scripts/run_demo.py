"""Run the checked-in examples twice and report whether card order is stable."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO_PATH = ROOT / "demo" / "queries.json"


def main(path: Path = DEMO_PATH) -> int:
    if not path.is_file():
        print("Демо-запросов пока нет: demo/queries.json отсутствует. Пропускаем.")
        return 0
    queries = json.loads(path.read_text(encoding="utf-8"))
    if not queries:
        print("Демо-запросов пока нет: список пуст. Пропускаем.")
        return 0

    # Support both `uv run python scripts/run_demo.py` and module invocation.
    sys.path.insert(0, str(ROOT))
    from matcher.api_types import MatchRequestDTO
    from matcher.pipeline import answer

    stable = True
    for query in queries:
        request = MatchRequestDTO.model_validate(query["request"]).to_domain()
        result = answer(request)
        repeated = answer(request)
        print(f"\n{query['name']}: {result['outcome']} — {result['outcome_title_ru']}")
        print(f"  semantic_backend: {result['semantic_backend']}")
        if result["shortfall_note"]:
            print(result["shortfall_note"])
        for rank, card in enumerate(result["cards"], 1):
            price = f"{card['price_from_kzt']:,}".replace(",", "\u2009")
            print(f"  {rank}. {card['id']} | {card['name']} | от {price} ₸")
            print(f"     {card['explanation']} [{card['explanation_source']}]")
        if not result["cards"]:
            print("  Карточек нет.")
        order = [card["id"] for card in result["cards"]]
        repeated_order = [card["id"] for card in repeated["cards"]]
        same = order == repeated_order
        expected = (order == query.get("expected_card_ids", order)
                    and result["outcome"] == query.get("expected_outcome", result["outcome"])
                    and result["semantic_backend"] == query.get("semantic_backend", result["semantic_backend"])
                    and repeated["semantic_backend"] == result["semantic_backend"])
        stable &= same and expected
        print(f"  Первый порядок: {order}")
        print(f"  Повторный порядок: {repeated_order}")
        print(f"  Порядок совпадает: {'да' if same else 'НЕТ'}")
        print(f"  Демо соответствует сохранённому результату: {'да' if expected else 'НЕТ'}")
    return 0 if stable else 1


if __name__ == "__main__":
    raise SystemExit(main())
