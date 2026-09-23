# Ресерч: веса ранжирования и классификация причин выбора (промежуточный)

Дата: 2026-09-23. Ветка `research-weights`. Статус: черновик, codex-отчёты
(веса / причины / модели) добавятся отдельными файлами в этой папке.

## 1. Что требует задача (из документа кейса)

- «Ценность в объяснении, а не в сортировке». Карточка: 1–2 предложения,
  совпадение по бюджету / формату / языку / длительности / смыслу описания.
- Объяснения не взаимозаменяемы между карточками одного запроса.
- Тот же запрос на две даты даёт разную выдачу, и в объяснении видно, что
  причина в занятости.
- Дообучение на 66 записях не нужно. Готовые эмбеддинги и LLM через API
  разрешены.
- Жюри: 25 соответствие задаче, 25 техреализация (в т.ч. AI/agentic AI),
  25 README и воспроизводимость, 15 ценность, 10 оригинальность.

## 2. Состояние репозитория (важно)

- `matcher/explain.py` и `matcher/embeddings.py` (worker «bravo») в `main`
  отсутствуют. `pipeline.answer()` падает: `ModuleNotFoundError: matcher.explain`.
  `/api/match` сейчас не работает, хотя `pytest` зелёный (124 теста на фейках).
- Текущая формула ранжирования (`matcher/ranking.py`):
  `0.35 budget_fit + 0.35 semantic + 0.10 language + 0.10 duration + 0.10 data_quality`.
- Семантика без ключа: `LexicalScorer` (стемы по формату/категории), `hits/4`.

## 3. Замер разброса признаков на демо-запросах (LexicalScorer)

Запрос «dense» (Ведущий, Алматы, корпоратив, 2 000 000 ₸, 4 ч): pool 10, eligible 5.

| признак | mean | sd | min | max |
| --- | ---: | ---: | ---: | ---: |
| budget_fit | 0.410 | 0.227 | 0.000 | 0.650 |
| semantic | 0.400 | 0.200 | 0.000 | 0.500 |
| language_fit | 0.667 | 0.211 | 0.333 | 1.000 |
| duration_fit | 0.473 | 0.120 | 0.333 | 0.600 |
| data_quality | 1.000 | 0.000 | 1.000 | 1.000 |

Выводы: (а) `data_quality` в плотных категориях константа, вес 0.10 не
работает; (б) `language_fit` без запрошенного языка имеет sd 0.21 при весе
0.10, т.е. «знает 3 языка» реально двигает порядок; (в) семантика лексическая
грубая (шаг 0.25). При переходе на эмбеддинги косинус, отображённый в
[0,1] через (cos+1)/2, сожмётся в узкий диапазон (~0.7–0.85) и при весе 0.35
почти перестанет влиять. Нужна нормализация внутри пула (min-max или z-score
по eligible) либо rank-based fusion.

## 4. Найденные открытые источники (первичный список)

Веса без размеченных данных:
- MCDM: weighted-sum / AHP / entropy / TOPSIS для выбора поставщиков —
  https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7516705/ ,
  https://pmc.ncbi.nlm.nih.gov/articles/PMC4775722/ ,
  https://www.1000minds.com/decision-making/what-is-mcdm-mcda
- LLM-as-judge парные предпочтения + Bradley–Terry для получения шкалы —
  https://www.statsig.com/perspectives/pairwise-comparison-ranking-model ,
  https://towardsdatascience.com/learning-from-pairwise-preferences-an-introduction-to-the-bradley-terry-model/
- Гибрид lexical+dense: RRF `1/(k+rank)`, k=60, робастен к разным шкалам —
  https://denser.ai/blog/hybrid-search-for-rag/ ,
  https://redis.io/blog/hybrid-search-explained/

Классификация причин («почему именно этот»):
- Reason codes в кредитном скоринге: до 5 кодов по предельному вкладу
  признака относительно базы — https://www.myfico.com/credit-education/blog/reason-codes ,
  https://pratikdhanave.com/blog/posts/finai-credit-explainability-shap-reason-codes.html
