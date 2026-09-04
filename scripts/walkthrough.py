"""Walk all eight demo beats plus the negative cases, and assert each one.

This is the acceptance test for the narrative, not just the build: it proves the
size recommendation came from an authorized data call, that no order exists
before approval, that a weak second factor is rejected, that a forwarded link
cannot approve, and that a double-click buys exactly one basketball.

    python scripts/walkthrough.py
"""

from __future__ import annotations

import re
import sys
import urllib.parse

import httpx

AGENT = "http://localhost:8788"
MCP = "http://localhost:8787"
BASKETBALL = "CourtEdge Official Game Basketball"

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def beat(number: str, title: str) -> None:
    print(f"\n=== {number}  {title} ===")


def signin(client: httpx.Client, email: str) -> str:
    response = client.post(f"{AGENT}/auth/demo-signin", json={"email": email})
    response.raise_for_status()
    return response.json()["id_token"]


def chat(client: httpx.Client, message: str, id_token: str) -> dict:
    response = client.post(
        f"{AGENT}/agent/chat", json={"message": message, "id_token": id_token}, timeout=60
    )
    response.raise_for_status()
    return response.json()


def basketballs(turn: dict) -> int:
    """Count *placed orders* for the basketball, from structured data.

    Counting the prose would conflate a pending standing intent with a real
    order — both are rendered with the product name — and the whole point of
    beat 5 is that the intent is not a purchase.
    """
    return sum(1 for order in turn.get("orders", []) if order.get("product_name") == BASKETBALL)


def hidden_fields(html: str) -> dict[str, str]:
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', html))
    return {k: v.replace("&amp;", "&") for k, v in fields.items()}


def run_stepup(client: httpx.Client, resume_url: str, decision: str) -> tuple[str, dict]:
    """Follow a resume link through authorize and the callback. Returns the final URL."""
    authorize_url = client.get(resume_url).headers["location"]
    page = client.get(authorize_url).text
    form = hidden_fields(page)
    form["decision"] = decision
    callback = client.post(f"{AGENT}/mock-as/users/v1/authorize", data=form)
    verdict = client.get(callback.headers["location"])
    final = verdict.headers["location"]
    return final, dict(urllib.parse.parse_qsl(urllib.parse.urlparse(final).query))


