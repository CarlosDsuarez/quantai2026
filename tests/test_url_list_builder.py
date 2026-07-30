"""Tests del builder de listas de URLs (fuentes de archivo publico).
La red se simula inyectando `fetch`; ningun test sale a internet.
"""

import json
from datetime import date

import pytest

import url_list_builder as ulb


# ---------------------------------------------------------------------------
# Funciones puras
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("nombre,slug", [
    ("Petrobras", "petrobras"),
    ("Bank of America", "bank-of-america"),
    ("Johnson & Johnson", "johnson-johnson"),
    ("Coca-Cola", "coca-cola"),
    ("Itau Unibanco", "itau-unibanco"),
])
def test_slugify_company(nombre, slug):
    assert ulb.slugify_company(nombre) == slug


@pytest.mark.parametrize("raw,expected", [
    ("https://www.bloomberg.com/news/articles/2025-07-03/petrobras-x?utm_source=rss",
     "https://www.bloomberg.com/news/articles/2025-07-03/petrobras-x"),
    ("http://bloomberg.com/news/articles/2025-07-03/petrobras-x",
     "https://www.bloomberg.com/news/articles/2025-07-03/petrobras-x"),
    ("https://www.bloomberg.com/news/articles/2025-07-03/petrobras-x/",
     "https://www.bloomberg.com/news/articles/2025-07-03/petrobras-x"),
    ("https://www.bloomberg.com/news/articles/2025-07-03/petrobras-x%0A",
     "https://www.bloomberg.com/news/articles/2025-07-03/petrobras-x"),  # \n encoded
    ("https://www.bloomberg.com/opinion/articles/2025-07-03/x", None),   # no /news/
    ("https://example.com/news/articles/2025-07-03/x", None),            # otro host
])
def test_canonicalize_url(raw, expected):
    assert ulb.canonicalize_url(raw) == expected


@pytest.mark.parametrize("url,pub,reason", [
    ("https://www.bloomberg.com/news/articles/2025-07-03/petrobras-x", "2025-07-03", ""),
    ("https://www.bloomberg.com/news/features/2025-06-01/deep-dive", "2025-06-01", ""),
    ("https://www.bloomberg.com/news/videos/2025-07-05/clip", "2025-07-05", "video"),
    ("https://www.bloomberg.com/news/audio/2025-07-05/pod", "2025-07-05", "audio"),
    ("https://www.bloomberg.com/news/live-blog/2025-07-05/fed", "2025-07-05", "no-articulo"),
    ("https://www.bloomberg.com/news/terminal/XYZ", None, "no-articulo"),
])
def test_classify_url(url, pub, reason):
    assert ulb.classify_url(url) == (pub, reason)


def test_display_date():
    assert ulb.display_date("2025-07-03") == "July 3, 2025"
    assert ulb.display_date(None) == ""
    assert ulb.display_date("garbage") == ""


def test_slug_to_headline():
    url = "https://www.bloomberg.com/news/articles/2025-07-03/petrobras-lifts-output-target"
    assert ulb.slug_to_headline(url) == "Petrobras Lifts Output Target"


def test_keyword_regex_escapes_and_alternates():
    assert ulb.keyword_regex(["coca-cola", "ko.us"]) == r".*(coca\-cola|ko\.us).*"


def test_month_prefixes_covers_range_and_sections():
    prefixes = ulb.month_prefixes(date(2025, 11, 15), date(2026, 2, 1))
    assert prefixes == [
        "bloomberg.com/news/articles/2025-11*",
        "bloomberg.com/news/articles/2025-12*",
        "bloomberg.com/news/articles/2026-01*",
        "bloomberg.com/news/articles/2026-02*",
        "bloomberg.com/news/features/2025-11*",
        "bloomberg.com/news/features/2025-12*",
        "bloomberg.com/news/features/2026-01*",
        "bloomberg.com/news/features/2026-02*",
    ]


def test_month_prefixes_single_month():
    assert ulb.month_prefixes(date(2025, 7, 1), date(2025, 7, 31)) == [
        "bloomberg.com/news/articles/2025-07*",
        "bloomberg.com/news/features/2025-07*",
    ]


# ---------------------------------------------------------------------------
# build_rows
# ---------------------------------------------------------------------------
def test_build_rows_dedup_filter_and_sort():
    urls = [
        "https://www.bloomberg.com/news/articles/2025-07-03/petrobras-b?utm=1",
        "https://www.bloomberg.com/news/articles/2025-07-03/petrobras-b",      # dup canonico
        "https://www.bloomberg.com/news/articles/2024-01-02/petrobras-a",
        "https://www.bloomberg.com/news/videos/2025-05-01/petrobras-clip",
        "https://www.bloomberg.com/news/articles/2020-01-01/petrobras-viejo",  # fuera de rango
        "https://example.com/otra-cosa",                                       # otro host
    ]
    rows = ulb.build_rows("PBR", urls, (date(2023, 7, 1), date(2026, 7, 15)))
    assert [r.url.rsplit("/", 1)[-1] for r in rows] == \
        ["petrobras-a", "petrobras-clip", "petrobras-b"]
    assert [r.excluded_reason for r in rows] == ["", "video", ""]
    assert rows[0].published_date_display == "January 2, 2024"
    assert all(r.ticker == "PBR" for r in rows)


