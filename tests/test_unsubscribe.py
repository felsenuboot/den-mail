"""List-Unsubscribe parsing and the one-click request."""

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from den_mail.jmap.client import JMAPClient
from den_mail.jmap.types import EMAIL_BODY_PROPERTIES
from den_mail.unsubscribe import UnsubscribeError, one_click_request, parse_list_unsubscribe

from .fake_server import FakeJMAPServer


@pytest.fixture
def server():
    srv = FakeJMAPServer().start()
    yield srv
    srv.stop()


def test_parse_prefers_one_click_then_mailto_then_page():
    both = "<mailto:leave@list.example?subject=unsubscribe>, <https://list.example/u/abc>"
    plan = parse_list_unsubscribe(both, "List-Unsubscribe=One-Click")
    assert plan.kind == "one-click" and plan.url == "https://list.example/u/abc" and plan.target == "list.example"
    nxt = plan.fallback()
    assert nxt.kind == "mailto" and nxt.to == "leave@list.example" and nxt.target == "leave@list.example"
    assert nxt.fallback().kind == "browser" and nxt.fallback().url == "https://list.example/u/abc"
    assert nxt.fallback().fallback() is None
    # without the -Post header a GET on the page is the last resort, the mailto message comes first
    plan = parse_list_unsubscribe(both, None)
    assert plan.kind == "mailto" and plan.fallback().kind == "browser"
    plan = parse_list_unsubscribe("<https://list.example/u/abc>", None)
    assert plan.kind == "browser" and plan.url == "https://list.example/u/abc" and plan.fallback() is None
    plan = parse_list_unsubscribe("<mailto:leave@list.example?subject=unsubscribe%20me&body=please>", None)
    assert plan.kind == "mailto" and plan.to == "leave@list.example" and plan.subject == "unsubscribe me"
    assert plan.body == "please" and plan.fallback() is None
    # one-click needs https; plain http on the internet is only a page
    plan = parse_list_unsubscribe("<http://list.example/u/abc>", "List-Unsubscribe=One-Click")
    assert plan.kind == "browser"
    assert parse_list_unsubscribe("<http://127.0.0.1:1/u>", "List-Unsubscribe=One-Click").kind == "one-click"
    assert parse_list_unsubscribe("https://bare.example/u", None).kind == "browser"
    assert parse_list_unsubscribe(" <https://click.example/x?a=1>", None).kind == "browser"
    assert parse_list_unsubscribe(None, None) is None
    assert parse_list_unsubscribe("<mailto:>", None) is None
    assert parse_list_unsubscribe("", "List-Unsubscribe=One-Click") is None


def test_one_click_posts_the_rfc8058_body(server):
    plan = parse_list_unsubscribe(f"<{server.data.base_url}unsubscribe/arch-news>", "List-Unsubscribe=One-Click")
    one_click_request(plan.url)
    assert server.unsubscribes == [("/unsubscribe/arch-news", "List-Unsubscribe=One-Click",
                                    "application/x-www-form-urlencoded")]
    with pytest.raises(UnsubscribeError):
        one_click_request(f"{server.data.base_url}nope")  # 404 from the fake server


def test_newsletter_headers_reach_the_client(server):
    client = JMAPClient(server.token, server.session_url)
    client.fetch_session()
    nid = next(i for i, e in server.data.emails.items() if e["subject"].startswith("Arch Linux news"))
    res = client.call("Email/get", {"accountId": client.session.account_id, "ids": [nid],
                                    "properties": EMAIL_BODY_PROPERTIES})
    e = res["list"][0]
    plan = parse_list_unsubscribe(e["header:List-Unsubscribe:asRaw"], e["header:List-Unsubscribe-Post:asRaw"])
    assert plan.kind == "one-click" and plan.url == f"{server.data.base_url}unsubscribe/arch-news"


def test_fetch_email_headers_updates_a_stale_cached_body(server, tmp_path, monkeypatch):
    from den_mail.config import Config
    from den_mail.store.db import Database
    from den_mail.store.sync import SyncEngine

    from .test_engine import pump

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    client = JMAPClient(server.token, server.session_url)
    client.fetch_session()
    db = Database(tmp_path / "t.sqlite3")
    engine = SyncEngine(client, db, Config())
    nid = next(i for i, e in server.data.emails.items() if e["subject"].startswith("Arch Linux news"))
    # a body cached by an older version: List-Unsubscribe known, -Post never fetched
    db.set_email_body({"id": nid, "header:List-Unsubscribe:asRaw": "<https://stale.example/u>"})
    got: list[dict] = []
    engine.start()
    try:
        engine.fetch_email_headers(nid, ["header:List-Unsubscribe:asRaw", "header:List-Unsubscribe-Post:asRaw"],
                                   got.append, lambda m: got.append({"error": m}))
        pump(lambda: got)
    finally:
        engine.stop()
    assert got[0]["header:List-Unsubscribe-Post:asRaw"] == "List-Unsubscribe=One-Click"
    assert db.get_email_body(nid)["header:List-Unsubscribe-Post:asRaw"] == "List-Unsubscribe=One-Click"
