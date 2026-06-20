# LoadRunner Enterprise (on-prem) MCP Server

Wraps the LRE REST API as MCP tools, authenticating via Client ID/Secret
(works alongside SAML SSO, which only governs interactive browser logins).

## 1. Generate API credentials in LRE

In LRE, create or use a dedicated **service account** (not a personal login),
then generate a **Client ID / Client Secret** pair for it. This is the
programmatic auth path that bypasses the SAML redirect used by the web UI.
Give this account read access only to the domain/project(s) you need.

## 2. Install

```bash
pip install -r requirements.txt
```

## 3. Configure environment variables

```bash
export LRE_BASE_URL="https://lre.mycompany.com/LoadTest"
export LRE_CLIENT_ID="your-client-id"
export LRE_CLIENT_SECRET="your-client-secret"
export LRE_DOMAIN="DEFAULT"
export LRE_PROJECT="Project1"
export LRE_VERIFY_SSL="true"   # set "false" only for self-signed certs in test envs
```

## 4. Run standalone (for testing)

```bash
python lre_mcp_server.py
```

## 5. Wire into Claude Desktop / Cursor

Add to your MCP client config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "loadrunner-enterprise": {
      "command": "python",
      "args": ["/absolute/path/to/lre_mcp_server.py"],
      "env": {
        "LRE_BASE_URL": "https://lre.mycompany.com/LoadTest",
        "LRE_CLIENT_ID": "your-client-id",
        "LRE_CLIENT_SECRET": "your-client-secret",
        "LRE_DOMAIN": "DEFAULT",
        "LRE_PROJECT": "Project1"
      }
    }
  }
}
```

## Tools exposed

| Tool              | Purpose                                                         |
|--------------------|------------------------------------------------------------------|
| `list_tests`       | List performance tests in a domain/project                      |
| `list_test_runs`   | List recent runs for a test, most recent first                  |
| `get_run_results`  | Transaction summary: avg/p90/p95/p99, throughput, fail counts   |
| `get_run_sla`      | SLA pass/fail status for a run                                  |
| `get_run_errors`   | Error log for a run (message, count, first/last occurrence)     |
| `compare_runs`     | Per-transaction delta between two runs (baseline vs. candidate) |

## Notes / things to verify against your LRE version

- **Endpoint paths**: the REST paths used here (`/rest/domains/{domain}/projects/{project}/...`)
  match the general LRE REST API shape, but exact resource names (e.g.
  `TransactionSummary` vs. `RunResults`) can differ slightly between LRE
  versions. Check your instance's REST API reference
  (`<LRE_BASE_URL>/rest` or the OpenText ADM Help Center for your version)
  and adjust the paths in `lre_mcp_server.py` if a call 404s.
- **Proxy in front of LRE**: if a reverse proxy in front of LRE enforces
  Basic Authentication before requests reach LRE itself, this will block
  the Client ID/Secret flow entirely — that needs to be excluded for the
  `/rest/authentication-point/*` path.
- **Session cookie expiry**: the `LRESession` class re-authenticates
  automatically on a 401. If your LRE session timeout is very short and
  you're hitting frequent re-auth under heavy tool-call volume, consider
  proactively re-authenticating on a timer instead of reactively on 401.
- **Confirm Client ID/Secret auth is enabled** in LRE Site Administration —
  some on-prem installs ship with SSO configured for the UI but the
  client-credential API path turned off by default.