# ---------------------------------------------------------------------------
# merge_into_csv: las filas existentes (curadas a mano) ganan
# ---------------------------------------------------------------------------
def test_merge_into_csv_preserves_existing_rows(tmp_path):
    path = tmp_path / "PBR.csv"
    path.write_text(
        "ticker,headline,url,published_date_display,excluded_reason\n"
        'PBR,"Headline curado a mano",https://www.bloomberg.com/news/articles/2025-07-03/petrobras-b,"July 3, 2025",\n'
    )
    rows = ulb.build_rows("PBR", [
        "https://www.bloomberg.com/news/articles/2025-07-03/petrobras-b",  # ya existe
        "https://www.bloomberg.com/news/articles/2025-07-04/petrobras-c",  # nueva
    ])
    new, total = ulb.merge_into_csv(path, rows)
    assert (new, total) == (1, 2)
    content = path.read_text()
    assert "Headline curado a mano" in content       # no fue pisado
    assert "petrobras-c" in content


def test_merge_into_csv_creates_dir_and_file(tmp_path):
    path = tmp_path / "sub" / "VALE.csv"
    rows = ulb.build_rows("VALE", ["https://www.bloomberg.com/news/articles/2025-07-04/vale-x"])
    new, total = ulb.merge_into_csv(path, rows)
    assert (new, total) == (1, 1)
    assert path.exists()


# ---------------------------------------------------------------------------
# Fuentes con fetch inyectado
# ---------------------------------------------------------------------------
class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise json.JSONDecodeError("no json", "", 0)
        return self._payload


def test_fetch_wayback_urls_queries_monthly_prefixes():
    seen_prefixes = []

    def fetch(url, params):
        assert url == ulb.WAYBACK_CDX
        assert params["filter"] == "original:.*(petrobras).*"
        seen_prefixes.append(params["url"])
        if params["url"] == "bloomberg.com/news/articles/2025-07*":
            return FakeResp(payload=[
                ["original"],
                ["https://www.bloomberg.com/news/articles/2025-07-03/petrobras-x"],
                ["https://www.bloomberg.com/news/articles/2025-07-05/petrobras-y"],
            ])
        return FakeResp(payload=[["original"]])  # mes sin matches

    urls, failed = ulb.fetch_wayback_urls(
        ["petrobras"], date(2025, 6, 1), date(2025, 7, 31), fetch=fetch)
    assert len(urls) == 2
    assert failed == []
    assert seen_prefixes == ulb.month_prefixes(date(2025, 6, 1), date(2025, 7, 31))


def test_fetch_wayback_urls_reports_failed_prefixes_and_continues():
    def fetch(url, params):
        if params["url"] == "bloomberg.com/news/articles/2025-06*":
            raise RuntimeError("request agoto reintentos: timeout")
        if params["url"] == "bloomberg.com/news/features/2025-06*":
            return FakeResp(status_code=503)
        return FakeResp(payload=[
            ["original"],
            [f"https://www.bloomberg.com/news/articles/2025-07-01/petrobras-{params['url'][-8:-1]}"],
        ])

    urls, failed = ulb.fetch_wayback_urls(
        ["petrobras"], date(2025, 6, 1), date(2025, 7, 31), fetch=fetch)
    assert len(urls) == 2  # articles/2025-07 y features/2025-07
    assert failed == [
        "bloomberg.com/news/articles/2025-06*",
        "bloomberg.com/news/features/2025-06*",
    ]


def test_fetch_wayback_urls_second_pass_recovers_transient_failures():
    # el primer intento de un prefijo da 503; el segundo pase lo recupera
    attempts: dict[str, int] = {}

    def fetch(url, params):
        prefix = params["url"]
        attempts[prefix] = attempts.get(prefix, 0) + 1
        if prefix == "bloomberg.com/news/articles/2025-07*" and attempts[prefix] == 1:
            return FakeResp(status_code=503)
        return FakeResp(payload=[
            ["original"],
            [f"https://www.bloomberg.com/news/articles/2025-07-01/petrobras-{len(attempts)}"],
        ])

    urls, failed = ulb.fetch_wayback_urls(
        ["petrobras"], date(2025, 7, 1), date(2025, 7, 31), fetch=fetch)
    assert failed == []
    assert len(urls) == 2  # articles (2do pase) + features (1er pase)
    assert attempts["bloomberg.com/news/articles/2025-07*"] == 2
