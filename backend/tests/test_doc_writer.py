import asyncio
from unittest.mock import AsyncMock

from app.skills import RunContext, doc_writer
from app.skills.doc_writer import Args, _safe_filename, skill


class Proc:
    async def wait(self) -> int:
        return 0


def context(content: str) -> RunContext:
    return RunContext("u1", "s1", "write a report", [], AsyncMock(), AsyncMock(return_value=content))


def test_doc_writer_saves_the_document_and_opens_it(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_writer, "DOCS_ROOT", tmp_path)
    monkeypatch.setattr(doc_writer, "is_owner", lambda user_id: True)
    opened: list[tuple] = []

    async def fake_exec(*cmd):
        opened.append(cmd)
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(skill.run(Args(title="Q3 report", prompt="summarise the quarter"), context("# Report\nDone.")))

    path = tmp_path / "Q3_report.md"
    assert path.read_text() == "# Report\nDone.\n"
    assert result.data["path"] == str(path) and result.data["words"] == 3
    assert result.data["opened"] is True and opened == [("open", "--", str(path))]


def test_doc_writer_refuses_titles_that_escape_the_documents_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_writer, "DOCS_ROOT", tmp_path)
    path = _safe_filename("../../etc/passwd", "md", tmp_path)
    assert path.parent == tmp_path and path.name == "etc_passwd.md"
    (tmp_path / "notes.md").write_text("x")
    assert _safe_filename("notes", "md", tmp_path).name == "notes_1.md"  # never overwrites


def test_other_people_get_their_own_folder_and_nothing_opens_on_the_owners_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_writer, "DOCS_ROOT", tmp_path)
    monkeypatch.setattr(doc_writer, "is_owner", lambda user_id: False)
    opened: list[tuple] = []

    async def fake_exec(*cmd):
        opened.append(cmd)
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(skill.run(Args(title="plan", prompt="p"), context("x")))
    assert result.data["path"] == str(tmp_path / "people" / "u1" / "plan.md")
    assert result.data["opened"] is False and opened == []


def test_doc_writer_is_a_gated_change_skill():
    assert skill.permission.value == "change"
    assert skill.name == "doc_writer"


def test_form_sign_in_is_refused_for_anyone_but_the_owner(monkeypatch):
    from app.skills import form_tools

    monkeypatch.setattr(form_tools, "is_owner", lambda user_id: False)
    opened = AsyncMock()
    monkeypatch.setattr(form_tools.forms, "sign_in", opened)
    signin = next(s for s in form_tools.skills if s.name == "form_sign_in")
    result = asyncio.run(signin.run(signin.input_schema(), context("")))
    assert result.data == {"saved": False} and not opened.called
