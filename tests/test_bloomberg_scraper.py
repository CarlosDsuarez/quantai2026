"""Tests del scraper: carga de cookies, pre-flight de sesion, lista de URLs,
normalizacion de timestamps, parser JSON y politica de paywall.

Ningun test toca la red ni lanza un navegador: las rutas que navegarian
(safe_goto / extract_article) se monkeypatchean.
"""

import json
import time

import pandas as pd
import pytest

import bloomberg_scraper as bs


# ---------------------------------------------------------------------------
# load_cookies_for_playwright
# ---------------------------------------------------------------------------
def _write_cookies(tmp_path, cookies):
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps(cookies))
    return path


def test_load_cookies_converts_cookie_editor_format(tmp_path):
    future = time.time() + 3600
    path = _write_cookies(tmp_path, [
        {"name": "a", "value": "1", "domain": ".bloomberg.com", "path": "/",
         "secure": True, "httpOnly": False, "sameSite": "no_restriction",
         "expirationDate": future},
        {"name": "b", "value": "2", "sameSite": "unspecified"},
        {"name": "c", "value": "3", "sameSite": "strict"},
    ])
    out = bs.load_cookies_for_playwright(path)
    assert [c["name"] for c in out] == ["a", "b", "c"]
    assert out[0]["sameSite"] == "None"
    assert out[0]["expires"] == pytest.approx(future)
    assert out[1]["domain"] == ".bloomberg.com"  # default
    assert out[1]["sameSite"] == "Lax"
    assert out[2]["sameSite"] == "Strict"


def test_load_cookies_skips_entries_without_name_or_value(tmp_path):
    path = _write_cookies(tmp_path, [{"name": "solo-name"}, {"name": "ok", "value": "v"}])
    out = bs.load_cookies_for_playwright(path)
    assert [c["name"] for c in out] == ["ok"]


def test_load_cookies_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit) as e:
        bs.load_cookies_for_playwright(tmp_path / "nope.json")
    assert e.value.code == 2


def test_load_cookies_invalid_payload_exits(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps({"not": "a list"}))
    with pytest.raises(SystemExit) as e:
        bs.load_cookies_for_playwright(path)
    assert e.value.code == 2


# ---------------------------------------------------------------------------
# check_session_cookies (pre-flight)
# ---------------------------------------------------------------------------
NOW = 1_800_000_000.0


def _cookie(name, expires=None):
    c = {"name": name, "value": "x", "domain": ".bloomberg.com", "path": "/"}
    if expires is not None:
        c["expires"] = expires
    return c


def test_preflight_missing_session_token_is_fatal():
    fatal, _ = bs.check_session_cookies([_cookie("_px2", NOW + 900)], now=NOW)
    assert len(fatal) == 1
    assert bs.SESSION_COOKIE in fatal[0]


def test_preflight_expired_session_token_is_fatal():
    fatal, _ = bs.check_session_cookies(
        [_cookie(bs.SESSION_COOKIE, NOW - 2700)], now=NOW)
    assert len(fatal) == 1
    assert "vencio hace 45 min" in fatal[0]


def test_preflight_live_token_warns_remaining_window():
    fatal, warnings = bs.check_session_cookies(
        [_cookie(bs.SESSION_COOKIE, NOW + 1800),
         _cookie("_px2", NOW + 900), _cookie("_pxde", NOW + 900)],
        now=NOW,
    )
    assert fatal == []
    assert any("vence en 30 min" in w for w in warnings)


def test_preflight_expired_or_missing_antibot_cookies_warn():
    fatal, warnings = bs.check_session_cookies(
        [_cookie(bs.SESSION_COOKIE, NOW + 1800), _cookie("_px2", NOW - 60)],
        now=NOW,
    )
    assert fatal == []
    assert any("'_px2' ya vencio" in w for w in warnings)
    assert any("'_pxde'" in w for w in warnings)


def test_preflight_session_cookie_without_expiry_is_not_fatal():
    # cookie de sesion (sin expiry explicito): no se puede afirmar que vencio
    fatal, _ = bs.check_session_cookies([_cookie(bs.SESSION_COOKIE)], now=NOW)
    assert fatal == []


# ---------------------------------------------------------------------------
# load_url_list
# ---------------------------------------------------------------------------
CSV_HEADER = "ticker,headline,url,published_date_display,excluded_reason\n"


def test_load_url_list_filters_and_dedups(tmp_path):
    path = tmp_path / "PBR.csv"
    path.write_text(
        CSV_HEADER
        + 'PBR,"A",https://www.bloomberg.com/news/articles/2025-07-03/a,"July 3, 2025",\n'
        + 'PBR,"B video",https://www.bloomberg.com/news/videos/2025-07-05/b,"July 5, 2025",video\n'
        + 'PBR,"A dup",https://www.bloomberg.com/news/articles/2025-07-03/a,"July 3, 2025",\n'
        + 'PBR,"sin url",,"July 6, 2025",\n'
        + 'pbr,"C",https://www.bloomberg.com/news/articles/2025-07-07/c,"July 7, 2025",\n'
    )
    df = bs.load_url_list(path)
    assert len(df) == 2
    assert set(df["ticker"]) == {"PBR"}  # upper-cased


