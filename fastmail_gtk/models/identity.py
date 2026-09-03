"""GObject wrappers for identities (send-as aliases) and masked emails."""

from __future__ import annotations

from gi.repository import GObject


class IdentityObject(GObject.Object):
    __gtype_name__ = "FmIdentityObject"

    id = GObject.Property(type=str, default="")
    name = GObject.Property(type=str, default="")
    email = GObject.Property(type=str, default="")
    display = GObject.Property(type=str, default="")

    def __init__(self, data: dict):
        super().__init__()
        self.data = data
        self.id = data["id"]
        self.name = data.get("name") or ""
        self.email = data.get("email") or ""
        self.display = f"{self.name} <{self.email}>" if self.name else self.email

    @property
    def is_wildcard(self) -> bool:
        return self.email.startswith("*@")

    @property
    def domain(self) -> str:
        return self.email.split("@", 1)[1] if "@" in self.email else ""

    @property
    def text_signature(self) -> str:
        return self.data.get("textSignature") or ""

    def matches(self, address: str) -> bool:
        address = (address or "").lower()
        if self.is_wildcard:
            return address.endswith("@" + self.domain.lower())
        return address == self.email.lower()


class MaskedEmailObject(GObject.Object):
    __gtype_name__ = "FmMaskedEmailObject"

    id = GObject.Property(type=str, default="")
    email = GObject.Property(type=str, default="")
    state = GObject.Property(type=str, default="")
    description = GObject.Property(type=str, default="")
    for_domain = GObject.Property(type=str, default="")
    last_message_at = GObject.Property(type=str, default="")

    def __init__(self, data: dict):
        super().__init__()
        self.data = data
        self.update(data)

    def update(self, data: dict) -> None:
        self.data = data
        self.id = data["id"]
        self.email = data.get("email") or ""
        self.state = data.get("state") or ""
        self.description = data.get("description") or ""
        self.for_domain = data.get("forDomain") or ""
        self.last_message_at = data.get("lastMessageAt") or ""
