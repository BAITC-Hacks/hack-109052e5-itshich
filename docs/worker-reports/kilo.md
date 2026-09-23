# codex-kilo — B5/B6 result

Branch: `wt-bravo`. Scope: reasons, explanations, their tests, minimal service-test assertions, and the DESIGN reasons section. Ranking and saved demo expectations are unchanged.

Implemented positive format-relevant aspect labels without description quotes; deterministic budget/date/language/hour priority boosts; category availability counts; concise replacement wording; numeric and directed swap validation; code-based templates and versioned memory/file caching.

## Verification

- Baseline: 280 passed, 4 skipped (284 collected).
- Final `uv run pytest -q -o addopts=`: **316 passed, 4 skipped in 15.69s**.
- `OPENAI_API_KEY='' uv run python scripts/run_demo.py`: **exit 0**; all six saved outcomes, embedding backends and ordered `expected_card_ids` preserved on both runs.
- Commands used `UV_CACHE_DIR=/private/tmp/codex-kilo-uv-cache` because the default UV cache is outside the writable sandbox.
- All six saved queries produce deterministic template texts accepted by the validator. Six additional typical triples cover structural-only, aspect-tagged, tight-budget, scarce-date, December and caveat cases.
- API behavior is tested with fake clients: temperature 0, seed 42, timeout 8, JSON object output, whole-result fallback, and cache invalidation. Cross-process file-cache replay is checked under different Python hash seeds.
- `git diff --check`: clean.

## Commit status

Commit was attempted with `git add` followed by `git commit`, but staging failed:
`fatal: Unable to create '/Users/mike/Hackaton/hack-109052e5-itshich/.git/worktrees/wt-bravo/index.lock': Operation not permitted`.
The shared Git metadata is outside this session's writable roots. No commit was created and nothing was pushed. Changes remain in the worktree; the pre-existing untracked `TASK.md` was left untouched.

## Integration notes

- `data/aspects.json` is absent in this worktree. Demo texts below use the shared `load_aspects()` contract; tagged behavior is tested using temporary files in the documented schema. No aspects data or shared contract was edited.
- `docs/TASKS.md` was read from `/Users/mike/Hackaton/hack-109052e5-itshich/docs/TASKS.md`; it is absent from this worktree.
- On 05.10.2026, restoring Kiki alone removes Bullma from the top three. The regression test verifies both the date-free top three and the single-competitor counterfactual. Scarcity is 5 free of 10 in the category even though only 4 pass every condition.

## Rendered demo templates

### dense

Ведущий / Алматы / корпоратив / 04.10.2026 / бюджет 2,000,000 ₸.

Outcome: `matched`. Backend: `embeddings`. First render: 0.007s. Repeated texts identical.

Условиям соответствуют 5 из 10; показаны 3 с лучшим баллом (не вошли: Софи Хаттер, Джинбей). Не прошли условия: 5 заняты на 04.10.2026 (Эмилия, Буллма, Мицури Канроджи, Хаул, Куррапика); 1 не поддерживает формат «корпоратив» (Эмилия). Причины могут пересекаться.

**1. Кики — HK-35215** (`LANGUAGE_UNIQUE_IN_SHOWN`, 101 characters)

Кики: среди показанных только здесь заявлен язык английский. В категории в эту дату свободны 5 из 10.

**2. Сон Гоку — HK-27222** (`BUDGET_HEADROOM`, 113 characters)

Сон Гоку: при цене от 1 000 000 ₸ остаётся 50 % бюджета 2 000 000 ₸. По доступности: в эту дату свободны 5 из 10.

**3. Аня Форджер — HK-29829** (`AVAILABILITY_REPLACEMENT`, 149 characters)

Поднялся в тройку: Куррапика на эту дату занят; в эту дату свободны 5 из 10. Аня Форджер: из бюджета 2 000 000 ₸ остаётся 65 % при цене от 700 000 ₸.

### rare

Флорист / Алматы / свадьба / 04.10.2026 / бюджет 250,000 ₸.

Outcome: `matched`. Backend: `embeddings`. First render: 0.000s. Repeated texts identical.

Показано 2 из 3: в городе всего 2 профиля этой категории.

**1. Тони Тони Чоппер — HK-39372** (`BUDGET_HEADROOM`, 168 characters)

Тони Тони Чоппер: цена от 200 000 ₸, бюджет 250 000 ₸: запас 20 %. Описание ближе всего к запросу среди показанных; цена проставлена при подготовке датасета, уточняйте.

