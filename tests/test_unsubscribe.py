"""List-Unsubscribe parsing and the one-click request."""

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from fastmail_gtk.jmap.client import JMAPClient  # noqa: E402
from fastmail_gtk.jmap.types import EMAIL_BODY_PROPERTIES  # noqa: E402
from fastmail_gtk.unsubscribe import UnsubscribeError, one_click_request, parse_list_unsubscribe  # noqa: E402

from .fake_server import FakeJMAPServer  # noqa: E402


@pytest.fixture
def server():
    srv = FakeJMAPServer().start()
    yield srv
    srv.stop()


def test_parse_prefers_one_click_then_page_then_mailto():
    both = "<mailto:leave@list.example?subject=unsubscribe>, <https://list.example/u/abc>"
    plan = parse_list_unsubscribe(both, "List-Unsubscribe=One-Click")
    assert plan.kind == "one-click" and plan.url == "https://list.example/u/abc" and plan.target == "list.example"
    plan = parse_list_unsubscribe(both, None)
    assert plan.kind == "browser" and plan.url == "https://list.example/u/abc"
    plan = parse_list_unsubscribe("<mailto:leave@list.example?subject=unsubscribe%20me&body=please>", None)
    assert plan.kind == "mailto" and plan.to == "leave@list.example" and plan.subject == "unsubscribe me"
    assert plan.body == "please" and plan.target == "leave@list.example"
    # one-click needs https; plain http on the internet falls back to the browser
    plan = parse_list_unsubscribe("<http://list.example/u/abc>", "List-Unsubscribe=One-Click")
    assert plan.kind == "browser"
    assert parse_list_unsubscribe("<http://127.0.0.1:1/u>", "List-Unsubscribe=One-Click").kind == "one-click"
    assert parse_list_unsubscribe("https://bare.example/u", None).kind == "browser"
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
