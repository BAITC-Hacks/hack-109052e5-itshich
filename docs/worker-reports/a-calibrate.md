# a-calibrate — A5

Ветка: `feat/a-calibrate`, база `995abd8`. Python 3.12.14. Push не выполнялся.

**Коммит не создан:** sandbox разрешает запись в worktree, но запрещает Git-метаданные основного checkout. `git add` завершился exit 128: `Unable to create '/Users/1nternetdirector/Development/hack-109052e5-itshich/.git/worktrees/feat-a-calibrate/index.lock': Operation not permitted`. Политика среды не позволяет запросить расширение прав. Все семь файлов сохранены, остаются untracked; для завершения передачи нужен commit из окружения с доступом к этому Git-каталогу.

Изменены только разрешённые файлы:

- `scripts/calibrate_weights.py`: подготовка через production scorer, 27 конфигураций BT, слепой судья, semaphore ≤4, backoff до 6 попыток, сохранение и возобновление JSONL, метрики и офлайн-пересчёт.
- `tests/test_calibrate.py`: 16 офлайн-тестов; TDD для BT, сетки, JSONL, перестановки, метрик, слепых заданий и API boundary. Дополнительно проверен лимит повторов после рестарта.
- `data/calibration/requests.json`: 16 запросов, split 10/6 по 8 семействам до разметки, 68 строк признаков и исходные факты, протокол и хеши исходников.
- `data/calibration/judgments.jsonl`: 332 сырых ответа и 332 записи начала попыток.
- `data/calibration/result.json`: выбранные множители, именованные веса по группам, train/held-out метрики, coverage, устойчивость, хеши входов.
- `docs/research/calibration.md`: методика, таблицы, ограничения, рекомендации и воспроизведение.
- Этот отчёт.

## Результат

Реальный прогон завершён: 224 основных парных вызова, 68 независимых grades, 40 повторов для 20 пар. Все ответы — `gpt-5.4-mini-2026-03-17`, без ошибок судьи; около 334 попыток API с учётом эмбеддингов. Семь новых векторов сохранены только в локальном `.work/calibration-embeddings.json`; базовый `data/embeddings.json` не изменён.

Минимум train: θ_budget=0.75, остальные θ=1; BT loss=0.657830. Принято 55/112 пар (49,11%), остальные исключены из-за несогласия двух порядков. Held-out pairwise: main 58,33%, приоры 50%, калибровка 45,83%. При повторе изменились ответы для 5/20 пар. Все 68 grades равны 2; NDCG@3=1 не различает варианты.

**Рекомендация: сохранить приоры; найденные множители не принимать.** Пороговые условия исследования не выполнены. `matcher/` не изменён. В JSON сохранён минимум сетки, в исследовательском отчёте отдельно приведена рекомендуемая таблица приоров.

## Команды и конечный вывод

- `UV_CACHE_DIR=$PWD/.work/uv-cache uv sync --python 3.12` → `Using CPython 3.12.14`, `Installed 34 packages`; зависимости проекта не менялись.
- `UV_CACHE_DIR=$PWD/.work/uv-cache uv run pytest -q -p no:cacheprovider tests/test_calibrate.py` → `................ [100%]`, exit 0. Предшествующие red-прогоны падали на отсутствующих BT/grid/JSONL/aggregation/metrics/jobs/collect интерфейсах; затем каждый срез реализован.
- `UV_CACHE_DIR=$PWD/.work/uv-cache uv run python scripts/calibrate_weights.py --prepare` → `Подготовлено: 16 запросов, 332 заданий судье.` Первый пакет эмбеддингов ранее завершился таймаутом 8 с; повтор прошёл. В окончательном скрипте клиенту подготовки задан timeout 60 с.
- `UV_CACHE_DIR=$PWD/.work/uv-cache uv run python scripts/calibrate_weights.py --limit 1` → первый ответ сохранён, ожидаемый exit 1: `Осталось заданий: 331; повторите запуск.`
- `UV_CACHE_DIR=$PWD/.work/uv-cache uv run python scripts/calibrate_weights.py` → `Готово: theta=(0.75, 1.0, 1.0), train loss=0.657830, согласовано 55 пар.`, exit 0.
- `UV_CACHE_DIR=$PWD/.work/uv-cache uv run python scripts/calibrate_weights.py --offline` → тот же вывод, exit 0.
- `UV_CACHE_DIR=$PWD/.work/uv-cache uv run pytest -q -p no:cacheprovider .` → полный прогон дошёл до `[100%]`, exit 0: **296 passed, 4 skipped** (браузерные e2e); 300 тестов подтверждены `--collect-only`.
- Проверка каталога, eligible, полного набора признаков через `score_all`, хешей и завершённости журнала → `PASS: 16 frozen requests, 68 production feature rows, 332 completed judgments, source hashes unchanged, zero pending jobs.`
- Повтор обычного запуска и `--offline` с проверкой SHA-256 журнала и байтов `result.json` → `PASS: normal resume and offline replay preserve judgments and reproduce result.json byte-for-byte.` Новых вызовов не было.
- `git diff --check` → пустой вывод, exit 0.
- `git add scripts/calibrate_weights.py tests/test_calibrate.py data/calibration/requests.json data/calibration/judgments.jsonl data/calibration/result.json docs/research/calibration.md docs/worker-reports/a-calibrate.md` → exit 128, запрет создания `index.lock` (см. выше). Другие способы записи в запрещённый каталог не использовались.

Команда после снятия ограничения среды:

```bash
git add scripts/calibrate_weights.py tests/test_calibrate.py data/calibration/requests.json data/calibration/judgments.jsonl data/calibration/result.json docs/research/calibration.md docs/worker-reports/a-calibrate.md
git commit -m "Add resumable LLM weight calibration and measured results"
```

## Открытые вопросы и передача

- В этом checkout ещё нет A1: semantic использован как есть. Четыре строки сравнения включают контроль «production semantic + старые веса», совпадающий с main; независимых состояний три. После A1/A2 нужен новый явно зафиксированный набор признаков и эксперимент, без изменений чужих файлов в этой задаче.
- Видеографы имеют лишь 3 профиля на город; залы Астаны — максимум 3 на формат. Для условия 4–6 eligible использованы ведущие обоих городов, фотографы и залы Алматы. Decor отдельно не проверен.
- Нужна человеческая проверка несогласованных пар и новая заранее зафиксированная рубрика независимых grades. Судья иногда предпочитает незапрошенные языки вопреки инструкции; текущие held-out не следует повторно использовать для подбора промпта и объявлять независимым тестом.
- Отсутствующий `docs/research/TASKS.md` прочитан из `research-weights` через `git show`; `RTK.md` в checkout и проверенных родительских каталогах не найден. Никакие чужие файлы для этого не добавлялись.
