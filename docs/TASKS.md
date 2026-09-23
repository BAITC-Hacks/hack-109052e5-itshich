# Задачи по внедрению: поток A и поток B

> Статус на вечер 2026-09-23: A1, A2, A5, A6, C1 сделаны (`feat/stream-a`);
> A3 NVIDIA снят; A4 отложен. Поток B влит в `main`. Итог в `README.md` этой папки.

Дата: 2026-09-23. Ветка исследования: `research-weights`. Полный ресерч и
обоснования: `docs/research/README.md` и отчёты `01-*`, `02-*`, `03-*`.

## Исполнители

| поток | кто | ветка (через herdr worktree) |
| --- | --- | --- |
| A. Ранжирование и семантика | Влад | `feat/ranking-weights` |
| B. Причины и объяснения | коллега | `feat/reasons-explain` |

## Что уже решено (не пересматриваем)

1. Порядок карточек и ключи причин задаёт **код**, не LLM. LLM только
   формулирует текст по готовым кодам и числам.
2. Причины считаются из вкладов линейного скора:
   `φ_f = w_f · (x_f − mean_f по eligible)`, плюс контраст с соседями по
   тройке и с лучшим кандидатом за бортом top-3.
3. Аспекты описаний размечаются **один раз офлайн** LLM-ом в
   `data/aspects.json`. Дословная цитата там хранится только как
   доказательство тега (`quote in description`). **В текст карточки цитаты
   не попадают**: LLM пересказывает аспект своими словами. Если тегов нет,
   карточка объясняется структурными причинами.
4. Числа в объяснении только из полей (`price_from_kzt`, `budget_kzt`,
   `budget_headroom_pct`, `max_hours`, `requested_hours`, `event_date`,
   разности цен с соседями). Любое другое число = отклонить текст, взять
   шаблон.
5. «Больные точки» пользователя: правила из структурных полей всегда
   (бюджет впритык, декабрь/дефицит дат, язык, часы) плюс опциональное поле
   `wishes` (пожелания) в запросе.
6. Эмбеддинги: OpenAI `text-embedding-3-large` основной, NVIDIA
   `nvidia/nemotron-3-embed-1b` второй backend с отдельным кэшем.
   Reranker не используем.
7. Стартовые веса (приоры): budget 0.25, text 0.55, language 0,
   duration 0.05, quality 0.15; профили по группам категорий. Косинус
   калибруется фиксированными якорями по каталогу, не по текущему пулу.

## Правила совместной работы

- Владение файлами: поток A правит `embeddings.py`, `ranking.py`,
  `config.py`, `model.py`, `api_types.py`, `pipeline.py`, `web/index.html`,
  `scripts/build_embeddings.py`, `scripts/calibrate_weights.py`, `demo/`.
  Поток B правит `reasons.py` (новый), `explain.py`, `service.py`,
  `scripts/tag_aspects.py`, `data/aspects.json`, `tests/test_reasons.py`,
  `tests/test_explain.py`.
- Если B нужны новые поля в `CardFacts` (`matcher/model.py`), B пишет
  список полей в чат, A добавляет. Не править чужие файлы.
- Каждый поток держит `uv run pytest -q` зелёным в своей ветке.
- Merge в `main`: сначала A (меняет скор и демо-ожидания), потом B поверх.
- Ключи в `.env`, не в git. `_env.crash` не коммитить.

## Поток A. Ранжирование и семантика (Влад)

| # | задача | файлы | оценка |
| --- | --- | --- | --- |
| A1 | Калибровка шкалы косинуса: якоря `a = P05`, `b = max(P95, a+0.10)` по всем парам (запрос, описание) из кэша; `z_dense = clip((cos−a)/(b−a), 0, 1)`; якоря хранить в `data/embeddings.json` рядом с моделью | `matcher/embeddings.py`, `scripts/build_embeddings.py`, `tests/test_embeddings.py` | 40 мин |
| A2 | Профили весов по группе категории (общий / площадка / ведущий / флорист-декоратор-сувениры), маска длительности при незапрошенных часах, перенормировка; `ScoreBreakdown` сохраняет компоненты | `matcher/ranking.py`, `matcher/config.py`, `tests/test_ranking.py` | 40 мин |
| A3 | Второй backend NVIDIA: `POST https://integrate.api.nvidia.com/v1/embeddings`, `model: nvidia/nemotron-3-embed-1b`, `input_type: query|passage`, `truncate: NONE`; кэш `data/embeddings_nvidia.json`; выбор через `EMBEDDING_PROVIDER=openai|nvidia`; при отсутствии ключа и кэша fallback на lexical | `matcher/embeddings.py`, `scripts/build_embeddings.py`, `matcher/config.py`, `.env.example` | 40 мин |
| A4 | Поле `wishes: str | None` в запросе: эмбеддинг на лету с кэшем по хешу, `text = 0.8·z_dense(query) + 0.2·lexical` расширяется на пожелания (например среднее косинусов запроса и пожеланий); поле в форме и API | `matcher/model.py`, `matcher/api_types.py`, `matcher/filtering.py`, `web/index.html`, `tests/test_api.py` | 40 мин |
| A5 | Скрипт калибровки: 16 запросов (10 train / 6 held-out по семействам), все пары eligible в обоих порядках, `gpt-5.4-mini` structured output `{winner: A|B|tie|insufficient, decisive_factor, evidence_a, evidence_b}`, судье не показывать скоры; сетка 81 конфигурации (`θ_budget, θ_duration, θ_quality ∈ {0.75,1,1.25}`, `α ∈ {0.5,0.8,1.0}`), Bradley–Terry loss с температурой 0.20; метрики pairwise agreement и NDCG@3; ответы судьи и выбранный config в репо | `scripts/calibrate_weights.py`, `docs/research/calibration.md` | 60 мин |
| A6 | Перегенерировать `demo/queries.json` (`scripts/find_demo_queries.py`), обновить ожидания в `tests/test_service.py`; проверить инвариант «бронь одного не меняет скор других» | `demo/`, `tests/` | 20 мин |
| C1 | README для жюри: пайплайн, кто что решает, как проверить, ограничения | `README.md` | 30 мин |

