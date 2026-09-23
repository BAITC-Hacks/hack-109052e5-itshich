# Alpha result

Implemented the deterministic catalogue → filter → rank → service path, local
lexical scoring, the six real-data demos, and tests at the four agreed seams.
Final verification: **105 tests passed**, including all ten Hypothesis properties
configured with `max_examples=200, deadline=None`.

## Files created

- `matcher/data.py`: strict CSV loading, optional synthetic sibling, `DataError`.
- `matcher/filtering.py`: request validation, `RequestError`, every rejection
  reason in enum order, rejection ordering by id.
- `matcher/ranking.py`: specified score formula, rounded-total/id ordering,
  three-card limit, complete `CardFacts`.
- `matcher/lexical.py`: deterministic format/category/language stem dictionaries,
  distinct keyword hits, highest-hit sentence with a 200-character limit.
- `matcher/service.py`: all three outcomes and Russian shortfall explanations
  with counts, names, starting prices, thin-space thousands separators, and ₸.
- `tests/conftest.py`: real catalogue, sample contractors, requests, CSV helpers,
  and saved-demo fixtures.
- `tests/test_data.py`, `tests/test_filtering.py`, `tests/test_ranking.py`,
  `tests/test_service.py`, `tests/test_properties.py`.
- `scripts/find_demo_queries.py`: searches the real catalogue without hardcoded
  card ids or expected results; unchanged regeneration does not rewrite the file.
- `demo/queries.json`: six requests with expected outcomes and ordered card ids.
- `RESULT.md`: this report.

## Commands and final output

Commands used the existing environment with `UV_NO_SYNC=1` and
`UV_CACHE_DIR=.work/uv-cache`. Pytest scratch files were directed to
`.work/pytest-tmp`. For the final suite, `PYTEST_ADDOPTS` also set `-o addopts=""`
to avoid the repository's additional `-q` hiding the result count.

| Command | Final output / result |
| --- | --- |
| `uv run pytest -q` | `105 passed in 10.70s` |
| `uv run pytest -q tests/test_properties.py -o addopts=''` | `10 passed in 10.44s` |
| `uv run pytest -q tests/test_service.py tests/test_ranking.py -o addopts=''` | `54 passed in 0.09s` |
| `uv run python scripts/find_demo_queries.py` | `Verified 6 demo queries in demo/queries.json` |
| Python harness running the demo command twice and comparing bytes and mtime | `Idempotence verified: 2 runs, unchanged bytes and modification time; sha256=3a2d0ec3dcc8a66f6e989b2bd66af78aad354168c6b08b72ae699c825415aab8` |
| `git diff --check` | Exit 0; no output (tracked files; new files remain unstaged). |
| `git diff -- matcher/model.py pyproject.toml uv.lock data/contractors.csv` | Exit 0; no output. |
| `git add` with the fourteen explicit deliverable/report paths | Exit 128; `fatal: Unable to create '/Users/mike/Hackaton/hack-109052e5-itshich/.git/worktrees/wt-alpha/index.lock': Operation not permitted` |

Implementation proceeded in red→green slices. Observed failures included missing
seam modules and error types, missing synthetic rows, incorrect
tie/truncation order, absent lexical stem matches, wrong empty outcomes, missing
shortfall notes, and the initially absent demo manifest. Targeted seam tests were
rerun after each corresponding implementation. The final suite passed as shown
above. Additional property checks exercise ordering, input immutability, and CSV
round trips with Unicode and quoted/multiline descriptions.

## Reproducible demo requests

All dates are in 2026; language is unspecified in every entry. A dash in duration
means it is omitted. Card ids are in result order.

| Name | City | Category / format | Date | Budget, ₸ | Hours | Outcome | Ordered card ids |
| --- | --- | --- | --- | ---: | ---: | --- | --- |
| dense | Алматы | Ведущий / корпоратив | 2026-10-04 | 2 000 000 | 4 | matched | HK-35215, HK-27222, HK-75012 |
| rare | Алматы | Флорист / свадьба | 2026-10-04 | 250 000 | — | matched | HK-90001, HK-39372 |
| empty_no_category | Астана | Декоратор / свадьба | 2026-10-04 | 250 000 | — | no_category_in_city | [] |
| empty_none_eligible | Алматы | Ведущий / корпоратив | 2026-10-02 | 500 000 | 4 | none_eligible | [] |
| date_pair_a | Алматы | Ведущий / корпоратив | 2026-10-04 | 2 000 000 | 4 | matched | HK-35215, HK-27222, HK-75012 |
| date_pair_b | Алматы | Ведущий / корпоратив | 2026-10-05 | 2 000 000 | 4 | matched | HK-44733, HK-88430, HK-77838 |

The dense request has five eligible profiles out of ten; its top three differ
from the three cheapest eligible profiles. The rare request shows both florists
in Алматы and explains that the city has only two profiles. The all-rejected
request has busy, over-budget, and unsupported-format reasons, including a
profile whose starting price fits but which is busy. Кики (`HK-35215`), first on
4 October, is busy on 5 October and disappears from the second date's cards.
Tests load the committed JSON and check every saved outcome and ordered id list
using `LexicalScorer`, plus these scenario characteristics independently.

## Design details and deviations

- No frozen contract changes were needed. `matcher/model.py` is unchanged.
  `DataError` lives in `matcher.data`; `RequestError` lives in `matcher.filtering`.
- The loader additionally rejects non-positive starting prices and explicit
  non-positive hour limits, and enforces the documented canonical boolean/date
  spellings. This keeps invalid source values out of ranking.
- If there are no related categories, the empty-category note labels its
  present-city suggestions “Другие категории в этом городе” rather than claiming
  that unrelated categories have a similar purpose. It still lists at most three.
- Overlapping rejection reasons add “Причины могут пересекаться.” so their counts
  are not mistaken for disjoint groups. Scoring, outcomes, reason order, and the
  calendar error wording follow DESIGN.md.
- `expected_card_ids` is included in every demo entry as explicitly requested.

Only owned deliverables and this report were created. The pre-existing untracked
`TASK.md` was left untouched. No push was run.

## Commit blocked by workspace permissions

The implementation and verification are complete, but no commit was created.
The worktree's Git metadata is stored outside the writable worktree, and the
environment denied `git add` when it tried to create the index lock at the path
shown above. Escalation is unavailable in this session. `git commit` was not run
after staging failed.

From a shell allowed to write this worktree's Git metadata, the remaining step is:

```sh
git add matcher/data.py matcher/filtering.py matcher/ranking.py matcher/lexical.py matcher/service.py tests/test_data.py tests/test_filtering.py tests/test_ranking.py tests/test_service.py tests/test_properties.py tests/conftest.py scripts/find_demo_queries.py demo/queries.json RESULT.md
git commit -m "Implement deterministic contractor matching and reproducible demos"
```