**2. Тихиро Огино — HK-90001** (`LANGUAGE_UNIQUE_IN_SHOWN`, 167 characters)

Тихиро Огино: язык казахский есть только в этой анкете среди показанных. Бюджет впритык: бюджет 250 000 ₸ покрывает стартовую цену от 250 000 ₸; синтетический профиль.

### empty_no_category

Декоратор / Астана / свадьба / 04.10.2026 / бюджет 250,000 ₸.

Outcome: `no_category_in_city`. Backend: `embeddings`. First render: 0.000s. Repeated texts identical.

В городе Астана нет подрядчиков категории «Декоратор». Ближайшие варианты: категории с похожим назначением в этом городе: Подарки и сувениры, Флорист. Категория есть в Алматы: 3 профиля.

Карточек нет.

### empty_none_eligible

Ведущий / Алматы / корпоратив / 02.10.2026 / бюджет 500,000 ₸.

Outcome: `none_eligible`. Backend: `embeddings`. First render: 0.000s. Repeated texts identical.

Кандидаты в категории есть (10), но ни один не проходит: 8 заняты на 02.10.2026 (Сон Гоку, Аня Форджер, Эмилия, Мицури Канроджи, Софи Хаттер, Джинбей, Хаул, Куррапика); 9 дороже бюджета (Сон Гоку, Аня Форджер, Кики, Эмилия, Буллма, Мицури Канроджи, Софи Хаттер, Джинбей, Хаул; цены от: 650 000 ₸, 700 000 ₸, 900 000 ₸, 1 000 000 ₸, 1 300 000 ₸, 2 000 000 ₸; бюджет 500 000 ₸); 1 не поддерживает формат «корпоратив» (Эмилия). Причины могут пересекаться.

Карточек нет.

### date_pair_a

Ведущий / Алматы / корпоратив / 04.10.2026 / бюджет 2,000,000 ₸.

Outcome: `matched`. Backend: `embeddings`. First render: 0.007s. Repeated texts identical.

Условиям соответствуют 5 из 10; показаны 3 с лучшим баллом (не вошли: Софи Хаттер, Джинбей). Не прошли условия: 5 заняты на 04.10.2026 (Эмилия, Буллма, Мицури Канроджи, Хаул, Куррапика); 1 не поддерживает формат «корпоратив» (Эмилия). Причины могут пересекаться.

**1. Кики — HK-35215** (`LANGUAGE_UNIQUE_IN_SHOWN`, 101 characters)

Кики: среди показанных только здесь заявлен язык английский. В категории в эту дату свободны 5 из 10.

**2. Сон Гоку — HK-27222** (`BUDGET_HEADROOM`, 113 characters)

Сон Гоку: при цене от 1 000 000 ₸ остаётся 50 % бюджета 2 000 000 ₸. По доступности: в эту дату свободны 5 из 10.

**3. Аня Форджер — HK-29829** (`AVAILABILITY_REPLACEMENT`, 149 characters)

Поднялся в тройку: Куррапика на эту дату занят; в эту дату свободны 5 из 10. Аня Форджер: из бюджета 2 000 000 ₸ остаётся 65 % при цене от 700 000 ₸.

### date_pair_b

Ведущий / Алматы / корпоратив / 05.10.2026 / бюджет 2,000,000 ₸.

Outcome: `matched`. Backend: `embeddings`. First render: 0.005s. Repeated texts identical.

Условиям соответствуют 4 из 10; показаны 3 с лучшим баллом (не вошли: Софи Хаттер). Не прошли условия: 5 заняты на 05.10.2026 (Сон Гоку, Аня Форджер, Кики, Мицури Канроджи, Джинбей); 1 не поддерживает формат «корпоратив» (Эмилия).

**1. Куррапика — HK-88430** (`BUDGET_HEADROOM`, 102 characters)

Куррапика: цена от 500 000 ₸, бюджет 2 000 000 ₸: запас 75 %. В категории в эту дату свободны 5 из 10.

**2. Хаул — HK-77838** (`DURATION_MAX_IN_SHOWN`, 107 characters)

Хаул: среди этих вариантов дольше всех может работать: до 8 ч. По доступности: в эту дату свободны 5 из 10.

**3. Буллма — HK-44733** (`AVAILABILITY_REPLACEMENT`, 134 characters)

Поднялся в тройку: Кики на эту дату занят; в эту дату свободны 5 из 10. Буллма: в отличие от соседей, заявлен рабочий язык английский.
