# Genie Embed App

A demonstration Databricks App that embeds two configurable **Genie rooms**
side-by-side via `<iframe>`, deployed as a Databricks Asset Bundle (DAB) using the
**`direct`** deployment engine.

The app is a small FastAPI page server — no database or SDK calls. It reads the
room configuration from environment variables (supplied by the bundle) and serves
one HTML page with two iframes, each with an "Open in Genie" link-out in its header.

Two things must both be true for the rooms to render inline: the iframe must use
the Genie **embed surface** (`/embed/genie/rooms/<id>`), and the app's domain must
be on the workspace's **embedding approved-domains** allowlist (Step 3).

---

## Prerequisites

| # | Requirement | How to check / get it |
|---|---|---|
| 1 | **Databricks CLI ≥ 1.3.0** | `databricks --version` |
| 2 | **A CLI profile for the target workspace** | `databricks auth describe -p <profile>` — must return the workspace host |
| 3 | **Workspace admin** on the target workspace | Needed once, to add the app domain to the embedding allowlist (Step 3). Non-admins can still deploy; the iframes just won't render inline until an admin does this. |
| 4 | **One or more Genie spaces** you can access, plus their **space IDs** | `databricks genie list-spaces -p <profile>` |
| 5 | **Viewers have access** to each Genie space and its underlying data | Embedded Genie authenticates the viewer; they see only rooms/data they're granted. Grant via the Genie space's **Share** dialog. |

List space IDs, e.g.:

```bash
databricks genie list-spaces -p <profile> \
  | python3 -c "import sys,json;[print(s['space_id'],'—',s['title']) for s in json.load(sys.stdin)['spaces']]"
```

---

## Project layout

```
genie-embed-app/
  databricks.yml        # bundle (engine: direct) + variables + app resource (command + env)
  src/
    app.py              # FastAPI page server — two Genie iframes + link-out
    requirements.txt    # fastapi, uvicorn[standard]
```

There is no `app.yaml` — the app's run command and environment are defined in the
bundle's app-resource `config` block, so all settings live in one file.

---

## Step 1 — Configure the embedded rooms (bundle variables)

Every room setting is a **bundle variable** in `databricks.yml`. Change the
defaults there, or override per deploy without editing any file:

```bash
databricks bundle deploy -t dev -p <profile> \
  --var="genie_space_1_id=<space-id>" \
  --var="genie_space_1_title=My Room" \
  --var="genie_space_2_id=<space-id>" \
  --var="genie_space_2_title=Another Room"
```

| Variable | Meaning |
|---|---|
| `workspace_host` | Workspace host serving the Genie rooms (no trailing slash) |
| `workspace_id` | Workspace (org) id appended as the `?o=` URL param |
| `genie_space_1_id` / `genie_space_1_title` | Left panel space id + heading |
| `genie_space_2_id` / `genie_space_2_title` | Right panel space id + heading |

Leave `genie_space_2_id` empty to render a single panel. The variables are wired
into the app-resource `config.env`, so a redeploy applies them — no code change.

---

## Step 2 — Deploy the app

From this directory, against your CLI profile:

```bash
databricks bundle validate --strict -t dev -p <profile>   # config sanity check
databricks bundle deploy   -t dev -p <profile>            # creates the app (direct engine)
databricks bundle run genie_embed -t dev -p <profile>     # starts the app, prints its URL
```

`bundle run` also restarts the app after a redeploy. Check status / logs any time:

```bash
databricks apps get  genie-embed-dev -p <profile>     # app_status / compute_status
databricks apps logs genie-embed-dev -p <profile>     # build + runtime logs
```

---

## Step 3 — Allow inline embedding (workspace admin, one-time)

The app runs on `*.databricksapps.com` while Genie lives on
`*.cloud.databricks.com` (a different origin, not covered by `*.databricks.com`).
Databricks only lets a Genie room be framed on domains in the workspace's
**embedding approved-domains allowlist**. The **same setting governs AI/BI
dashboard embedding and Genie Agent embedding.** Until the app's domain is
allowlisted, the browser blocks the frame (CSP `frame-ancestors`) and the panel is
blank — use the header "Open in Genie" link.

**3a. Check the current policy:**

