import re
from pathlib import Path

from app.main import app


def test_every_api_path_is_forwarded_by_the_web_app():
    # The web app proxies (dev) and Vercel rewrites (prod) only the paths listed in vite.config.ts;
    # a route missing there gets the app's index.html back instead of JSON.
    config = (Path(__file__).parents[2] / "web" / "vite.config.ts").read_text()
    forwarded = set(re.findall(r"'(/[a-z]+)'", config.split("export const paths", 1)[1].split("]", 1)[0]))
    served = {"/" + p.split("/")[1] for p in app.openapi()["paths"]} - {"/healthz"}  # health is checked on the API host
    assert served - forwarded == set()
