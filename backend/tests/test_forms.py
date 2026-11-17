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
# Questions drawn late by script, radio inputs hidden under styled labels and a decoy type=submit menu button,
# the way Microsoft Forms renders a quiz.
QUIZ = """<!doctype html><title>Quiz</title><button type="submit" aria-label="Form Menu">...</button>
<div id="root"></div><script>setTimeout(() => { document.getElementById('root').innerHTML = `
<form action="/thanks.html" method="get">
 <div><span>1.</span><span>What is 1 + 1?</span><div role="radiogroup">
  <label><input type="radio" name="q1" value="1" style="opacity:0;position:absolute">1</label>
  <label><input type="radio" name="q1" value="2" style="opacity:0;position:absolute">2</label></div></div>
 <div><span>Which are fruits?</span><div role="group">
  <label><input type="checkbox" name="q2" value="apple">Apple</label>
  <label><input type="checkbox" name="q2" value="brick">Brick</label>
  <label><input type="checkbox" name="q2" value="mango">Mango</label></div></div>
 <div><label for="dob">Date of birth</label><input id="dob" name="dob"></div>
 <button type="submit">Submit</button></form>`; }, 800);</script>"""
# A form that only shows its questions to a signed-in visitor (a session cookie) and prints Microsoft's notice.
MEMBERS = """<!doctype html><title>Members</title><div id="root"></div><script>
if (document.cookie.includes('session=ok')) document.getElementById('root').innerHTML = `
<p>When you submit this form, it will not automatically collect your details like name and email address unless
you provide it yourself.</p><form action="/done.html"><label for="nm">Your name</label><input id="nm" name="nm">
<label for="em">Email</label><input id="em" name="em"><button type="submit">Submit</button></form>`;</script>"""
# Two sections behind a Next button, the way Google Forms pages a long form; one box starts ticked.
PAGED = """<!doctype html><title>Survey</title><form action="/done.html">
<div id="one"><p>Favourite colour?</p><div role="radiogroup"><label><input type="radio" name="c" value="red">Red</label>
<label><input type="radio" name="c" value="blue">Blue</label></div>
<div role="button" tabindex="0" onclick="one.hidden=true;two.hidden=false">Next</div></div>
<div id="two" hidden><p>Which do you own?</p><div role="group">
<label><input type="checkbox" name="o" value="car" checked>Car</label><label><input type="checkbox" name="o" value="bike">Bike</label>
</div><button type="submit">Submit</button></div></form>"""
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
    (tmp_path / "quiz.html").write_text(QUIZ)
    (tmp_path / "members.html").write_text(MEMBERS)
    (tmp_path / "paged.html").write_text(PAGED)
    (tmp_path / "thanks.html").write_text("<p>Your response was submitted.</p>")
    (tmp_path / "enter.html").write_text('<form action="/done.html"><label for="n">Full name</label><input id="n" name="who"></form>')
    (tmp_path / "apply.html").write_text('<form action="/done.html"><label for="n">Full name</label>'
                                         '<input id="n" name="who"><input type="submit" value="Apply now"></form>')
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
        filled = {f["key"] for f in result["filled"]}  # without a model only the profile fills fields
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


@needs_browser
def test_quiz_answers_choice_questions_and_submits_with_the_real_button(site):
    asked = []

    async def model(messages, schema=None):
        asked.append(messages[-1]["content"])
        return '{"0.0": "2", "0.1": ["Apple", "Mango"], "0.2": "1 Jan 1990"}'  # the dob answer is ignored below

    async def flow():
        result = await forms.fill("u3", f"{site}/quiz.html", {}, model, "fill this quiz")
        page = forms.SESSIONS["u3"].page
        checked = await page.evaluate("Array.from(document.querySelectorAll('input:checked')).map(e => e.value)")
        done = await forms.submit("u3")
        return result, checked, done

    result, checked, done = asyncio.run(flow())
    assert '"What is 1 + 1?"' in asked[0] and '"options": ["1", "2"]' in asked[0]
    assert sorted(checked) == ["2", "apple", "mango"]
    assert {f["field"] for f in result["filled"]} == {"What is 1 + 1?", "Which are fruits?"}
    assert "Date of birth" in result["missing"] and "Date of birth" not in asked[0]  # never invented by the model
    assert done["confirmed"] and "/thanks.html?" in done["url"] and "q1=2" in done["url"]
    assert "submitted" in done["message"]


@needs_browser
def test_saved_sign_in_reaches_the_page_and_names_are_filled(site):
    cookie = {"name": "session", "value": "ok", "domain": "127.0.0.1", "path": "/", "expires": -1,
              "httpOnly": False, "secure": False, "sameSite": "Lax"}
    account = {"full_name": "Asha Rao", "email": "asha@example.com"}

    async def flow():
        bare = await forms.fill("u4", f"{site}/members.html", account)  # not signed in: nothing to answer
        await forms.cancel("u4")
        signed = await forms.fill("u4", f"{site}/members.html", account, signed_in={"cookies": [cookie], "origins": []})
        await forms.cancel("u4")
        return bare, signed

    bare, signed = asyncio.run(flow())
    assert bare["questions"] == 0
    assert {f["key"] for f in signed["filled"]} == {"full_name", "email"} and signed["anonymous"]


@needs_browser
def test_sign_in_window_returns_the_cookies_it_got(site, monkeypatch):
    real = forms._browser
    monkeypatch.setattr(forms, "_browser", lambda headless=True: real(True))  # no window pops up in tests

    async def flow():
        return await forms.sign_in(f"{site}/members.html", wait_s=1)

    state = asyncio.run(flow())
    assert state["cookies"] == [] and "origins" in state  # the user never logged in, so nothing was saved


@needs_browser
def test_paged_form_answers_each_section_and_keeps_ticked_boxes(site):
    async def model(messages, schema=None):
        text = messages[-1]["content"]
        return '{"0.0": "Blue"}' if "Favourite" in text else '{"1.0": ["Car", "Bike"]}'

    async def flow():
        result = await forms.fill("u5", f"{site}/paged.html", {}, model, "fill it")
        done = await forms.submit("u5")
        return result, done

    result, done = asyncio.run(flow())
    assert [f["field"] for f in result["filled"]] == ["Favourite colour?", "Which do you own?"]
    assert "c=blue" in done["url"] and "o=car" in done["url"] and "o=bike" in done["url"]  # Car stayed ticked


@needs_browser
def test_submit_works_without_a_submit_named_button(site):
    async def flow():
        for page in ("enter.html", "apply.html"):  # sent with Enter only; an input button with an unusual label
            await forms.fill("u6", f"{site}/{page}", PROFILE)
            done = await forms.submit("u6")
            assert "/done.html?" in done["url"] and "who=Asha+Rao" in done["url"], page

    asyncio.run(flow())
