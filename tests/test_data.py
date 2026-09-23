from collections import Counter

import pytest

from matcher.data import load_contractors
from matcher import data
from matcher.model import CALENDAR_END, CALENDAR_START


def test_loads_real_catalogue_with_flags_lists_and_calendar(real_contractors):
    assert len(real_contractors) == 66
    assert [c.id for c in real_contractors] == sorted(c.id for c in real_contractors)
    assert Counter(c.city for c in real_contractors) == {
        "Алматы": 50, "Астана": 15, "Зарубежье": 1,
    }
    assert Counter(c.city for c in real_contractors if "Ведущий" in c.categories) == {
        "Алматы": 10, "Астана": 5,
    }
    assert sum("Флорист" in c.categories for c in real_contractors) == 3
    decorators = [c for c in real_contractors if "Декоратор" in c.categories]
    assert len(decorators) == 3 and all(c.city == "Алматы" for c in decorators)
    assert sum(c.max_hours is None for c in real_contractors) == 9
    assert sum(c.city_imputed for c in real_contractors) == 8
    assert sum(c.price_imputed for c in real_contractors) == 18
    assert sum(c.synthetic for c in real_contractors) == 13
    assert all(CALENDAR_START <= d <= CALENDAR_END for c in real_contractors for d in c.busy_dates)
    florist = next(c for c in real_contractors if c.id == "HK-39372")
    assert florist.name == "Тони Тони Чоппер"
    assert florist.price_from_kzt == 200_000
    assert florist.languages == ("русский",)


@pytest.mark.parametrize("field,value", [
    ("city", "Москва"), ("event_formats", "корпоратив|бал"),
    ("languages", "русский|французский"), ("busy_dates", "2026-02-30"),
    ("busy_dates", "20261001"), ("busy_dates", "2026-09-22"),
    ("busy_dates", "2027-01-01"), ("price_from_kzt", "200.5"),
    ("price_from_kzt", "free"), ("price_from_kzt", "-1"),
    ("city_imputed", "yes"), ("price_imputed", "false"), ("synthetic", "1"),
    ("max_hours", "0"), ("max_hours", "1.5"),
])
def test_invalid_rows_report_their_id(csv_row, write_csv, field, value):
    path = write_csv([csv_row | {field: value}])
    with pytest.raises(data.DataError, match="csv-01"):
        load_contractors(path)


@pytest.mark.parametrize("synthetic", ["True", "False"])
def test_extra_catalogue_requires_synthetic_and_merges_in_id_order(csv_row, write_csv, synthetic):
    path = write_csv([csv_row | {"id": "z-last"}])
    write_csv([csv_row | {"id": "a-extra", "synthetic": synthetic}], "synthetic_extra.csv")
    if synthetic == "False":
        with pytest.raises(data.DataError, match="a-extra.*synthetic"):
            load_contractors(path)
    else:
        loaded = load_contractors(path)
        assert [c.id for c in loaded] == ["a-extra", "z-last"]
        assert [c.synthetic for c in loaded] == [True, False]
        assert loaded[0].categories == ("Ведущий", "Ведущий церемонии")
        assert loaded[0].busy_dates == frozenset((CALENDAR_START, CALENDAR_END))
        assert loaded[0].description == csv_row["description"]
