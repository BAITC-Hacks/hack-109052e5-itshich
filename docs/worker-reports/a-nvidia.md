# a-nvidia — A3

Ветка: `feat/a-nvidia`. Изменения подготовлены; коммит заблокирован sandbox.
`TASKS.md` прочитан из соседнего worktree `research-weights`: в текущем
checkout этого файла нет. Также прочитаны требуемые разделы исследования,
`DESIGN.md`, `README.md` и изменяемый код. `RTK.md` не найден.

## Файлы

- `matcher/embeddings_nvidia.py`: отдельный NVIDIA scorer и сборщик кэша;
  модель `nvidia/nemotron-3-embed-1b`, 2048 измерений, пакеты до 64 текстов,
  ключи с моделью, `input_type` и текстом; атомарная запись, округление
  векторов до шести знаков. Якоря `{low, high}` рассчитываются по полному
  произведению запросов и описаний, сохраняются и не зависят от eligible.
  Без якорей scorer сообщает о недоступности вместо некалиброванного скора.
- `matcher/config.py`: только `create_nvidia_client`, отдельный ключ,
  фиксированный endpoint, timeout 8, max_retries 0.
- `matcher/pipeline.py`: выбор NVIDIA через `EMBEDDING_PROVIDER`; NVIDIA
  добавлен в существующее условие перехода на lexical при ошибке scorer.
- `scripts/build_embeddings.py`: делегирование `--provider nvidia --anchors`
  новому модулю; default output зависит от провайдера. Обработка OpenAI
  после выбора default output сохранена. NVIDIA строит запросы для всех
  поддерживаемых сочетаний категории, города, формата и языка, описания
  и предложения, включая `synthetic_extra.csv`.
- `.env.example`: две опциональные переменные NVIDIA/provider.
- `tests/test_embeddings_nvidia.py`: 25 тестовых случаев; fake SDK,
  request/response, ключи, калибровка, offline reuse, fallback, сборка,
  некорректные ответы и отказ атомарной записи.
- `README.md`: один подраздел о NVIDIA.
- Этот отчёт. Новые Python-модули: 183 и 239 строк; зависимостей нет.

## Команды и результаты

- TDD: `UV_CACHE_DIR=$PWD/.work/uv-cache uv run pytest -q -p no:cacheprovider
  tests/test_embeddings_nvidia.py`. Новые сценарии сначала падали из-за
  отсутствующих helper/module/method/provider/CLI и отсутствия разбиения
  на пакеты; после соответствующей реализации проходили.
- Проверка затронутых интерфейсов:
  `UV_CACHE_DIR=$PWD/.work/uv-cache uv run pytest -q -p no:cacheprovider
  tests/test_embeddings_nvidia.py tests/test_embeddings.py tests/test_api.py --tb=short`.
  Итог: `[100%]`, exit 0.
- Полный прогон:
  `OPENAI_API_KEY= NVIDIA_API_KEY= EMBEDDING_PROVIDER=openai UV_OFFLINE=1
  UV_CACHE_DIR=$PWD/.work/uv-cache uv run pytest -q -p no:cacheprovider . -o addopts=''`.
  Итог: **305 passed, 4 skipped in 15.30s**, exit 0; пропущены браузерные тесты.
  Использовано готовое окружение Python 3.14.7.
- Python 3.12: тот же полный прогон с дополнительными
  `UV_PYTHON=3.12 UV_PROJECT_ENVIRONMENT=$PWD/.work/venv312` остановился
  до запуска тестов: offline cache не содержит wheel `greenlet==3.5.6`
  для CPython 3.12, exit 1. Ничего не загружалось. `ast.parse` с
  `feature_version=(3, 12)` принял все пять затронутых Python-модулей.
- `OPENAI_API_KEY= NVIDIA_API_KEY= UV_OFFLINE=1
  UV_CACHE_DIR=$PWD/.work/uv-cache uv run python scripts/build_embeddings.py --provider nvidia --anchors`.
  Итог: `Не задан NVIDIA_API_KEY: добавьте ключ для сборки кэша NVIDIA NIM.`, exit 2.
- `git diff --check`: exit 0, вывода нет.
- `git add matcher/embeddings_nvidia.py matcher/pipeline.py matcher/config.py
  scripts/build_embeddings.py .env.example tests/test_embeddings_nvidia.py README.md`
  завершился с exit 128: `Unable to create .../.git/worktrees/feat-a-nvidia/index.lock:
  Operation not permitted`. Следующая команда
  `git commit -m "Add NVIDIA NIM embedding backend with calibrated cache"`
  не выполнялась. Git metadata находится вне разрешённых writable roots.
  Изменения не закоммичены и не отправлены.

## Открытые вопросы для интегратора

1. Обязательная правка вне моего владения: добавить `nvidia` в
   `matcher/model.py` (`SemanticScorer.name`, `MatchResult.semantic_backend`)
   и `matcher/api_types.py` (`SemanticBackend`). `service.run` уже возвращает
   `nvidia`, но текущий HTTP DTO отклоняет его с `literal_error`, включая
   пустые исходы. Успешный NVIDIA HTTP-ответ пока заблокирован этими типами;
   fallback-ответ `lexical` проверен через настоящий `pipeline.answer`.
2. После расширения типов нужен тест успешного NVIDIA HTTP-ответа на fake
   клиентах и готовом кэше. Сейчас не подменял типы ради зелёного теста.
3. Реальный `data/embeddings_nvidia.json` не создан: ключа NVIDIA нет,
   NVIDIA API не вызывался. Калибровка проверена на известных fake-векторах;
   качество модели и реальный endpoint здесь не оценивались.
4. Нужен полный прогон на Python 3.12 после предоставления локальных
   зависимостей и коммит перечисленных файлов вместе с этим отчётом из
   окружения с правом записи Git metadata.

## Ошибка изоляции первого отрицательного теста

До реализации выбора NVIDIA один первоначальный тест дошёл до старого
OpenAI scorer с ключом из `.env` и сделал сетевые попытки вопреки заданию.
Один запрос записал вектор тестового описания, остальные необходимые
запросы завершились таймаутами. `data/embeddings.json` восстановлен побайтно
из HEAD после проверки, что добавлен только один известный тестовый вектор,
а существующие записи не изменились. Новая autouse fixture очищает ключи
и запрещает создавать настоящий SDK client; полный итоговый прогон шёл
с пустыми ключами. `.env` не изменялся и его содержимое не выводилось.
