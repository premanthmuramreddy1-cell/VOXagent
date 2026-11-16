from unittest.mock import AsyncMock, patch

import pytest

from app.skills import RunContext
from app.skills.system_control import Args, skill


@pytest.fixture
def mock_ctx():
    return RunContext(
        user_id="u1",
        session_id="s1",
        user_text="set volume to 40",
        upload_ids=[],
        emit=AsyncMock(),
        complete=AsyncMock(),
    )


@pytest.mark.anyio
async def test_volume_adjust(mock_ctx):
    with patch("app.skills.system_control._run_cmd", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (0, "", "")
        res = await skill.run(Args(action="volume", value="50"), mock_ctx)
        assert res.summary == "Output volume set to 50%."
        assert res.data["volume"] == 50
        mock_run.assert_called_with("osascript", "-e", "set volume output volume 50")


@pytest.mark.anyio
async def test_volume_mute_and_query(mock_ctx):
    with patch("app.skills.system_control._run_cmd", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (0, "65", "")
        res = await skill.run(Args(action="volume", value="mute"), mock_ctx)
        assert res.data["muted"] is True
        mock_run.assert_called_with("osascript", "-e", "set volume output muted true")

        res_query = await skill.run(Args(action="volume", value=""), mock_ctx)
        assert "65%" in res_query.summary
        assert res_query.data["volume"] == "65"


@pytest.mark.anyio
async def test_app_open_and_quit(mock_ctx):
    with patch("app.skills.system_control._run_cmd", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (0, "", "")
        res = await skill.run(Args(action="app_open", value="Spotify"), mock_ctx)
        assert "Opened application Spotify" in res.summary
        mock_run.assert_called_with("open", "-a", "Spotify")

        res_q = await skill.run(Args(action="app_quit", value="Spotify"), mock_ctx)
        assert "Closed application Spotify" in res_q.summary
        mock_run.assert_called_with("osascript", "-e", 'tell application "Spotify" to quit')


@pytest.mark.anyio
async def test_app_name_injection_prevention(mock_ctx):
    with pytest.raises(ValueError, match="invalid application name"):
        await skill.run(Args(action="app_open", value="App; rm -rf /"), mock_ctx)


@pytest.mark.anyio
async def test_open_url_safety(mock_ctx):
    with patch("app.skills.system_control._run_cmd", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (0, "", "")
        res = await skill.run(Args(action="open_url", value="https://example.com"), mock_ctx)
        assert "https://example.com" in res.summary
        mock_run.assert_called_with("open", "--", "https://example.com")

    with pytest.raises(ValueError, match="only valid http and https URLs"):
        await skill.run(Args(action="open_url", value="file:///etc/passwd"), mock_ctx)


@pytest.mark.anyio
async def test_media_falls_back_to_the_system_media_key(mock_ctx):
    with patch("app.skills.system_control._run_cmd", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (0, "system media key", "")
        res = await skill.run(Args(action="media", value="next"), mock_ctx)

    assert "key code 17" in mock_run.call_args.args[2]  # works when Spotify and Music are not running
    assert res.data["player"] == "system media key"


@pytest.mark.anyio
async def test_battery_status(mock_ctx):
    with patch("app.skills.system_control._run_cmd", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (0, "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=123)\t87%; discharging; 4:12 remaining", "")
        res = await skill.run(Args(action="battery"), mock_ctx)
        assert "87%" in res.summary
        assert res.data["percentage"] == "87"
