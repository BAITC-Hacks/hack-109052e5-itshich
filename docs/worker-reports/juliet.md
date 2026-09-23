# codex-juliet — B1 offline aspect tagging

Branch: `wt-alpha`. No push. Shared `matcher/aspects.py` unchanged.
`docs/TASKS.md` is absent in this checkout; the supplied B1 instructions were used.
The existing gitignored `.env` symlink was reused without displaying credentials.

## Implementation

`scripts/tag_aspects.py` uses the official OpenAI client via `matcher.config`,
Chat Completions, `gpt-5.4-mini`, temperature 0, seed 42, and strict JSON Schema.
The Russian prompt includes all 24 tag keys and labels, asks for 0–4 supported
tags, and explains semantic boundaries. Eight concurrent requests shorten the
batch. Contractors come from `load_contractors(DATA_PATH)`, including the sibling CSV.
Exact quotes are validated; whitespace-only differences recover the original
contiguous substring. Empty, overlong, unsupported, and invalid-enum evidence is
dropped. Tags and cache keys are sorted. Completed entries are checkpointed.

Existing IDs are skipped unless `--force` is supplied. IDs with no retained tags
are omitted as required, so they remain eligible for another request next run.
`main(..., client=fake)` supports dependency injection. CLI `--dry-run` supplies
an offline fake client and never writes or requires credentials.

## Real generation and evidence review

Two real 78-description batches completed. The first yielded 77 tagged contractors
and dropped 8 invalid quotes. Manual review found semantic overreach, so the prompt
was tightened and a full `--force` batch was run. That final model batch tagged
75 contractors with 162 tags, dropping 7 invalid quotes and 0 invalid tag/polarity
values. A subsequent manual review removed 30 additional tags whose verbatim
quotes did not prove the claimed meaning. No tags or quotes were manually invented.
Examples removed: filming teamwork as team-building, speaking only Kazakh as
multilingual hosting, timely delivery as fast delivery, and a family photo service
as a children's program. The committed cache therefore differs from raw model
output. Future forced regeneration still needs semantic review; substring checks
alone cannot prove semantic support.

Final cache: **68 / 78 contractors tagged; 132 tags; version 1**.
The coverage threshold remains **50**. No shared TAGS keys were added.
The closed taxonomy has no specific tag for generic vocal performances,
light/pixel shows, awards, or children's illustration; such descriptions need
no invented mapping. Possible taxonomy extensions are deferred to its owner.

Untagged IDs: `HK-19103`, `HK-25279`, `HK-26808`, `HK-29829`, `HK-35846`, `HK-88430`, `HK-90002`, `HK-92824`, `SYN-00002`, `SYN-00005`.

## Final tag histogram

| Tag | Count |
| --- | ---: |
| accommodation | 1 |
| branded_merch | 5 |
| business_forum | 5 |
| custom_script | 3 |
| documentary_photo | 10 |
| fast_delivery | 0 |
| improvisation | 7 |
| instant_print | 2 |
| international_repertoire | 3 |
| kazakh_repertoire | 6 |
| kids_program | 0 |
| large_scale_events | 6 |
| live_instruments | 9 |
| multilingual_hosting | 14 |
| outdoor_area | 3 |
| own_kitchen | 3 |
| panoramic_view | 7 |
| posing_guidance | 2 |
| premium_clients | 11 |
| presentation_equipment | 2 |
| team_building | 0 |
| toi_traditions | 0 |
| turnkey_decor | 2 |
| wedding_ceremony | 31 |

## Five example entries

```json
{
  "HK-44733": [
    {
      "polarity": "positive",
      "quote": "Веду как ламповые вечера от 8 человек, так и крупные бизнес форумы на 3000 человек.",
      "tag": "business_forum"
    },
    {
      "polarity": "positive",
      "quote": "Веду как ламповые вечера от 8 человек, так и крупные бизнес форумы на 3000 человек.",
      "tag": "large_scale_events"
    },
    {
      "polarity": "positive",
      "quote": "Языки ведения: Русский, Английский",
      "tag": "multilingual_hosting"
    }
  ],
  "HK-61323": [
    {
      "polarity": "positive",
      "quote": "Последние годы больше внимание уделяю таким жанрам, как свадебный фотожурнализм, документально — свадебный фотожурнализм.",
      "tag": "documentary_photo"
    },
    {
      "polarity": "positive",
      "quote": "я научу, как свободно чувствовать себя перед камерой и получить удовольствие от съемки.",
      "tag": "posing_guidance"
    },
    {
      "polarity": "positive",
      "quote": "Я свадебный фотограф.",
      "tag": "wedding_ceremony"
    }
  ],
  "HK-90003": [
    {
      "polarity": "positive",
      "quote": "Работаем под ключ: проект, монтаж, демонтаж.",
      "tag": "turnkey_decor"
    },
    {
      "polarity": "neutral",
      "quote": "для свадеб и корпоративных мероприятий",
      "tag": "wedding_ceremony"
    }
  ],
  "HK-90007": [
    {
      "polarity": "positive",
      "quote": "Работаю по сценарию пары, без шаблонных фраз — каждая церемония собирается индивидуально под историю молодожёнов.",
      "tag": "custom_script"
    },
    {
      "polarity": "positive",
      "quote": "на казахском и русском языках.",
      "tag": "multilingual_hosting"
    },
    {
      "polarity": "positive",
      "quote": "Провожу выездную регистрацию и церемонию бракосочетания",
      "tag": "wedding_ceremony"
    }
  ],
  "SYN-00004": [
    {
      "polarity": "positive",
      "quote": "На конференциях музыканты играют инструментальные сеты во время регистрации, оставляя место для разговора гостей.",
      "tag": "business_forum"
    },
    {
      "polarity": "positive",
      "quote": "Вокал сопровождают электропиано, контрабас и ударные со щётками.",
      "tag": "live_instruments"
    }
  ]
}
```

## Verification

The new tests were run first and failed for the missing script/cache (6 failures).
Offline behavior tests then passed before making API requests.
Final verification after the final cache review:

- `uv run pytest -q -o addopts= tests/test_aspects.py`: **6 passed** (0.09 s).
- `uv run pytest -q -o addopts=`: **286 passed, 4 skipped** (14.55 s).
- Standalone `--dry-run` processed all 78 descriptions offline and wrote no cache.
- Tests verify both existing-file and absent-file dry runs, invalid tags/quotes,
  whitespace recovery, deterministic ordering, cache reuse, and `--force`.
- `git diff --check`: no whitespace errors.

Commands use `UV_CACHE_DIR=/tmp/codex-juliet-uv-cache` because the default uv cache
is outside the writable sandbox. No dependency files were changed.

## Commit blocked by filesystem permissions

Attempted `git add -- scripts/tag_aspects.py data/aspects.json tests/test_aspects.py RESULT.md`.
Git exited 128: it could not create
`/Users/mike/Hackaton/hack-109052e5-itshich/.git/worktrees/wt-alpha/index.lock`
(`Operation not permitted`). This worktree's Git metadata is outside the writable
sandbox, and approval escalation is unavailable. No commit or push was performed.
All four deliverables are saved locally. The pre-existing untracked `TASK.md` was
left untouched.