```bash
databricks settings aibi-dashboard-embedding-access-policy    get -p <profile>
databricks settings aibi-dashboard-embedding-approved-domains get -p <profile>
```

The access policy must be `ALLOW_APPROVED_DOMAINS` (or `ALLOW_ALL_DOMAINS`), and
the app domain must be in the approved-domains list. **An empty list blocks
everything.**

**3b. Add the app domain — UI:** username → **Settings** → **Security** →
**External access** → **Embed dashboards** → **Manage** → add
`*.databricksapps.com` → **Save**.

**3b. Add the app domain — CLI** (fails unless the policy is already
`ALLOW_APPROVED_DOMAINS`):

```bash
ETAG=$(databricks settings aibi-dashboard-embedding-approved-domains get -p <profile> \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['etag'])")

databricks settings aibi-dashboard-embedding-approved-domains update -p <profile> --json "{
  \"allow_missing\": true,
  \"setting\": {
    \"aibi_dashboard_embedding_approved_domains\": {\"approved_domains\": [\"*.databricksapps.com\"]},
    \"etag\": \"$ETAG\",
    \"setting_name\": \"default\"
  },
  \"field_mask\": \"aibi_dashboard_embedding_approved_domains.approved_domains\"
}"
```

The `*.databricksapps.com` wildcard covers this app and every redeploy. If the
policy is `DENY_ALL_DOMAINS`, first switch it (the `etag` from a `get` is
required):

```bash
ETAG=$(databricks settings aibi-dashboard-embedding-access-policy get -p <profile> \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['etag'])")

databricks settings aibi-dashboard-embedding-access-policy update -p <profile> --json "{
  \"allow_missing\": true,
  \"setting\": {
    \"aibi_dashboard_embedding_access_policy\": {\"access_policy_type\": \"ALLOW_APPROVED_DOMAINS\"},
    \"etag\": \"$ETAG\",
    \"setting_name\": \"default\"
  },
  \"field_mask\": \"aibi_dashboard_embedding_access_policy.access_policy_type\"
}"
```

Reload the app after saving — the Genie rooms render inline. A room's **Share →
Embed space** dialog lists the current approved domains and shows the exact embed
URL, which is how the `/embed/genie/rooms/<id>` path below was confirmed.

---

## How it works (key code)

**`databricks.yml` — direct engine, variables, and env injection:**

```yaml
bundle:
  name: genie_embed
  engine: direct                    # deploy without Terraform
variables:
  genie_space_1_id: { default: "..." }
  # ...host, workspace_id, titles, space 2...
resources:
  apps:
    genie_embed:
      name: genie-embed-${bundle.target}
      source_code_path: ./src
      config:                       # replaces app.yaml
        command: ["python", "app.py"]
        env:
          - { name: GENIE_SPACE_1_ID, value: "${var.genie_space_1_id}" }
          # ...one entry per variable...
```

> Names are dot-free on purpose — CLI v1.3.0 panics on dotted Apps bundle names.
> The target's `workspace.host` must be a literal (it is resolved for auth before
> variables), so it is not a variable.

**Building each room URL** (`src/app.py`). The iframe uses the Genie **embed
surface** (`/embed/genie/rooms/<id>`) — the plain `/genie/rooms/<id>` URL redirects
inside the frame and is blocked. The "Open in Genie" link-out uses the plain URL:

```python
suffix = f"?o={workspace_id}" if workspace_id else ""
url       = f"{host}/genie/rooms/{space_id}{suffix}"        # link-out → full room UI
embed_url = f"{host}/embed/genie/rooms/{space_id}{suffix}"  # iframe → embed surface
```

**Binding the port** — Databricks Apps inject `DATABRICKS_APP_PORT`; never
hardcode 8080 or you get 502s:

```python
if __name__ == "__main__":
    port = int(os.environ.get("DATABRICKS_APP_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
```

**The iframe** — each panel header carries an "Open in Genie ↗" link, and the
iframe fills the panel. `allow="clipboard-write"` matches Databricks' generated
embed code so viewers can copy CSV / conversation links:

```html
<iframe src="{embed_url}" title="{title}" allow="clipboard-write"></iframe>
```
