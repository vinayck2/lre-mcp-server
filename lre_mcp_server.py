"""
LoadRunner Enterprise (on-prem) MCP Server
-------------------------------------------
Exposes LRE REST API data (test runs, transaction results, run comparisons)
as MCP tools, for use with Claude Desktop, Cursor, or any MCP client.

Auth model:
  LRE's web UI may be SSO/SAML for human logins, but the REST API authenticates
  separately via a Client ID / Client Secret pair (generated per-user in LRE),
  which returns an LWSSO_COOKIE_KEY session cookie. This server manages that
  cookie session transparently -- including re-auth on expiry -- so MCP tool
  calls behave like simple stateless function calls to the client.

Configuration (environment variables):
  LRE_BASE_URL      e.g. https://lre.mycompany.com/LoadTest
  LRE_CLIENT_ID      Client ID generated in LRE for the service account
  LRE_CLIENT_SECRET  Client Secret generated in LRE for the service account
  LRE_DOMAIN         Default LRE domain (e.g. DEFAULT)
  LRE_PROJECT        Default LRE project (e.g. Project1)
  LRE_VERIFY_SSL     "true"/"false" -- set false only for self-signed certs in test envs

Run:
  pip install -r requirements.txt
  python lre_mcp_server.py
"""

import os
import threading
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

LRE_BASE_URL = os.environ.get("LRE_BASE_URL", "").rstrip("/")
LRE_CLIENT_ID = os.environ.get("LRE_CLIENT_ID", "")
LRE_CLIENT_SECRET = os.environ.get("LRE_CLIENT_SECRET", "")
LRE_DOMAIN_DEFAULT = os.environ.get("LRE_DOMAIN", "")
LRE_PROJECT_DEFAULT = os.environ.get("LRE_PROJECT", "")
LRE_VERIFY_SSL = os.environ.get("LRE_VERIFY_SSL", "true").lower() != "false"

if not LRE_BASE_URL or not LRE_CLIENT_ID or not LRE_CLIENT_SECRET:
    raise RuntimeError(
        "Missing required env vars: LRE_BASE_URL, LRE_CLIENT_ID, LRE_CLIENT_SECRET"
    )


# --------------------------------------------------------------------------
# Session manager: handles cookie-based auth + transparent re-auth on 401
# --------------------------------------------------------------------------