def test_load_url_list_missing_column_exits(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("ticker,headline,url\nPBR,x,http://a\n")
    with pytest.raises(SystemExit) as e:
        bs.load_url_list(path)
    assert e.value.code == 2


def test_load_url_list_all_rows_excluded_exits(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text(CSV_HEADER + 'PBR,"B",https://x/b,"July 5, 2025",video\n')
    with pytest.raises(SystemExit):
        bs.load_url_list(path)


# ---------------------------------------------------------------------------
# _normalize_ts
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("2025-07-03T14:22:00Z", "2025-07-03T14:22:00+00:00"),
    ("May 20, 2025", "2025-05-20T00:00:00+00:00"),
    ("1751551320", "2025-07-03T14:02:00+00:00"),      # epoch s
    ("1751551320000", "2025-07-03T14:02:00+00:00"),   # epoch ms
    ("", None),
    (None, None),
    ("no es una fecha", None),
])
def test_normalize_ts(raw, expected):
    assert bs._normalize_ts(raw) == expected


# ---------------------------------------------------------------------------
# Parser JSON: _find_article_subtree + _walk_collect_text
# ---------------------------------------------------------------------------
def test_find_article_subtree_locates_headline_and_body():
    payload = {
        "props": {"pageProps": {"story": {
            "headline": "Titular",
            "body": {"content": [
                {"type": "paragraph", "content": [{"type": "text", "value": "Parrafo uno."}]},
                {"type": "paragraph", "content": [{"type": "text", "value": "Parrafo dos."}]},
            ]},
            "datePublished": "2025-07-03T14:22:00Z",
        }}}
    }
    subtree = bs._find_article_subtree(payload)
    assert subtree["headline"] == "Titular"
    parts: list = []
    bs._walk_collect_text(subtree["body"], parts)
    assert parts == ["Parrafo uno.", "Parrafo dos."]


def test_find_article_subtree_returns_none_when_absent():
    assert bs._find_article_subtree({"foo": {"bar": [1, 2, 3]}}) is None


# ---------------------------------------------------------------------------
# Politica de paywall en scrape_url_list
# ---------------------------------------------------------------------------
class _NoopLimiter:
    def wait(self):
        pass


class _DummyPage:
    def content(self):
        return "<html></html>"


def _rows(n):
    return pd.DataFrame([{
        "ticker": "PBR",
        "headline": f"H{i}",
        "url": f"https://www.bloomberg.com/news/articles/2025-07-03/a{i}",
        "published_date_display": "July 3, 2025",
        "excluded_reason": "",
    } for i in range(n)])


@pytest.fixture
def isolated_articles_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "ARTICLES_DIR", tmp_path / "articles")
    monkeypatch.setattr(bs, "SAMPLES_DIR", tmp_path / "articles" / "_samples")
    monkeypatch.setattr(bs, "PARSE_FAILURES_LOG", tmp_path / "outputs" / "parse_failures.log")
    (tmp_path / "articles").mkdir(parents=True)
    return tmp_path / "articles"


def test_consecutive_paywalls_abort(monkeypatch, isolated_articles_dir):
    def always_paywalled(page, url, limiter):
        raise bs.PaywallDetected(f"Paywall/login detectado en {url}")

    monkeypatch.setattr(bs, "safe_goto", always_paywalled)
    with pytest.raises(bs.SessionExpiredError):
        bs.scrape_url_list(_DummyPage(), _NoopLimiter(), "PBR", "Petrobras",
                           _rows(bs.MAX_CONSECUTIVE_PAYWALLS + 2))


def test_isolated_paywall_is_skipped_and_counter_resets(monkeypatch, isolated_articles_dir):
    # patron: paywall, ok, paywall, ok, ... nunca llega a 3 consecutivos
    calls = {"n": 0}

    def alternating(page, url, limiter):
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            raise bs.PaywallDetected(f"Paywall/login detectado en {url}")

    monkeypatch.setattr(bs, "safe_goto", alternating)
    monkeypatch.setattr(bs, "extract_article",
                        lambda page: ("H", "x" * 200, "2025-07-03T00:00:00+00:00", "json"))

    added = bs.scrape_url_list(_DummyPage(), _NoopLimiter(), "PBR", "Petrobras", _rows(6))
    assert added == 3  # los 3 pares; los 3 impares saltados

    df = pd.read_parquet(isolated_articles_dir / "PBR.parquet")
    assert len(df) == 3
    # los saltados NO se marcaron como scrapeados -> siguen pendientes
    assert bs.load_existing_urls("PBR") == set(df["url"])


def test_paywalled_articles_remain_retryable(monkeypatch, isolated_articles_dir):
    def always_paywalled(page, url, limiter):
        raise bs.PaywallDetected("paywall")

    monkeypatch.setattr(bs, "safe_goto", always_paywalled)
    with pytest.raises(bs.SessionExpiredError):
        bs.scrape_url_list(_DummyPage(), _NoopLimiter(), "PBR", "Petrobras", _rows(3))
    # nada se persistio como procesado
    assert bs.load_existing_urls("PBR") == set()


def test_failed_parse_not_marked_as_scraped(monkeypatch, isolated_articles_dir):
    monkeypatch.setattr(bs, "safe_goto", lambda page, url, limiter: None)
    monkeypatch.setattr(bs, "extract_article",
                        lambda page: ("H", "corto", None, None))  # < MIN_BODY_CHARS

    added = bs.scrape_url_list(_DummyPage(), _NoopLimiter(), "PBR", "Petrobras", _rows(2))
    assert added == 0
    assert bs.load_existing_urls("PBR") == set()
    failures = list((isolated_articles_dir / "_samples").glob("failed_parse_PBR_*.html"))
    assert len(failures) == 2
