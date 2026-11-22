"""Статические проверки фронтенда (без браузера): правильные пути API и абсолютные пути ассетов."""
import re
from pathlib import Path

FRONT = Path(__file__).resolve().parent.parent / "frontend"


def test_assets_are_absolute_so_join_page_works():
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    local = [r for r in refs if not r.startswith(("http", "#", "data:"))]
    assert local and all(r.startswith("/") for r in local), local  # /join/<token> иначе сломает относительные пути


def test_all_scripts_exist():
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    for src in re.findall(r'src="/(js/[^"]+)"', html):
        assert (FRONT / src).exists(), src


def test_api_client_uses_api_prefix_and_health_contract():
    js = (FRONT / "js" / "api.js").read_text(encoding="utf-8")
    assert '"/api" + path' in js and "/api/health" in js and 'data.service === "woody"' in js


def test_frontend_calls_existing_endpoints():
    app_py = (FRONT.parent / "backend" / "main.py").read_text(encoding="utf-8")
    routes = set(re.findall(r'@app\.(?:get|post|put|delete)\("(/api/[^"]+)"', app_py))
    used = set()
    for js in (FRONT / "js").glob("*.js"):
        used |= set(re.findall(r'(?:request|api)\(\s*[`"](/[a-z/]+)', js.read_text(encoding="utf-8")))
    for path in used:
        base = "/api" + path.rstrip("/")
        assert any(r == base or r.startswith(base + "/") or base.startswith(r.split("{")[0].rstrip("/")) for r in routes), base


def test_every_element_id_used_in_js_exists_in_html():
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    for js in (FRONT / "js").glob("*.js"):
        for used in re.findall(r'(?:getElementById|\$)\(\s*"([a-z0-9\-]+)"\s*\)', js.read_text(encoding="utf-8")):
            assert used in ids, f"{js.name}: нет элемента #{used}"


def test_intro_form_does_not_overlap_the_face():
    css = (FRONT / "css" / "style.css").read_text(encoding="utf-8")
    block = re.search(r"\.intro\s*\{([^}]*)\}", css).group(1)
    assert "absolute" not in block  # поле имени — обычный блок под лицом, а не поверх рта


def test_learning_effect_and_airdrop_markup_present():
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    assert 'id="learn-fx"' in html and 'id="airdrop"' in html and 'id="ad-send"' in html
