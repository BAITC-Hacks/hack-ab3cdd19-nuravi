import csv
import pytest

from matcher.data import DATA, load_catalog


def write_rows(tmp_path, rows, fields=None):
    path = tmp_path / "test.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def raw():
    with DATA.open(encoding="utf-8") as stream:
        return next(csv.DictReader(stream))


def test_source_integrity():
    c = load_catalog()
    assert c.source_rows == len(c.profiles) == 66
    assert not c.issues
    assert sum(p.synthetic for p in c.profiles) == 13
    assert sum(p.city_imputed for p in c.profiles) == 8
    assert sum(p.price_imputed for p in c.profiles) == 18


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("anon_name", ""), ("city", ""), ("categories", ""),
    ("price_from_kzt", ""), ("price_from_kzt", "NaN"), ("price_from_kzt", "-2"),
    ("event_formats", ""), ("languages", ""), ("max_hours", "broken"),
    ("max_hours", "-5"), ("busy_dates", "oops"),
    ("busy_dates", "2027-01-01"), ("synthetic", "yes"), ("price_imputed", ""),
])
def test_bad_rows_quarantined(tmp_path, raw, field, value):
    bad = dict(raw, id="BAD")
    bad[field] = value
    c = load_catalog(write_rows(tmp_path, [raw, bad]))
    assert len(c.profiles) == 1
    assert len(c.issues) == 1
    assert c.source_rows == 2


def test_missing_header_fails_clearly(tmp_path):
    with pytest.raises(ValueError, match="отсутствуют поля"):
        load_catalog(write_rows(tmp_path, [{"id": "x"}]))


def test_duplicate_id_reported(tmp_path, raw):
    c = load_catalog(write_rows(tmp_path, [raw, raw]))
    assert len(c.profiles) == 1 and "повторяющийся id" in c.issues[0]


def test_malformed_row_does_not_reserve_id(tmp_path, raw):
    c = load_catalog(write_rows(tmp_path, [dict(raw, price_from_kzt="bad"), raw]))
    assert len(c.profiles) == 1 and len(c.issues) == 1


@pytest.mark.parametrize("empty_calendar", ["", "[]", "null"])
def test_null_hours_and_empty_calendar_are_unknown(tmp_path, raw, empty_calendar):
    raw.update(max_hours="null", busy_dates=empty_calendar, description="")
    c = load_catalog(write_rows(tmp_path, [raw]))
    assert not c.issues
    assert c.profiles[0].max_hours is None
    assert c.profiles[0].busy_dates is None


def test_whitespace_and_pipe_normalization(tmp_path, raw):
    raw.update(city="  Алматы  ", languages=" русский | казахский | русский ")
    p = load_catalog(write_rows(tmp_path, [raw])).profiles[0]
    assert p.city == "Алматы"
    assert p.languages == ("казахский", "русский")