- SHAP как аддитивное разложение линейного скора (для линейной формулы вклад
  признака = w_i * (x_i - baseline_i), считается без библиотек) —
  https://christophm.github.io/interpretable-ml-book/shap.html
- ShaRP: Shapley для ранжирования (rank, top-k, pairwise), pip `xai-sharp`,
  MIT — https://arxiv.org/abs/2401.16744 , https://github.com/DataResponsibly/ShaRP
- Explainable recsys обзор, aspect-based и contrastive объяснения —
  https://arxiv.org/pdf/1804.11192 , https://arxiv.org/pdf/2503.08051 ,
  https://arxiv.org/html/2512.03439v1
- Разнообразие объяснений в списке (не повторять одну причину) —
  https://arxiv.org/pdf/2003.04315 (LIMEADE), https://arxiv.org/pdf/2410.22020
- Индустрия: Thumbtack показывает «почему этот pro» по экспертизе,
  потребностям проекта, отзывам, доступности —
  https://help.thumbtack.com/article/how-thumbtack-works/

Готовые модели (правила разрешают):
- NVIDIA NIM: `nvidia/llama-3.2-nv-embedqa-1b-v2` (26 языков, включая русский),
  `nvidia/llama-3.2-nv-rerankqa-1b-v2`, `baai/bge-m3` —
  https://docs.api.nvidia.com/nim/reference/nvidia-llama-3_2-nv-embedqa-1b-v2 ,
  https://docs.api.nvidia.com/nim/reference/nvidia-llama-3_2-nv-rerankqa-1b-v2 ,
  https://build.nvidia.com/explore/retrieval
- Rerank API: `POST /v1/retrieval/nvidia/<model>/reranking`, `{query:{text}, passages:[{text}]}`,
  ответ `rankings:[{index, logit}]` —
  https://docs.nvidia.com/nim/nemo-retriever/reranking/2.0/reference.html
- Embeddings API: `POST /v1/embeddings` с `input_type: query|passage`, `truncate` —
  https://docs.nvidia.com/nim/nemo-retriever/text-embedding/latest/reference.html
- Русский: BGE-M3 на MIRACL-ru 70.16 vs mE5-large 67.33; ruMTEB бенчмарк —
  https://arxiv.org/pdf/2408.12503 , https://aclanthology.org/2025.naacl-long.12/
- OpenAI `text-embedding-3-large` MIRACL avg 54.9 —
  https://openai.com/index/new-embedding-models-and-api-updates/

## 5. Предварительная гипотеза (до отчётов codex)

1. Причины выбора = reason codes из аддитивного скора: для каждой карточки
   вклад признака `c_i = w_i * (x_i - mean_i(eligible))`; топ-2 положительных
   вклада = причины, отрицательный = оговорка. Плюс «контраст» с соседними
   карточками: причина, по которой эта карточка лучше следующей.
2. Разнообразие: если у двух карточек совпала главная причина, у второй
   берём следующую по вкладу (greedy, детерминированно по id).
3. Веса: нормализовать признаки внутри eligible (иначе косинус давит), затем
   калибровать веса по парным предпочтениям LLM-судьи на 20–40 парах
   (gpt-5.4-mini, temperature 0, swap-order для снятия position bias),
   fit Bradley–Terry / простая сетка по NDCG@3.
4. Семантика: первичный кандидат NVIDIA `nv-embedqa-1b-v2` + rerank
   `nv-rerankqa-1b-v2` по описанию против запроса; fallback OpenAI
   `text-embedding-3-large`; кэш векторов в репо для детерминизма без сети.

## 6. Что дальше

- Дождаться трёх codex-отчётов, свести в `01-weights.md`, `02-reasons.md`,
  `03-models.md`, финальные рекомендации в `README` этой папки.
- Восстановить `matcher/explain.py` / `embeddings.py` (блокер демо).
