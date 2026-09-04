"""GObject models for the mailbox tree (folders and labels are both mailboxes)."""

from __future__ import annotations

import zlib

from gi.repository import Gio, GObject

from ..jmap.types import ROLE_ICONS, ROLE_ORDER

LABEL_PALETTE_SIZE = 12


def label_color_index(key: str) -> int:
    """Stable palette index for a label (keyed by id so renames keep the colour)."""
    return zlib.crc32(key.encode("utf-8")) % LABEL_PALETTE_SIZE


class MailboxObject(GObject.Object):
    __gtype_name__ = "FmMailboxObject"

    id = GObject.Property(type=str, default="")
    name = GObject.Property(type=str, default="")
    role = GObject.Property(type=str, default="")
    parent_id = GObject.Property(type=str, default="")
    unread = GObject.Property(type=int, default=0)
    total = GObject.Property(type=int, default=0)
    depth = GObject.Property(type=int, default=0)
    icon_name = GObject.Property(type=str, default="folder-symbolic")
    is_section = GObject.Property(type=bool, default=False)
    color_index = GObject.Property(type=int, default=0)
    is_view = GObject.Property(type=bool, default=False)   # a local query (#19), not a JMAP mailbox

    def __init__(self, data: dict | None = None, depth: int = 0, section_title: str | None = None):
        super().__init__()
        self.data: dict = data or {}
        self.children = Gio.ListStore(item_type=MailboxObject)
        self.depth = depth
        self.held = 0             # unread messages the screener hides from this mailbox (#62)
        self._server_unread = 0
        if section_title is not None:
            self.is_section = True
            self.name = section_title
            self.id = f"section:{section_title}"
        elif data:
            self.update(data)

    @classmethod
    def for_view(cls, view) -> MailboxObject:
        """A sidebar row for a den_mail.views.View: no rights, so nothing can be
        dropped on or created in it, and a per-view default sort."""
        rights = {r: False for r in ("mayReadItems", "mayAddItems", "mayRemoveItems", "maySetSeen",
                                     "maySetKeywords", "mayCreateChild", "mayRename", "mayDelete", "maySubmit")}
        data: dict = {"id": view.id, "name": view.name, "myRights": rights}
        if view.default_sort == "size":
            data["sort"] = [{"property": "size", "isAscending": False}]
        elif view.default_sort == "oldest":
            data["sort"] = [{"property": "receivedAt", "isAscending": True}]
        obj = cls()
        obj.data = data
        obj.is_view = True
        obj.id = view.id
        obj.name = view.name
        obj.icon_name = view.icon
        return obj

    def update(self, data: dict, color_override: int | None = None) -> None:
        self.data = data
        role = data.get("role") or ""
        color = color_override if color_override is not None and color_override >= 0 else label_color_index(data["id"])
        self._server_unread = int(data.get("unreadEmails") or 0)
        for prop, value in (
            ("id", data["id"]),
            ("name", data.get("name") or ""),
            ("role", role),
            ("parent_id", data.get("parentId") or ""),
            ("unread", max(0, self._server_unread - self.held)),
            ("total", int(data.get("totalEmails") or 0)),
            ("icon_name", ROLE_ICONS.get(role or None, "folder-symbolic") if role else "fm-tag-symbolic"),
            ("color_index", color),
        ):
            if self.get_property(prop) != value:
                self.set_property(prop, value)

    def set_held(self, held: int) -> None:
        """The badge shows the server's unread count minus what the screener holds back (#62)."""
        self.held = max(0, int(held))
        unread = max(0, self._server_unread - self.held)
        if self.unread != unread:
            self.unread = unread

    @property
    def is_hidden(self) -> bool:
        return bool(self.data.get("hidden"))

    @property
    def starts_collapsed(self) -> bool:
        return bool(self.data.get("isCollapsed"))

    @property
    def is_system(self) -> bool:
        return bool(self.role)

    @property
    def rights(self) -> dict:
        return self.data.get("myRights") or {}

    def may(self, right: str) -> bool:
        return bool(self.rights.get(right, True))

    def __repr__(self) -> str:
        return f"<Mailbox {self.name!r} {self.id}>"


def _sort_key(m: dict) -> tuple:
    role = m.get("role")
    if role in ROLE_ORDER:
        return (0, ROLE_ORDER.index(role), "")
    return (1, m.get("sortOrder") or 0, (m.get("name") or "").lower())