## Поток B. Причины и объяснения (коллега)

| # | задача | файлы | оценка |
| --- | --- | --- | --- |
| B1 | Офлайн-разметка аспектов: `gpt-5.4-mini`, Structured Outputs, закрытый enum тегов (`business_forum, team_building, wedding_ceremony, improvisation, custom_script, documentary_photo, posing_guidance, live_instruments, kazakh_repertoire, turnkey_decor, branded_merch, instant_print, presentation_equipment`, дополнить по каталогу), `polarity`, `quote` для проверки `quote in description`; результат `data/aspects.json`, скрипт идемпотентный | `scripts/tag_aspects.py`, `data/aspects.json` | 40 мин |
| B2 | `matcher/reasons.py`: `Reason(code, family, key, role, values, contribution, utility)`; вклад `φ_f = w_f·(x_f − mean_f eligible)`; контраст `Δ_f = w_f·(x_i − x_j)` с соседями по тройке и с лучшим вне top-3; guard: главная причина не меньше 20 % максимального вклада; utility `0.65·A + 0.25·D + 0.10·U` | `matcher/reasons.py`, `tests/test_reasons.py` | 60 мин |
| B3 | Разнообразие на тройке: перебор комбинаций главных причин (≤ 6³), штраф 0.5 за одинаковый ключ, 0.1 за семейство, tie-break по кортежу ключей; порядок карточек не менять; флаг `diversity_limited`, если различий не хватает | `matcher/reasons.py` | 30 мин |
| B4 | Счётчик занятости: `T0 = top3(eligible без фильтра даты)`, `Td = top3(на дату)`; карточка «попала из-за занятости», если `i ∈ Td` и `i ∉ T0`; назвать занятого конкурента `j` с `reasons == (BUSY_ON_DATE,)` и проверить `i ∉ top3(Ed + [j])` | `matcher/reasons.py`, `matcher/service.py` | 30 мин |
| B5 | Правила «больных точек» из полей: запас бюджета < 15 % → приоритет `BUDGET_*`; дата в декабре или ≥ 50 % пула занято → приоритет `AVAILABILITY_*` и упоминание дефицита; запрошен язык → `LANGUAGE_REQUEST_MATCH` с подтверждением из тегов; запрошены часы → `DURATION_HEADROOM` | `matcher/reasons.py` | 20 мин |
| B6 | Промпт LLM: вход = коды причин + факты с числами + теги аспектов (без цитат и без полного описания) + сводка отказов; 1–2 предложения, своими словами; шаблонный fallback по тем же кодам; валидатор: каждое число есть в разрешённых значениях карточки, нет общих фраз, swap-test (утверждения карточки i не должны быть верны для карточки j), ключи причин у карточек различаются | `matcher/explain.py`, `tests/test_explain.py` | 60 мин |
| C2 | Прогон `scripts/run_demo.py`: 6 запросов дважды, порядок и текст совпадают, ответ < 10 с; пара дат показывает занятость в объяснении | — | 20 мин |

## Интерфейс между потоками

Поток B читает из `CardFacts` (после A2): `score.budget_fit, score.semantic,
score.language_fit, score.duration_fit, score.data_quality, score.total`,
веса профиля (`config.weights_for(category)`), `budget_headroom_pct`,
`requested_hours`, `duration_note`, `caveats`. Поток A обязуется сохранить
эти имена. Если B нужно среднее по eligible, A добавляет в
`MatchResult` поле `eligible_means: dict[str, float]`.
