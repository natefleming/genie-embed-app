"""Genie Embed App.

A demonstration-only Databricks App showing two ways to surface the "Ask Genie"
chat experience inside a custom app, one clearly-labeled section each:

  1. Embedded Genie Space — the full Genie chat page framed via <iframe>
     (/embed/genie/rooms/<id>). The viewer queries Genie as themselves.
  2. Custom chat box (Genie Agent API) — a bare, self-styled chat UI backed by
     the GA Genie Agent API (/api/2.0/genie/agents/<id>/responses), streamed live
     over SSE through this app's own POST /api/ask proxy, with the answer and
     result tables rendered as markdown.

Section 1 is pure HTML (no auth). Section 2 adds the only server-side logic: it
reads the viewer's forwarded OAuth token (X-Forwarded-Access-Token, OBO) and
streams the Agent API response back to the browser, falling back to the app
service principal (via databricks-sdk) for local development.

The Agent API streams at item granularity: each server-sent event carries a
complete output item — a `reasoning` step, an `execute_sql` `function_call`, a
`function_call_output` result table, or the final assistant `message`. The
browser renders each item as it arrives and typewriter-reveals the final answer.
"""

from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)

app = FastAPI(title="Genie Embed App")


@dataclass(frozen=True)
class GenieSpace:
    """The single Genie space this app demonstrates.

    The same space id is both the iframe room id and the Agent API `agent_id`
    (Genie Agents were formerly known as Genie Spaces — the ids are synonymous).
    """

    space_id: str
    title: str
    url: str  # full room UI — used for the "Open in Genie" link-out
    embed_url: str  # embed surface — used for the section 1 iframe


def _embed_host() -> str:
    """Workspace host that serves the Genie embed surface (for iframe URLs)."""
    return os.environ.get("DATABRICKS_WORKSPACE_HOST", "").strip().rstrip("/")


def _api_host() -> str:
    """Host for REST API calls, always scheme-qualified.

    Databricks Apps inject DATABRICKS_HOST at runtime as a bare hostname (no
    scheme); fall back to the explicitly configured embed host for local
    development. Prepend https:// when the value has no scheme.
    """
    host = (os.environ.get("DATABRICKS_HOST", "").strip() or _embed_host()).rstrip("/")
    if host and not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    return host


def _space() -> GenieSpace | None:
    """Build the configured GenieSpace, or None when GENIE_SPACE_ID is unset."""
    space_id = os.environ.get("GENIE_SPACE_ID", "").strip()
    if not space_id:
        return None
    title = os.environ.get("GENIE_SPACE_TITLE", "").strip() or "Genie Space"
    workspace_id = os.environ.get("WORKSPACE_ID", "").strip()
    suffix = f"?o={workspace_id}" if workspace_id else ""
    host = _embed_host()
    return GenieSpace(
        space_id=space_id,
        title=title,
        url=f"{host}/genie/rooms/{space_id}{suffix}",
        embed_url=f"{host}/embed/genie/rooms/{space_id}{suffix}",
    )


def _auth_headers(request: Request) -> dict[str, str] | None:
    """Resolve a bearer Authorization header for the Genie Agent API call.

    Prefers the viewer's forwarded token (OBO) so Genie answers as the signed-in
    user; falls back to the app service principal via the Databricks SDK for local
    development. Returns None when no credential is available.
    """
    token = request.headers.get("X-Forwarded-Access-Token")
    if token:
        return {"Authorization": f"Bearer {token}"}
    try:
        # Lazy import so the iframe section still serves if the SDK is absent.
        from databricks.sdk.core import Config

        headers = Config().authenticate()
        authorization = headers.get("Authorization")
        if authorization:
            return {"Authorization": authorization}
    except Exception:  # noqa: BLE001 — degrade gracefully; report as 503 below.
        return None
    return None


# --- HTML rendering -------------------------------------------------------

STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: #f5f6f8;
  color: #1b1b1f;
}
.top { background: #1b3a2f; color: #fff; padding: 16px 24px; }
.top h1 { margin: 0; font-size: 20px; }
.top p { margin: 4px 0 0; font-size: 13px; opacity: 0.85; }
.stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  max-width: 1100px;
  margin: 0 auto;
}
.card {
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #e0e2e7;
  border-radius: 10px;
  overflow: hidden;
}
.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border-bottom: 1px solid #e0e2e7;
  background: #fafbfc;
}
.card-head h2 { margin: 0; font-size: 16px; }
.desc { margin: 4px 0 0; font-size: 13px; opacity: 0.8; max-width: 74ch; }
.desc code {
  background: rgba(0,0,0,0.06);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12px;
}
.open-link {
  font-size: 13px;
  color: #1b6ef3;
  text-decoration: none;
  white-space: nowrap;
  flex-shrink: 0;
}
.open-link:hover { text-decoration: underline; }
.genie-frame { border: 0; width: 100%; height: 640px; display: block; }
.chat { display: flex; flex-direction: column; padding: 14px 16px; gap: 12px; }
.chat-log {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 80px;
  max-height: 520px;
  overflow-y: auto;
}
.turn { display: flex; flex-direction: column; gap: 8px; }
.msg {
  padding: 10px 12px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.5;
  word-break: break-word;
}
.msg-user {
  align-self: flex-end;
  background: #1b6ef3;
  color: #fff;
  max-width: 80%;
  white-space: pre-wrap;
}
.msg-genie { align-self: flex-start; background: #eef0f3; color: #1b1b1f; max-width: 92%; }
.msg-genie.streaming { white-space: pre-wrap; }
.msg-error {
  background: #fdecec;
  color: #b3261e;
  border: 1px solid #f3b9b4;
  align-self: flex-start;
  max-width: 92%;
  white-space: pre-wrap;
}
/* rendered-markdown answer */
.md > *:first-child { margin-top: 0; }
.md > *:last-child { margin-bottom: 0; }
.md p { margin: 8px 0; }
.md h1, .md h2, .md h3, .md h4 { margin: 10px 0 6px; line-height: 1.25; }
.md h1 { font-size: 18px; } .md h2 { font-size: 16px; }
.md h3 { font-size: 15px; } .md h4 { font-size: 14px; }
.md code {
  background: rgba(0,0,0,0.07);
  padding: 1px 5px;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
}
.md a { color: #1b6ef3; }
.md table { border-collapse: collapse; margin: 8px 0; font-size: 13px; display: block; overflow-x: auto; }
.md th, .md td { border: 1px solid #d4d7dd; padding: 5px 10px; text-align: left; white-space: nowrap; }
.md th { background: rgba(0,0,0,0.05); font-weight: 600; }
/* agent step trace */
.steps {
  align-self: flex-start;
  max-width: 92%;
  border: 1px solid #e0e2e7;
  border-radius: 8px;
  background: #fafbfc;
  font-size: 13px;
}
.steps summary { cursor: pointer; padding: 7px 10px; color: #555; user-select: none; }
.steps-body { padding: 0 10px 10px; display: flex; flex-direction: column; gap: 8px; }
.step-reason { color: #6a6d75; font-style: italic; }
.step-title { font-weight: 600; font-size: 12px; margin-bottom: 4px; opacity: 0.9; }
pre.step-sql {
  margin: 0;
  background: #1b1f24;
  color: #e6e6e6;
  padding: 8px 10px;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 12px;
  line-height: 1.4;
}
.chat-status { font-size: 13px; opacity: 0.7; font-style: italic; }
.chat-form { display: flex; gap: 8px; }
.chat-input {
  flex: 1;
  padding: 10px 12px;
  border: 1px solid #cfd2d8;
  border-radius: 8px;
  font-size: 14px;
  background: #fff;
  color: inherit;
}
.chat-send {
  padding: 10px 18px;
  border: 0;
  border-radius: 8px;
  background: #1b3a2f;
  color: #fff;
  font-size: 14px;
  cursor: pointer;
}
.chat-send:disabled { opacity: 0.5; cursor: default; }
.raw { font-size: 12px; opacity: 0.85; }
.raw summary { cursor: pointer; }
.chat-raw {
  max-height: 220px;
  overflow: auto;
  background: rgba(0,0,0,0.04);
  padding: 8px;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 8px 0 0;
}
@media (prefers-color-scheme: dark) {
  body { background: #16171a; color: #e6e6e6; }
  .card { background: #202226; border-color: #34363c; }
  .card-head { background: #26282d; border-color: #34363c; }
  .msg-genie { background: #2b2e33; color: #e6e6e6; }
  .chat-input { background: #17181b; border-color: #3a3d43; }
  .desc code, .chat-raw, .md code { background: rgba(255,255,255,0.10); }
  .msg-error { background: #3a1f1e; color: #ffb4ad; border-color: #5c2b28; }
  .steps { background: #26282d; border-color: #34363c; }
  .steps summary { color: #b9bcc2; }
  .step-reason { color: #9a9da4; }
  .md th, .md td { border-color: #3a3d43; }
  .md th { background: rgba(255,255,255,0.06); }
}
"""

TOP_BANNER = """
  <div class="top">
    <h1>Genie Integration Demo</h1>
    <p>Two ways to surface "Ask Genie" inside a Databricks App.</p>
  </div>
"""

PLACEHOLDER = """
      <section class="card">
        <header class="card-head">
          <div>
            <h2>No Genie space configured</h2>
            <p class="desc">Set the <code>GENIE_SPACE_ID</code> (and optionally
            <code>GENIE_SPACE_TITLE</code>) bundle variable, then redeploy.</p>
          </div>
        </header>
      </section>
"""

# Vanilla JS for the section-2 chat box. A raw string so JS backslashes (regex,
# "\n") pass through verbatim; runtime config is injected via window.__APP_CONFIG__.
CHAT_JS = r"""
(function () {
  var cfg = window.__APP_CONFIG__ || {};
  var form = document.getElementById('chat-form');
  if (!form || !cfg.agentConfigured) return;
  var input = document.getElementById('chat-input');
  var sendBtn = document.getElementById('chat-send');
  var log = document.getElementById('chat-log');
  var statusEl = document.getElementById('chat-status');
  var rawEl = document.getElementById('chat-raw');

  function scrollDown() { log.scrollTop = log.scrollHeight; }
  function setStatus(t) {
    if (!t) { statusEl.hidden = true; statusEl.textContent = ''; return; }
    statusEl.hidden = false; statusEl.textContent = t;
  }
  function logRaw(name, data) { rawEl.textContent += (name ? '[' + name + '] ' : '') + data + '\n'; }

  function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function inlineMd(s) {
    // s must already be HTML-escaped.
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return s;
  }
  function splitRow(line) {
    return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|')
      .map(function (c) { return c.trim(); });
  }
  // Compact markdown -> HTML: paragraphs, headings, GitHub tables, bold, code, links.
  function renderMarkdown(md) {
    if (!md) return '';
    md = md.replace(/\\([\[\]()])/g, '$1'); // unescape \[ \] \( \)
    var lines = md.split('\n');
    var html = '';
    var para = [];
    function flushPara() {
      if (para.length) {
        html += '<p>' + para.map(function (l) { return inlineMd(escapeHtml(l)); }).join('<br>') + '</p>';
        para = [];
      }
    }
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      var sep = i + 1 < lines.length ? lines[i + 1] : '';
      if (/\|/.test(line) && /^\s*\|?[\s:|-]*---[\s:|-]*$/.test(sep)) {
        flushPara();
        var header = splitRow(line);
        i += 2;
        var rows = [];
        while (i < lines.length && /\|/.test(lines[i])) { rows.push(splitRow(lines[i])); i++; }
        html += '<table><thead><tr>' +
          header.map(function (c) { return '<th>' + inlineMd(escapeHtml(c)) + '</th>'; }).join('') +
          '</tr></thead><tbody>' +
          rows.map(function (r) {
            return '<tr>' + r.map(function (c) { return '<td>' + inlineMd(escapeHtml(c)) + '</td>'; }).join('') + '</tr>';
          }).join('') + '</tbody></table>';
        continue;
      }
      var h = /^(#{1,4})\s+(.*)$/.exec(line);
      if (h) { flushPara(); html += '<h' + h[1].length + '>' + inlineMd(escapeHtml(h[2])) + '</h' + h[1].length + '>'; i++; continue; }
      if (/^\s*$/.test(line)) { flushPara(); i++; continue; }
      para.push(line); i++;
    }
    flushPara();
    return html;
  }

  function newTurn(question) {
    var turn = document.createElement('div');
    turn.className = 'turn';
    var u = document.createElement('div');
    u.className = 'msg msg-user';
    u.textContent = question;
    turn.appendChild(u);
    var steps = document.createElement('details');
    steps.className = 'steps';
    steps.open = true;
    var sum = document.createElement('summary');
    sum.textContent = 'Agent steps';
    var body = document.createElement('div');
    body.className = 'steps-body';
    steps.appendChild(sum); steps.appendChild(body);
    turn.appendChild(steps);
    // Attach refs on the turn ELEMENT so ensureAnswer can appendChild to it.
    turn._steps = steps; turn._body = body; turn._answer = null;
    log.appendChild(turn);
    scrollDown();
    return turn;
  }
  function addStep(turn, innerHtml) {
    var s = document.createElement('div');
    s.className = 'step';
    s.innerHTML = innerHtml;
    turn._body.appendChild(s);
    scrollDown();
  }
  function ensureAnswer(turn) {
    if (!turn._answer) {
      turn._answer = document.createElement('div');
      turn._answer.className = 'msg msg-genie streaming';
      turn.appendChild(turn._answer);
    }
    return turn._answer;
  }
  function safeJson(s) { try { return JSON.parse(s); } catch (e) { return {}; } }

  // Typewriter reveal, then final markdown render.
  function typewrite(el, text) {
    var i = 0;
    var stepN = Math.max(3, Math.floor(text.length / 100));
    el.textContent = '';
    var timer = setInterval(function () {
      i += stepN;
      el.textContent = text.slice(0, i);
      scrollDown();
      if (i >= text.length) {
        clearInterval(timer);
        el.classList.remove('streaming');
        el.classList.add('md');
        el.innerHTML = renderMarkdown(text);
        scrollDown();
      }
    }, 12);
  }

  function messageText(item) {
    var content = (item && item.content) || [];
    return content.filter(function (c) { return c && c.type === 'output_text'; })
      .map(function (c) { return c.text || ''; }).join('\n').trim();
  }

  function handleItem(turn, item) {
    if (!item) return;
    if (item.type === 'reasoning') {
      var rt = (item.content && item.content[0] && item.content[0].text) || '';
      if (rt) { addStep(turn, '<span class="step-reason">💭 ' + escapeHtml(rt) + '</span>'); }
      setStatus('Reasoning…');
    } else if (item.type === 'function_call' && item.name === 'execute_sql') {
      var args = safeJson(item.arguments);
      var title = args.title || 'SQL query';
      var sql = (args.sql || '').trim();
      addStep(turn, '<div class="step-title">🛠️ ' + escapeHtml(title) + '</div>' +
        '<pre class="step-sql"><code>' + escapeHtml(sql) + '</code></pre>');
      setStatus('Running SQL…');
    } else if (item.type === 'function_call_output') {
      var out = item.output || '';
      if (/\|/.test(out)) { addStep(turn, '<div class="md">' + renderMarkdown(out) + '</div>'); }
      setStatus('Reading results…');
    } else if (item.type === 'message') {
      var text = messageText(item);
      if (text) { turn._steps.open = false; setStatus(''); typewrite(ensureAnswer(turn), text); return true; }
    }
    return false;
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    var question = (input.value || '').trim();
    if (!question) return;
    input.value = '';
    sendBtn.disabled = true; input.disabled = true;
    var turn = newTurn(question);
    var gotMessage = false;
    setStatus('Thinking…');
    try {
      var res = await fetch(cfg.askPath, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: question })
      });
      if (!res.ok || !res.body) {
        var msg = 'Request failed (' + res.status + ')';
        try { var j = await res.json(); if (j.error) msg = j.error; } catch (_) {}
        var err = document.createElement('div');
        err.className = 'msg msg-error'; err.textContent = msg;
        turn.appendChild(err); return;
      }
      function processBlock(block) {
        var eventName = '';
        var dataParts = [];
        var blockLines = block.split('\n');
        for (var k = 0; k < blockLines.length; k++) {
          var bl = blockLines[k];
          if (bl.indexOf('event:') === 0) eventName = bl.slice(6).trim();
          else if (bl.indexOf('data:') === 0) dataParts.push(bl.slice(5).trim());
        }
        if (!dataParts.length) return;
        var dataStr = dataParts.join('\n');
        logRaw(eventName, dataStr);
        var d = safeJson(dataStr);
        var type = eventName || d.type || '';
        if (type === 'error') {
          var e2 = document.createElement('div');
          e2.className = 'msg msg-error';
          e2.textContent = 'Error: ' + (d.message || dataStr);
          turn.appendChild(e2); setStatus('');
          return;
        }
        if (type === 'response.output_item.done' || type === 'response.output_item.added') {
          if (d.item && d.item.type === 'message') {
            if (!gotMessage && handleItem(turn, d.item)) gotMessage = true;
          } else if (type === 'response.output_item.done') {
            handleItem(turn, d.item);
          }
        } else if (type === 'response.completed') {
          if (!gotMessage) {
            var out = (d.response && d.response.output) || [];
            for (var m = 0; m < out.length; m++) {
              if (out[m].type === 'message' && handleItem(turn, out[m])) { gotMessage = true; break; }
            }
          }
          setStatus('');
        }
      }

      var reader = res.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';
      while (true) {
        var chunk = await reader.read();
        if (chunk.value) buffer += decoder.decode(chunk.value, { stream: true });
        var idx;
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          processBlock(buffer.slice(0, idx));
          buffer = buffer.slice(idx + 2);
        }
        if (chunk.done) break;
      }
      // Flush any trailing frame the stream closed without terminating.
      if (buffer.trim()) processBlock(buffer);
      if (!gotMessage) {
        ensureAnswer(turn).textContent = '(No answer text returned — see agent steps.)';
        setStatus('');
      }
    } catch (err) {
      var e3 = document.createElement('div');
      e3.className = 'msg msg-error';
      e3.textContent = 'Error: ' + (err && err.message ? err.message : String(err));
      turn.appendChild(e3);
    } finally {
      setStatus('');
      sendBtn.disabled = false; input.disabled = false;
      input.focus();
    }
  });
})();
"""


def _section_iframe(space: GenieSpace) -> str:
    """Render section 1 — the embedded Genie space iframe with a link-out."""
    title = html.escape(space.title)
    url = html.escape(space.url, quote=True)
    embed_url = html.escape(space.embed_url, quote=True)
    return f"""
      <section class="card">
        <header class="card-head">
          <div>
            <h2>1 · Embedded Genie Space</h2>
            <p class="desc">The full Genie chat experience framed via
            <code>&lt;iframe&gt;</code> pointed at the embed surface
            (<code>/embed/genie/rooms/&lt;id&gt;</code>). Requires the app domain on
            the workspace embedding allowlist. Space: {title}.</p>
          </div>
          <a class="open-link" href="{url}" target="_blank" rel="noopener">Open in Genie ↗</a>
        </header>
        <iframe class="genie-frame" src="{embed_url}" title="{title}" allow="clipboard-write"></iframe>
      </section>
    """


def _section_chat(space: GenieSpace) -> str:
    """Render section 2 — the custom chat box backed by the Genie Agent API."""
    title = html.escape(space.title)
    return f"""
      <section class="card">
        <header class="card-head">
          <div>
            <h2>2 · Custom chat box (Genie Agent API)</h2>
            <p class="desc">A fully custom chat UI backed by the GA Genie Agent API
            (<code>/api/2.0/genie/agents/&lt;id&gt;/responses</code>), streamed live
            over SSE through this app's own <code>/api/ask</code> proxy. Agent steps
            (reasoning + SQL + results) stream in as they run; the final answer is
            rendered as markdown. Queries run as the signed-in viewer.</p>
          </div>
        </header>
        <div class="chat">
          <div id="chat-log" class="chat-log" aria-live="polite"></div>
          <div id="chat-status" class="chat-status" hidden></div>
          <form id="chat-form" class="chat-form">
            <input id="chat-input" class="chat-input" type="text" autocomplete="off"
                   placeholder="Ask a question about {title}…" />
            <button id="chat-send" class="chat-send" type="submit">Ask</button>
          </form>
          <details class="raw">
            <summary>Raw agent events</summary>
            <pre id="chat-raw" class="chat-raw"></pre>
          </details>
        </div>
      </section>
    """


def _page() -> str:
    space = _space()
    body = PLACEHOLDER if space is None else _section_iframe(space) + _section_chat(space)
    config_json = json.dumps({"agentConfigured": space is not None, "askPath": "/api/ask"})
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        "<title>Genie Integration Demo</title>\n"
        "<style>" + STYLE + "</style>\n"
        "</head>\n<body>\n"
        + TOP_BANNER
        + '<main class="stack">\n'
        + body
        + "\n</main>\n"
        + "<script>window.__APP_CONFIG__ = " + config_json + ";</script>\n"
        + "<script>" + CHAT_JS + "</script>\n"
        + "</body>\n</html>"
    )


# --- Routes ---------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _page()


@app.post("/api/ask", response_model=None)
async def ask(request: Request) -> StreamingResponse | JSONResponse:
    """Proxy a question to the Genie Agent API and stream the SSE response.

    Runs as the signed-in viewer (OBO) when the forwarded token is present, else
    as the app service principal (local dev). Upstream SSE frames are forwarded
    verbatim so the browser parses the agent-mode event stream directly.
    """
    space = _space()
    if space is None:
        return JSONResponse(status_code=503, content={"error": "Genie space not configured"})

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    question = str(body.get("question") or "").strip()
    if not question:
        return JSONResponse(status_code=400, content={"error": "question is required"})

    auth = _auth_headers(request)
    if auth is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": (
                    "No credentials: missing X-Forwarded-Access-Token and no local "
                    "service-principal auth available."
                )
            },
        )

    url = f"{_api_host()}/api/2.0/genie/agents/{space.space_id}/responses"
    headers = {
        **auth,
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
    }
    payload = {
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": question}],
            }
        ],
        "stream": True,
        "enable_viz": True,
    }

    async def event_stream():
        timeout = httpx.Timeout(None, connect=30.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        raw = await resp.aread()
                        detail = raw.decode("utf-8", "replace")[:500]
                        err = json.dumps({"status": resp.status_code, "message": detail})
                        yield f"event: error\ndata: {err}\n\n".encode()
                        return
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        except Exception as exc:  # noqa: BLE001
            err = json.dumps({"status": 0, "message": str(exc)[:500]})
            yield f"event: error\ndata: {err}\n\n".encode()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


if __name__ == "__main__":
    # Databricks Apps inject the port to bind via DATABRICKS_APP_PORT.
    port = int(os.environ.get("DATABRICKS_APP_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
