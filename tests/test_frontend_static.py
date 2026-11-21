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
