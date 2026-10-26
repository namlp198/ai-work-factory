"""The dashboard ships in English and Vietnamese: both dictionaries must cover every text the UI shows."""

from __future__ import annotations

import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "apps" / "control_api" / "awf_api" / "static"
LANGUAGES = ("en", "vi")


def _dictionaries() -> dict[str, dict[str, str]]:
    source = (STATIC / "i18n.js").read_text(encoding="utf-8")
    body = source[source.index("window.AWF_I18N = ") + len("window.AWF_I18N = "):].strip().rstrip(";")
    return json.loads(body)


def _used_keys() -> set[str]:
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    keys = set(re.findall(r'(?<![\w.])t\("([\w.\-]+)"', app))
    for key in re.findall(r'(?<![\w.])tn\("([\w.\-]+)"', app):
        keys |= {f"{key}.one", f"{key}.other"}
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    keys |= set(re.findall(r'data-i18n(?:-title)?="([\w.\-]+)"', page))
    return keys


def test_every_language_has_the_same_keys():
    dicts = _dictionaries()
    assert set(dicts) == set(LANGUAGES)
    en = set(dicts["en"])
    for lang in LANGUAGES:
        assert set(dicts[lang]) == en, f"{lang}: missing {sorted(en - set(dicts[lang]))}, extra {sorted(set(dicts[lang]) - en)}"
        empty = [k for k, v in dicts[lang].items() if not str(v).strip()]
        assert not empty, f"{lang}: empty texts {empty}"


def test_every_text_the_ui_uses_is_translated():
    dicts = _dictionaries()
    missing = sorted(_used_keys() - set(dicts["en"]))
    assert not missing, f"t() keys without a translation: {missing}"


def test_placeholders_match_between_languages():
    dicts = _dictionaries()
    for key, text in dicts["en"].items():
        expected = set(re.findall(r"\{(\w+)\}", text))
        for lang in LANGUAGES:
            assert set(re.findall(r"\{(\w+)\}", dicts[lang][key])) == expected, f"{lang}:{key}"


def test_dynamic_keys_cover_their_values():
    dicts = _dictionaries()["en"]
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    for prefix, values in {"platform": re.search(r"const PLATFORMS = \[([^\]]*)\]", app).group(1),
                           "engine": re.search(r"const ENGINES = \[([^\]]*)\]", app).group(1)}.items():
        for value in re.findall(r'"([\w-]+)"', values):
            assert f"{prefix}.{value}" in dicts
    for value in ("internal", "public", "restricted"):
        assert f"confidentiality.{value}" in dicts


def test_the_page_loads_the_dictionary_before_the_app(client):
    html = client.get("/").text
    assert html.index("/static/i18n.js?v=") < html.index("/static/app.js?v=")
    assert 'id="lang-select"' in html
    assert "/static/i18n.js?v=" in client.get("/sw.js").text  # cached for offline use like the rest of the shell
