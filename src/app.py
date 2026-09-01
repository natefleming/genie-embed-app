"""Genie Embed App.

A minimal, demonstration-only Databricks App that embeds two configurable Genie
rooms side-by-side via <iframe>. No database or SDK calls — it just serves one
HTML page built from environment configuration.

The iframe points at the Genie embed surface (/embed/genie/rooms/<id>), which is
allowed to be framed only on domains in the workspace's embedding approved-domains
list. Each panel also links out to the full Genie room.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse

app = FastAPI(title="Genie Embed App")


@dataclass(frozen=True)
class GenieRoom:
    """A single Genie room to embed."""

    title: str
    url: str  # full room UI, used for the "Open in Genie" link-out
    embed_url: str  # embed surface, used for the iframe


def _host() -> str:
    """Workspace host with any trailing slash stripped."""
    return os.environ.get("DATABRICKS_WORKSPACE_HOST", "").rstrip("/")


def _room(title_env: str, id_env: str, default_title: str) -> GenieRoom | None:
    """Build a GenieRoom from env vars, or None when its space id is unset."""
    space_id = os.environ.get(id_env, "").strip()
    if not space_id:
        return None
    title = os.environ.get(title_env, "").strip() or default_title
    workspace_id = os.environ.get("WORKSPACE_ID", "").strip()
    suffix = f"?o={workspace_id}" if workspace_id else ""
    url = f"{_host()}/genie/rooms/{space_id}{suffix}"
    embed_url = f"{_host()}/embed/genie/rooms/{space_id}{suffix}"
    return GenieRoom(title=title, url=url, embed_url=embed_url)


def _rooms() -> list[GenieRoom]:
    """Return the configured rooms, skipping any that are unset."""
    configured = [
        _room("GENIE_SPACE_1_TITLE", "GENIE_SPACE_1_ID", "Genie Room 1"),
        _room("GENIE_SPACE_2_TITLE", "GENIE_SPACE_2_ID", "Genie Room 2"),
    ]
    return [room for room in configured if room is not None]


def _panel(room: GenieRoom) -> str:
    """Render one titled iframe panel with a link-out to the full room."""
    title = html.escape(room.title)
    url = html.escape(room.url, quote=True)
    embed_url = html.escape(room.embed_url, quote=True)
    return f"""
      <section class="panel">
        <header class="panel-head">
          <h2>{title}</h2>
          <a class="open-link" href="{url}" target="_blank" rel="noopener">Open in Genie ↗</a>
        </header>
        <iframe src="{embed_url}" title="{title}" allow="clipboard-write"></iframe>
      </section>
    """


def _page() -> str:
    rooms = _rooms()
    if not rooms:
        return (
            "<h1>No Genie rooms configured</h1>"
            "<p>Set GENIE_SPACE_1_ID and/or GENIE_SPACE_2_ID.</p>"
        )
    panels = "\n".join(_panel(room) for room in rooms)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Genie Embed Demo</title>
  <style>
    :root {{ color-scheme: light dark; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f5f6f8;
      color: #1b1b1f;
    }}
    .top {{ background: #1b3a2f; color: #fff; padding: 16px 24px; }}
    .top h1 {{ margin: 0; font-size: 20px; }}
    .top p {{ margin: 4px 0 0; font-size: 13px; opacity: 0.85; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
      gap: 16px;
      padding: 16px;
      height: calc(100vh - 74px);
    }}
    .panel {{
      display: flex;
      flex-direction: column;
      background: #fff;
      border: 1px solid #e0e2e7;
      border-radius: 10px;
      overflow: hidden;
      min-height: 400px;
    }}
    .panel-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 14px;
      border-bottom: 1px solid #e0e2e7;
      background: #fafbfc;
    }}
    .panel-head h2 {{ margin: 0; font-size: 15px; }}
    .open-link {{ font-size: 13px; color: #1b6ef3; text-decoration: none; white-space: nowrap; }}
    .open-link:hover {{ text-decoration: underline; }}
    iframe {{ border: 0; width: 100%; flex: 1; display: block; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #16171a; color: #e6e6e6; }}
      .panel {{ background: #202226; border-color: #34363c; }}
      .panel-head {{ background: #26282d; border-color: #34363c; }}
    }}
  </style>
</head>
<body>
  <div class="top">
    <h1>Genie Analytics</h1>
    <p>Live Genie rooms embedded for demonstration.</p>
  </div>
  <main class="grid">
    {panels}
  </main>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _page()


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


if __name__ == "__main__":
    # Databricks Apps inject the port to bind via DATABRICKS_APP_PORT.
    port = int(os.environ.get("DATABRICKS_APP_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
