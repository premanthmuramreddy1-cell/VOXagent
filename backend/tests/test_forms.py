import asyncio
import functools
import http.server
import threading
from pathlib import Path

import pytest

from app import forms, web

FORM = """<!doctype html><title>Apply</title>
<form action="/done.html" method="get">
  <label for="n">Full name</label><input id="n" name="applicant">
  <label>Email <input type="email" name="mail"></label>
  <label for="c">City</label><select id="c" name="city"><option>Delhi</option><option>Hyderabad</option></select>
  <label for="u">University</label><input id="u" name="uni">
  <label for="p">Password</label><input id="p" type="password" name="pw">
  <label for="cc">Card number</label><input id="cc" name="card" autocomplete="cc-number">
  <label for="q">Why do you want this role?</label><textarea id="q" name="why"></textarea>
  <button type="submit">Send</button>
</form>"""
PROFILE = {"full_name": "Asha Rao", "email": "asha@example.com", "city": "hyderabad", "college": "JNTU",
           "password": "never-used"}


def browser_ready() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return any(Path.home().joinpath("Library/Caches/ms-playwright").glob("chromium*")) or \
        any(Path.home().joinpath(".cache/ms-playwright").glob("chromium*"))


needs_browser = pytest.mark.skipif(not browser_ready(), reason="Playwright Chromium is not installed")


@pytest.fixture
def site(tmp_path, monkeypatch):
    (tmp_path / "form.html").write_text(FORM)
    (tmp_path / "done.html").write_text("<p>thanks</p>")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    async def allow_local(host):  # the SSRF guard refuses localhost; this test serves the form locally
        if host != "127.0.0.1":
            raise web.WebError("blocked")
        return host

    monkeypatch.setattr(web, "_resolve", allow_local)
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_field_matching_never_targets_secrets():
    field = {"label": "", "name": "", "id": "", "autocomplete": "", "type": "text"}
    assert forms.match_field({**field, "label": "Full name"}, PROFILE) == "full_name"
    assert forms.match_field({**field, "label": "E-mail address"}, PROFILE) == "email"
    assert forms.match_field({**field, "label": "College / University"}, PROFILE) == "college"
    assert forms.match_field({**field, "label": "Password", "type": "password"}, PROFILE) is None
    assert forms.match_field({**field, "label": "Card number", "autocomplete": "cc-number"}, PROFILE) is None


def test_form_urls_must_be_public():
    for url in ("http://127.0.0.1:8000/", "file:///etc/passwd", "http://169.254.169.254/"):
        with pytest.raises(forms.FormError):
            asyncio.run(forms.check_url(url))


@needs_browser
def test_fill_previews_then_submits_only_on_request(site):
    async def flow():
        result = await forms.fill("u1", f"{site}/form.html", PROFILE)
        filled = {f["key"] for f in result["filled"]}
        assert filled == {"full_name", "email", "city", "college"}
        assert result["screenshot"] and "u1" in forms.SESSIONS
        page = forms.SESSIONS["u1"].page
        assert await page.input_value("#p") == "" and await page.input_value("#cc") == ""
        assert page.url.endswith("/form.html")  # nothing submitted yet
        done = await forms.submit("u1")
        assert "/done.html?" in done["url"] and "applicant=Asha+Rao" in done["url"] and "city=Hyderabad" in done["url"]
        assert "pw=&" in done["url"] and "u1" not in forms.SESSIONS

    asyncio.run(flow())


@needs_browser
def test_page_cannot_reach_internal_addresses(site):
    async def flow():
        await forms.fill("u2", f"{site}/form.html", PROFILE)
        page = forms.SESSIONS["u2"].page
        status = await page.evaluate("fetch('http://10.0.0.1/admin').then(() => 'reached', () => 'blocked')")
        await forms.cancel("u2")
        return status

    assert asyncio.run(flow()) == "blocked"