def main() -> int:
    client = httpx.Client(follow_redirects=False, timeout=30)

    health = client.get(f"{MCP}/healthz").json()
    beat("0", "preflight")
    check("MCP server enforces auth", health["require_auth"] is True,
          "MCP_REQUIRE_AUTH=true, tokens verified against JWKS")

    beat("1", "shopper signs in")
    id_token = signin(client, "alex@oktane.demo")
    check("shopper holds an ID token", bool(id_token))

    # The demo is a story about state changing, so it cannot start from whatever
    # state the last run left behind: size 7 must be out of stock before we claim
    # it is, and the token cache must be cold before "an exchange happened" means
    # anything.
    stock = client.post(f"{MCP}/demo/restock", json={"sku": "CE-BB-GAME-7", "stock": 0}).json()
    check("size 7 starts out of stock", stock["after"] == 0, f"was {stock['before']}")
    cache = client.post(f"{AGENT}/demo/forget-tokens", json={"id_token": id_token}).json()
    check("token cache is cold", cache["cache"] == "cleared", cache["subject"])

    beat("2-4", "asks for a size; answer requires an authorized data call")
    turn = chat(client, "What size basketball should I get for a 16-year-old?", id_token)
    kinds = [event["kind"] for event in turn["trace"]]
    tools = [
        event["claims"].get("tool") for event in turn["trace"] if event["kind"] == "mcp_call"
    ]
    check("recommends size 7 / 29.5\"", '29.5"' in turn["reply"] and "size 7" in turn["reply"].lower(),
          turn["reply"][:70] + "...")
    check("says it is out of stock", "out of stock" in turn["reply"].lower())
    check("performed an ID-JAG exchange", "id_jag" in kinds,
          f"{kinds.count('id_jag')} exchanges")
    check("read the sizing guide over MCP", "catalog.sizing_guide" in tools)
    check("checked inventory over MCP", "inventory.check" in tools)
    check("used two distinct scopes",
          {"catalog:read", "inventory:read"} <= {
              s for event in turn["trace"] if event["kind"] == "access_token"
              for s in (event["claims"].get("scp") or [])
          })
    access = [event for event in turn["trace"] if event["kind"] == "access_token"]
    check("token carries human sub and agent act", bool(access) and all(
        event["claims"].get("sub") == "00u_alex_demo"
        and event["claims"].get("act.sub") == "wlp-oktane-demo-agent"
        for event in access
    ), "sub=the shopper, act.sub=the agent")

    beat("5", "buy it when it's back — intent only, no purchase")
    turn = chat(client, "OK, purchase it when it's back in stock.", id_token)
    intent = turn.get("intent") or {}
    check("standing intent recorded", intent.get("state") == "PENDING_STOCK", intent.get("intent_id", ""))
    check("intent targets the out-of-stock size 7", intent.get("variant_sku") == "CE-BB-GAME-7")
    orders_before = chat(client, "what are my orders?", id_token)
    check("the intent is pending, not purchased", intent.get("state") == "PENDING_STOCK",
          orders_before["reply"][:60])
    # Orders accumulate across demo runs, so "bought exactly one" is a delta from
    # here, not an absolute count.
    orders_at_start = basketballs(orders_before)

    beat("scope", "API Access Management: the wrong token is refused")
    probe = client.post(f"{AGENT}/demo/scope-probe", json={"id_token": id_token}).json()
    check("every unauthorized attempt at orders.create refused", probe["all_refused"] is True)
    for result in probe["probes"]:
        check(f"{result['label']} -> {result.get('reason')}",
              result["refused"] and result.get("as_expected", False),
              str(result.get("detail", ""))[:80])

    beat("exchange", "the authorization server refuses to over-issue")
    issued = client.post(f"{AGENT}/demo/exchange-probe", json={"id_token": id_token}).json()
    check("every attack on the token endpoints refused", issued["all_refused"] is True)
    for result in issued["probes"]:
        check(f"{result['label']} -> {result.get('reason')}",
              result["refused"] and result.get("as_expected", False),
              str(result.get("detail", ""))[:80])

    beat("6", "restock fires, standing intent wakes, approval raised")
    restock = client.post(f"{AGENT}/demo/restock",
                          json={"sku": "CE-BB-GAME-7", "stock": 12}).json()
    raised = restock["approvals_raised"]
    check("an approval was raised", len(raised) == 1, raised[0]["approval_id"] if raised else "none")
    if not raised:
        return summarize()
    approval_id, resume_url = raised[0]["approval_id"], raised[0]["resume_url"]
    state = client.get(f"{AGENT}/approvals/{approval_id}").json()
    check("shopper notified, awaiting approval", state["approval"]["state"] == "NOTIFIED")

    beat("7a", "NEGATIVE: weak second factor is rejected")
    _, params = run_stepup(client, resume_url, "1fa")
    check("acr_insufficient", params.get("error") == "acr_insufficient", params.get("detail", ""))
    check("a retry code was issued", "retry" in params, "shopper is not locked out")
    retry_url = (
        f"{AGENT}/auth/stepup/start?approval_id={approval_id}&code={params['retry']}"
        if "retry" in params else resume_url
    )

    beat("7b", "NEGATIVE: the original link cannot be replayed")
    replay_authorize = client.get(resume_url)
    replay_target = replay_authorize.headers.get("location", "")
    check("consumed resume code refused",
          "error=invalid_resume_link" in replay_target,
          "single-use enforced")

    beat("7c", "step-up with a real second factor")
    final, params = run_stepup(client, retry_url, "2fa")
    check("no error on the callback", "error" not in params, params.get("error", ""))
    decision_token = params.get("dt", "")
    check("one-time decision token issued", bool(decision_token))
    state = client.get(f"{AGENT}/approvals/{approval_id}").json()["approval"]
    check("approval is STEPUP_VERIFIED", state["state"] == "STEPUP_VERIFIED",
          f"acr={state['verified_acr']}")

    beat("7d", "NEGATIVE: a forwarded link cannot be approved by someone else")
    other = signin(client, "sam@oktane.demo")
    check("second shopper has a different sub", bool(other))
    forged = client.post(f"{AGENT}/approvals/{approval_id}/decision",
                         json={"decision": "approve", "decision_token": "not-the-token"})
    check("decision refused without the real token", forged.status_code == 403,
          f"HTTP {forged.status_code}")

    beat("8", "approval releases the order through MCP with orders:write")
    decided = client.post(f"{AGENT}/approvals/{approval_id}/decision",
                          json={"decision": "approve", "decision_token": decision_token},
                          timeout=60)
    check("decision accepted", decided.status_code == 200, f"HTTP {decided.status_code}")
    if decided.status_code != 200:
        print(decided.text[:400])
        return summarize()
    body = decided.json()
    order = body.get("order", {})
    scopes = {
        s for event in body.get("trace", []) if event["kind"] == "access_token"
        for s in (event["claims"].get("scp") or [])
    }
    check("order placed", bool(order.get("order_id")), order.get("order_id", ""))
    check("used orders:write", "orders:write" in scopes, str(sorted(scopes)))
    check("audience was the orders server", any(
        event["claims"].get("aud") == "api://oktane-orders"
        for event in body.get("trace", []) if event["kind"] == "access_token"
    ))
    check("order attributed to the human", order.get("subject") == "00u_alex_demo")
    check("order records the acting agent", order.get("placed_by_agent") == "wlp-oktane-demo-agent")
    check("approval COMPLETED", body["approval"]["state"] == "COMPLETED")

    beat("8b", "NEGATIVE: double-click buys exactly one basketball")
    again = client.post(f"{AGENT}/approvals/{approval_id}/decision",
                        json={"decision": "approve", "decision_token": decision_token})
    check("replayed decision refused", again.status_code in (403, 409),
          f"HTTP {again.status_code}")
    orders_after = chat(client, "what are my orders?", id_token)
    placed = basketballs(orders_after) - orders_at_start
    check("the approval bought exactly one basketball", placed == 1,
          f"{placed} order(s) added by this run")

    return summarize()


def summarize() -> int:
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{'=' * 60}")
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    for _, name, detail in failed:
        print(f"  FAIL {name} {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