class LRESession:
    """
    Wraps an httpx.Client and keeps it authenticated against LRE's
    cookie-session REST API. Thread-safe re-authentication so concurrent
    MCP tool calls don't race each other into double-login.
    """

    def __init__(self, base_url: str, client_id: str, client_secret: str, verify_ssl: bool = True):
        self.base_url = base_url
        self.client_id = client_id
        self.client_secret = client_secret
        self._client = httpx.Client(base_url=base_url, verify=verify_ssl, timeout=30.0)
        self._lock = threading.Lock()
        self._authenticated = False

    def _authenticate(self) -> None:
        """Authenticate via Client ID/Secret and store the LWSSO cookie on the client."""
        resp = self._client.post(
            "/rest/authentication-point/authenticate-client",
            json={"ClientID": self.client_id, "ClientSecret": self.client_secret},
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        # LRE returns the session via Set-Cookie (LWSSO_COOKIE_KEY); httpx's
        # client cookie jar picks this up automatically from the response,
        # and will resend it on every subsequent request made by self._client.
        self._authenticated = True

    def _ensure_auth(self) -> None:
        with self._lock:
            if not self._authenticated:
                self._authenticate()

    def request(self, method: str, path: str, retry: bool = True, **kwargs) -> httpx.Response:
        """
        Make an authenticated request. On a 401 (expired/invalid session),
        re-authenticate once and retry the call automatically.
        """
        self._ensure_auth()
        resp = self._client.request(method, path, **kwargs)

        if resp.status_code == 401 and retry:
            with self._lock:
                self._authenticated = False
            self._authenticate()
            resp = self._client.request(method, path, **kwargs)

        resp.raise_for_status()
        return resp

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.request("GET", path, **kwargs)


session = LRESession(LRE_BASE_URL, LRE_CLIENT_ID, LRE_CLIENT_SECRET, LRE_VERIFY_SSL)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _resolve_scope(domain: Optional[str], project: Optional[str]) -> tuple[str, str]:
    d = domain or LRE_DOMAIN_DEFAULT
    p = project or LRE_PROJECT_DEFAULT
    if not d or not p:
        raise ValueError(
            "domain/project not provided and no LRE_DOMAIN/LRE_PROJECT default is set"
        )
    return d, p


def _project_path(domain: str, project: str) -> str:
    return f"/rest/domains/{domain}/projects/{project}"


# --------------------------------------------------------------------------
# MCP server + tools
# --------------------------------------------------------------------------

mcp = FastMCP("loadrunner-enterprise")


@mcp.tool()
def list_tests(domain: Optional[str] = None, project: Optional[str] = None) -> list[dict[str, Any]]:
    """
    List performance tests available in an LRE domain/project.
    Falls back to LRE_DOMAIN / LRE_PROJECT env defaults if not specified.
    """
    d, p = _resolve_scope(domain, project)
    resp = session.get(f"{_project_path(d, p)}/tests")
    return resp.json().get("Tests", resp.json())


@mcp.tool()
def list_test_runs(
    test_id: int,
    domain: Optional[str] = None,
    project: Optional[str] = None,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """
    List recent runs for a given test ID, most recent first.
    """
    d, p = _resolve_scope(domain, project)
    resp = session.get(
        f"{_project_path(d, p)}/tests/{test_id}/runs",
        params={"page-size": max_results, "order-by": "-StartTime"},
    )
    return resp.json().get("Runs", resp.json())


@mcp.tool()
def get_run_results(
    run_id: int,
    domain: Optional[str] = None,
    project: Optional[str] = None,
) -> dict[str, Any]:
    """
    Get the transaction summary for a specific run: response time
    percentiles (avg/p90/p95/p99), throughput, pass/fail counts, and
    error rate per transaction.
    """
    d, p = _resolve_scope(domain, project)
    resp = session.get(f"{_project_path(d, p)}/Runs/{run_id}/TransactionSummary")
    return resp.json()


@mcp.tool()
def get_run_sla(
    run_id: int,
    domain: Optional[str] = None,
    project: Optional[str] = None,
) -> dict[str, Any]:
    """
    Get the SLA (Service Level Agreement) pass/fail status for a run,
    if SLAs were configured on the test.
    """
    d, p = _resolve_scope(domain, project)
    resp = session.get(f"{_project_path(d, p)}/Runs/{run_id}/SLA")
    return resp.json()


@mcp.tool()
def get_run_errors(
    run_id: int,
    domain: Optional[str] = None,
    project: Optional[str] = None,
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """
    Get the error log for a specific run (error message, count, first/last
    occurrence). Useful for root-causing failed or degraded runs.
    """
    d, p = _resolve_scope(domain, project)
    resp = session.get(
        f"{_project_path(d, p)}/Runs/{run_id}/Errors",
        params={"page-size": max_results},
    )
    return resp.json().get("Errors", resp.json())


@mcp.tool()
def compare_runs(
    run_id_a: int,
    run_id_b: int,
    domain: Optional[str] = None,
    project: Optional[str] = None,
) -> dict[str, Any]:
    """
    Compare two runs (e.g. baseline vs. candidate) by pulling each run's
    transaction summary and computing per-transaction deltas in average
    response time, p90, throughput, and error rate.

    LRE has no native "compare" endpoint, so this is computed client-side
    here, then handed back as structured data for the model to reason over.
    """
    a = get_run_results(run_id_a, domain, project)
    b = get_run_results(run_id_b, domain, project)

    tx_a = {t["TransactionName"]: t for t in a.get("Transactions", a if isinstance(a, list) else [])}
    tx_b = {t["TransactionName"]: t for t in b.get("Transactions", b if isinstance(b, list) else [])}

    comparison = []
    for name in sorted(set(tx_a) | set(tx_b)):
        ta, tb = tx_a.get(name), tx_b.get(name)
        if not ta or not tb:
            comparison.append({"transaction": name, "status": "missing_in_one_run"})
            continue

        def pct_delta(field: str) -> Optional[float]:
            va, vb = ta.get(field), tb.get(field)
            if va in (None, 0) or vb is None:
                return None
            return round(((vb - va) / va) * 100, 2)

        comparison.append(
            {
                "transaction": name,
                "avg_response_time_a": ta.get("Average"),
                "avg_response_time_b": tb.get("Average"),
                "avg_response_time_pct_delta": pct_delta("Average"),
                "p90_a": ta.get("Percentile90"),
                "p90_b": tb.get("Percentile90"),
                "p90_pct_delta": pct_delta("Percentile90"),
                "error_count_a": ta.get("FailCount", 0),
                "error_count_b": tb.get("FailCount", 0),
            }
        )

    return {"run_id_a": run_id_a, "run_id_b": run_id_b, "transactions": comparison}


if __name__ == "__main__":
    mcp.run()
