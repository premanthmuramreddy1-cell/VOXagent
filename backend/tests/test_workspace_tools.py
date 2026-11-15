import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from app import workspace as wsp
from app.db import SessionLocal, User, new_id
from app.docs import read_document, write_document
from app.skills import RunContext, discover
from app.skills import workspace_tools as tools


def context(answer: str = "") -> RunContext:
    with SessionLocal() as db:
        user = User(email=f"t{new_id()}@example.com", password_hash="x")
        db.add(user)
        db.commit()
    return RunContext(user.id, "s", "", [], AsyncMock(), AsyncMock(return_value=answer))


def run(tool, ctx, **args):
    return asyncio.run(tool.run(tool.input_schema(**args), ctx))


def base_of(ctx) -> Path:
    with SessionLocal() as db:
        return wsp.root(asyncio.run(wsp.current(db, ctx.user_id)))


def test_registry_exposes_every_workspace_tool():
    assert {"list_files", "read_file", "write_file", "edit_file", "multi_edit", "search_in_files",
            "delete_file", "apply_changes", "undo_change", "ask_project", "rename_symbol"} <= set(discover())


def test_stage_apply_rename_and_undo():
    ctx = context()
    staged = run(tools.WriteFile(), ctx, path="src/app.py", content="def getUser():\n    pass\n")
    assert "say apply" in staged.summary
    assert ctx.emit.await_args.args[0] == "changes.proposed"
    assert "+def getUser" in ctx.emit.await_args.kwargs["changes"][0]["diff"]
    assert not (base_of(ctx) / "src/app.py").exists()  # staged only
    run(tools.ApplyChanges(), ctx)
    app = base_of(ctx) / "src/app.py"
    assert app.exists()
    run(tools.RenameSymbol(), ctx, old="getUser", new="fetchUser")
    run(tools.ApplyChanges(), ctx)
    assert "fetchUser" in app.read_text()
    run(tools.UndoChange(), ctx)
    assert "getUser" in app.read_text() and "fetchUser" not in app.read_text()


def test_mass_delete_needs_the_critical_tool():
    ctx = context()
    for n in range(7):
        run(tools.WriteFile(), ctx, path=f"f{n}.txt", content="x")
    run(tools.ApplyChanges(), ctx)
    run(tools.DeleteFile(), ctx, paths=[f"f{n}.txt" for n in range(7)])
    refused = run(tools.ApplyChanges(), ctx)
    assert refused.data == {"needs": "apply_deletions"} and (base_of(ctx) / "f0.txt").exists()
    assert tools.ApplyDeletions.permission.value == "critical"
    run(tools.ApplyDeletions(), ctx)
    assert not (base_of(ctx) / "f0.txt").exists()


def test_ask_project_answers_from_real_files():
    ctx = context("Login is handled in auth.py:1.")
    (base_of(ctx) / "auth.py").write_text("def login(user):\n    return True\n")
    result = run(tools.ProjectQuestion(), ctx, question="where is login handled?")
    prompt = ctx.complete.await_args.args[0][1]["content"]
    assert "auth.py:1" in prompt and "def login" in prompt
    assert result.summary == "Login is handled in auth.py:1." and result.data["places"][0]["path"] == "auth.py"


def test_docx_round_trip_and_document_edit(tmp_path):
    path = tmp_path / "letter.docx"
    write_document(path, "Dear team,\nWe ship on <Friday> & celebrate.")
    assert read_document(path) == "Dear team,\nWe ship on <Friday> & celebrate."

    ctx = context("Hello team.\nShipping Friday.")
    write_document(base_of(ctx) / "memo.docx", "hey team we ship friday")
    result = run(tools.EditDocument(), ctx, path="memo.docx", action="grammar")
    assert read_document(base_of(ctx) / "memo.docx") == "Hello team.\nShipping Friday."
    assert result.data["commit"]


def test_generate_tests_stages_a_new_test_file():
    ctx = context("```python\ndef test_add():\n    assert add(1, 2) == 3\n```")
    (base_of(ctx) / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    change = run(tools.GenerateCode(), ctx, kind="tests", path="calc.py").data["changes"][0]
    assert change["path"] == "tests/test_calc.py" and "```" not in change["diff"]
