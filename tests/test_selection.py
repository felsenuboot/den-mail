"""The selection page's unsubscribe lookup (#151): a plan from cached bodies only."""

from den_mail.newsletters import HEADER_POST, HEADER_UNSUBSCRIBE, cached_plan


class FakeDb:
    def __init__(self, rows: dict, bodies: dict):
        self.rows, self.bodies = rows, bodies

    def get_email(self, email_id):
        return self.rows.get(email_id)

    def get_email_body(self, email_id):
        return self.bodies.get(email_id)


HEADER = "<https://news.example/unsub?u=7>, <mailto:leave@news.example>"


def test_first_cached_body_with_a_header_wins():
    db = FakeDb({"a": {"id": "a", "from": [{"email": "n@news.example"}]}, "b": {"id": "b"}},
                {"a": {"htmlBody": []},                       # cached without the header keys: skipped
                 "b": {HEADER_UNSUBSCRIBE: HEADER, HEADER_POST: "List-Unsubscribe=One-Click"}})
    found = cached_plan(db, ["a", "b", "c"])
    assert found is not None
    email, plan = found
    assert email["id"] == "b" and email[HEADER_UNSUBSCRIBE] == HEADER
    assert plan.kind == "one-click"


def test_no_plan_without_a_cached_header():
    db = FakeDb({"a": {"id": "a"}}, {"a": {HEADER_UNSUBSCRIBE: None, HEADER_POST: None}})
    assert cached_plan(db, ["a", "missing"]) is None