class MailboxTree:
    """Keeps a Gio.ListStore tree in sync with the cached mailbox list.

    Object identity is preserved across updates so ListView selection and
    bindings survive count changes.
    """

    def __init__(self) -> None:
        self.root = Gio.ListStore(item_type=MailboxObject)
        self.by_id: dict[str, MailboxObject] = {}
        self.labels_section = MailboxObject(section_title="Labels")
        self.views_section = MailboxObject(section_title="Views")
        self.views: list[MailboxObject] = []   # local views (#19), shown between the folders and the labels
        self.show_views = False
        self._all: list[dict] = []
        self.color_overrides: dict[str, int] = {}  # mailbox id -> palette index chosen by the user

    def set_views(self, views: list[MailboxObject]) -> None:
        for obj in self.views:
            self.by_id.pop(obj.id, None)
        self.views = list(views)
        for obj in self.views:
            self.by_id[obj.id] = obj

    def update(self, mailboxes: list[dict]) -> None:
        self._all = mailboxes
        data_by_id = {m["id"]: m for m in mailboxes}
        for mid in list(self.by_id):
            if mid not in data_by_id and not self.by_id[mid].is_view:
                del self.by_id[mid]
        children: dict[str | None, list[dict]] = {}
        for m in mailboxes:
            if m.get("hidden"):  # Fastmail: "hide from the folder list"
                self._obj(m, 0)
                continue
            parent = m.get("parentId")
            if parent and parent not in data_by_id:
                parent = None
            children.setdefault(parent, []).append(m)
        for lst in children.values():
            lst.sort(key=_sort_key)
        top = children.get(None, [])
        system = [m for m in top if m.get("role")]
        labels = [m for m in top if not m.get("role")]
        desired_root: list[MailboxObject] = [self._obj(m, 0) for m in system]
        if self.show_views and self.views:
            desired_root.append(self.views_section)
            desired_root += self.views
        if labels:
            desired_root.append(self.labels_section)
        desired_root += [self._obj(m, 0) for m in labels]
        self._reconcile(self.root, desired_root)
        for m in mailboxes:
            if m.get("hidden"):
                continue
            # Children of hidden parents never enter the tree but still need objects for lookups.
            obj = self.by_id.get(m["id"]) or self._obj(m, 0)
            kids = [self._obj(c, obj.depth + 1) for c in children.get(m["id"], [])]
            self._reconcile(obj.children, kids)

    def _obj(self, data: dict, depth: int) -> MailboxObject:
        obj = self.by_id.get(data["id"])
        if obj is None:
            obj = MailboxObject(None, depth)
            self.by_id[data["id"]] = obj
        obj.update(data, self.color_overrides.get(data["id"]))
        if obj.depth != depth:
            obj.depth = depth
        return obj

    def refresh(self) -> None:
        """Re-apply the cached mailbox list (e.g. after a colour override changed)."""
        self.update(self._all)

    @staticmethod
    def _reconcile(store: Gio.ListStore, desired: list[MailboxObject]) -> None:
        current = [store.get_item(i) for i in range(store.get_n_items())]
        if [o.id for o in current] == [o.id for o in desired]:
            return
        # Remove vanished items one by one (keeps positions of the others stable), then insert.
        for i in range(len(current) - 1, -1, -1):
            if current[i] not in desired:
                store.remove(i)
        current = [store.get_item(i) for i in range(store.get_n_items())]
        if [o.id for o in current] == [o.id for o in desired]:
            return
        store.splice(0, store.get_n_items(), desired)

    # ----------------------------------------------------------- lookups

    def get(self, mailbox_id: str) -> MailboxObject | None:
        return self.by_id.get(mailbox_id)

    def by_role(self, role: str) -> MailboxObject | None:
        for obj in self.by_id.values():
            if obj.role == role:
                return obj
        return None

    def path_name(self, mailbox_id: str) -> str:
        parts = []
        cur = self.by_id.get(mailbox_id)
        while cur is not None:
            parts.append(cur.name)
            cur = self.by_id.get(cur.parent_id) if cur.parent_id else None
        return " / ".join(reversed(parts))

    def labels(self) -> list[MailboxObject]:
        """Non-system mailboxes in tree order (depth-first)."""
        out: list[MailboxObject] = []

        def walk(store: Gio.ListStore) -> None:
            for i in range(store.get_n_items()):
                obj = store.get_item(i)
                if obj.is_section or obj.is_view:
                    continue
                if not obj.is_system:
                    out.append(obj)
                walk(obj.children)

        walk(self.root)
        return out

    def all(self) -> list[MailboxObject]:
        """Every JMAP mailbox in tree order (views are not mailboxes: nothing moves into them)."""
        out: list[MailboxObject] = []

        def walk(store: Gio.ListStore) -> None:
            for i in range(store.get_n_items()):
                obj = store.get_item(i)
                if not obj.is_section and not obj.is_view:
                    out.append(obj)
                walk(obj.children)

        walk(self.root)
        return out
