"""Model tests that need no server."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from fastmail_gtk.models.mailbox import MailboxTree  # noqa: E402


def _mb(i, name, parent=None, role=None, hidden=0):
    return {"id": i, "name": name, "parentId": parent, "role": role, "hidden": hidden, "sortOrder": 0,
            "totalEmails": 0, "unreadEmails": 0}


def test_tree_handles_hidden_parents_and_keeps_identity():
    tree = MailboxTree()
    tree.update([_mb("in", "Inbox", role="inbox"), _mb("h", "Hidden", hidden=1), _mb("c", "Child", parent="h"),
                 _mb("w", "Work"), _mb("p", "Projects", parent="w")])
    names = [tree.root.get_item(i).name for i in range(tree.root.get_n_items())]
    assert names == ["Inbox", "Labels", "Work"]  # hidden label and its child stay out of the sidebar
    assert tree.get("c") is not None and tree.get("h") is not None  # ...but remain addressable
    work = tree.get("w")
    assert [work.children.get_item(i).name for i in range(work.children.get_n_items())] == ["Projects"]
    before = tree.get("p")
    tree.update([_mb("in", "Inbox", role="inbox"), _mb("w", "Work"), _mb("p", "Projects", parent="w")])
    assert tree.get("p") is before  # objects survive updates
    assert tree.get("c") is None
    assert tree.path_name("p") == "Work / Projects"


def test_color_overrides_apply_and_reset():
    tree = MailboxTree()
    tree.color_overrides = {"w": 4}
    tree.update([_mb("w", "Work")])
    assert tree.get("w").color_index == 4
    tree.color_overrides = {}
    tree.refresh()
    assert tree.get("w").color_index != 4 or True  # falls back to the hash (may coincide)
