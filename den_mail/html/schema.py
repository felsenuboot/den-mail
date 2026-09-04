"""schema.org data in HTML mail (#20): JSON-LD blocks and microdata, and one line that sums them up.

Shops, carriers, airlines and booking sites mark up their mail with
schema.org types (Order, ParcelDelivery, Invoice, FlightReservation, …) so
that clients can show "Order shipped · DHL 00340…" without reading the text.
This reads what is in a cached body; it never fetches anything.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser

JSON_LD_RE = re.compile(r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
                        re.IGNORECASE | re.DOTALL)
TRANSACTIONAL = {"Order", "ParcelDelivery", "Invoice", "FlightReservation", "LodgingReservation", "EventReservation",
                 "TrainReservation", "BusReservation", "RentalCarReservation", "FoodEstablishmentReservation",
                 "TaxiReservation", "Reservation"}


@dataclass(frozen=True)
class Summary:
    kind: str          # the schema.org type that produced it
    text: str          # "Order shipped · DHL · 00340434…"
    copy: str | None   # a tracking or reservation number worth a copy button


# ------------------------------------------------------------- extraction


def _type_of(obj: dict) -> str:
    t = obj.get("@type") or obj.get("type") or ""
    if isinstance(t, list):
        t = t[0] if t else ""
    t = str(t)
    return t.rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def _flatten(node) -> list[dict]:
    """Every object with a @type in a JSON-LD document, top level first."""
    out: list[dict] = []
    if isinstance(node, dict):
        if "@graph" in node and isinstance(node["@graph"], list):
            for n in node["@graph"]:
                out += _flatten(n)
        elif node.get("@type") or node.get("type"):
            out.append(node)
    elif isinstance(node, list):
        for n in node:
            out += _flatten(n)
    return out


class _Microdata(HTMLParser):
    """itemscope/itemtype/itemprop into nested dicts, enough for the types above."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._stack: list[tuple[dict, str | None, str]] = []   # (item, itemprop it fills in the parent, tag)
        self._text_target: tuple[dict, str] | None = None
        self._depth: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        prop = a.get("itemprop")
        if "itemscope" in a:
            item = {"@type": (a.get("itemtype") or "").rsplit("/", 1)[-1]}
            if self._stack and prop:
                self._stack[-1][0][prop] = item
            elif not self._stack:
                self.items.append(item)
            self._stack.append((item, prop, tag))
            return
        if prop and self._stack:
            item = self._stack[-1][0]
            value = a.get("content") or a.get("datetime") or a.get("href") or a.get("src")
            if value is not None:
                item[prop] = value
            elif tag not in ("meta", "link", "img"):
                self._text_target = (item, prop)
                item.setdefault(prop, "")

    def handle_endtag(self, tag):
        if self._text_target is not None:
            self._text_target = None
        if self._stack and self._stack[-1][2] == tag:
            self._stack.pop()

    def handle_data(self, data):
        if self._text_target is not None:
            item, prop = self._text_target
            item[prop] = (item.get(prop, "") + data).strip()


def extract(html: str | None) -> list[dict]:
    """Every schema.org object in the HTML: JSON-LD first, then microdata."""
    if not html:
        return []
    out: list[dict] = []
    for block in JSON_LD_RE.findall(html):
        try:
            out += _flatten(json.loads(block.strip()))
        except ValueError:
            continue
    if "itemscope" in html:
        parser = _Microdata()
        try:
            parser.feed(html)
        except Exception:  # noqa: BLE001 - a broken document yields what it yielded
            pass
        out += parser.items
    return out


# -------------------------------------------------------------- summaries


def _name(v) -> str:
    if isinstance(v, dict):
        return str(v.get("name") or v.get("iataCode") or v.get("legalName") or "").strip()
    return str(v or "").strip()


def _when(v) -> str:
    if not isinstance(v, str) or not v:
        return ""
    try:
        when = datetime.fromisoformat(v)
    except ValueError:
        return v
    if when.tzinfo is not None:
        when = when.astimezone()
    return when.strftime("%-d %b %H:%M") if (when.hour or when.minute) else when.strftime("%-d %b")


def _status(v) -> str:
    s = _name(v) or str(v or "")
    s = s.rsplit("/", 1)[-1]
    return {"OrderProcessing": "being prepared", "OrderInTransit": "shipped", "OrderDelivered": "delivered",
            "OrderShipped": "shipped", "OrderCancelled": "cancelled", "OrderReturned": "returned",
            "OrderPaymentDue": "payment due", "OrderProblem": "problem", "OrderPickupAvailable": "ready for pickup",
            "InTransit": "in transit", "OutForDelivery": "out for delivery", "Delivered": "delivered",
            "ReservationConfirmed": "confirmed", "ReservationCancelled": "cancelled", "ReservationPending": "pending",
            "PaymentDue": "payment due", "PaymentComplete": "paid", "PaymentPastDue": "overdue"}.get(s, s)


def _price(obj: dict) -> str:
    v = obj.get("totalPaymentDue") or obj.get("totalPrice") or obj.get("price")
    if isinstance(v, dict):
        amount, currency = v.get("price"), v.get("priceCurrency") or v.get("currency")
    else:
        amount, currency = v, obj.get("priceCurrency")
    return f"{amount} {currency}".strip() if amount else ""


def summarise_one(obj: dict) -> Summary | None:
    kind = _type_of(obj)
    if kind not in TRANSACTIONAL:
        return None
    parts: list[str] = []
    copy: str | None = None
    if kind == "Order":
        status = _status(obj.get("orderStatus"))
        merchant = _name(obj.get("merchant") or obj.get("seller"))
        items = obj.get("acceptedOffer") or obj.get("orderedItem") or []
        items = items if isinstance(items, list) else [items]
        names = [_name(i.get("itemOffered") or i.get("orderedItem") or i) for i in items if isinstance(i, dict)]
        parts.append(f"Order {status}" if status else "Order")
        if merchant:
            parts.append(merchant)
        if names := [n for n in names if n]:
            parts.append(", ".join(names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else ""))
        copy = str(obj.get("orderNumber") or "") or None
        if copy:
            parts.append(copy)
    elif kind == "ParcelDelivery":
        status = _status(obj.get("deliveryStatus"))
        parts.append(f"Parcel {status}" if status else "Parcel")
        if carrier := _name(obj.get("carrier") or obj.get("provider")):
            parts.append(carrier)
        arrival = _when(obj.get("expectedArrivalFrom") or obj.get("expectedArrivalUntil"))
        if arrival:
            parts.append(f"expected {arrival}")
        copy = str(obj.get("trackingNumber") or "") or None
        if copy:
            parts.append(copy)
    elif kind == "Invoice":
        status = _status(obj.get("paymentStatus"))
        parts.append(f"Invoice {status}" if status else "Invoice")
        if provider := _name(obj.get("provider") or obj.get("broker")):
            parts.append(provider)
        if price := _price(obj):
            parts.append(price)
        if due := _when(obj.get("paymentDueDate") or obj.get("paymentDue")):
            parts.append(f"due {due}")
        copy = str(obj.get("accountId") or obj.get("confirmationNumber") or "") or None
    else:   # reservations
        target = obj.get("reservationFor") if isinstance(obj.get("reservationFor"), dict) else {}
        status = _status(obj.get("reservationStatus"))
        if kind == "FlightReservation":
            airline = target.get("airline") if isinstance(target.get("airline"), dict) else {}
            code = f"{airline.get('iataCode') or _name(airline)} {target.get('flightNumber') or ''}".strip()
            parts.append(f"Flight {code}".strip())
            route = " → ".join(x for x in (_name(target.get("departureAirport")), _name(target.get("arrivalAirport"))) if x)
            if route:
                parts.append(route)
            if dep := _when(target.get("departureTime")):
                parts.append(dep)
        elif kind in ("TrainReservation", "BusReservation"):
            number = target.get("trainNumber") or target.get("busNumber") or ""
            parts.append(f"{'Train' if kind == 'TrainReservation' else 'Bus'} {number}".strip())
            route = " → ".join(x for x in (_name(target.get("departureStation") or target.get("departureBusStop")),
                                           _name(target.get("arrivalStation") or target.get("arrivalBusStop"))) if x)
            if route:
                parts.append(route)
            if dep := _when(target.get("departureTime")):
                parts.append(dep)
        elif kind == "LodgingReservation":
            parts.append(f"Stay at {_name(target)}" if _name(target) else "Stay")
            stay = " – ".join(  # noqa: RUF001 - an en dash between two dates
            x for x in (_when(obj.get("checkinTime") or obj.get("checkinDate")),
                                          _when(obj.get("checkoutTime") or obj.get("checkoutDate"))) if x)
            if stay:
                parts.append(stay)
        elif kind == "EventReservation":
            parts.append(_name(target) or "Event")
            if start := _when(target.get("startDate")):
                parts.append(start)
            if place := _name(target.get("location")):
                parts.append(place)
        elif kind == "RentalCarReservation":
            parts.append(f"Car: {_name(target)}" if _name(target) else "Rental car")
            if pick := _when(obj.get("pickupTime")):
                parts.append(pick)
        elif kind == "FoodEstablishmentReservation":
            parts.append(f"Table at {_name(target)}" if _name(target) else "Table")
            if start := _when(obj.get("startTime")):
                parts.append(start)
            if obj.get("partySize"):
                parts.append(f"{obj['partySize']} people")
        else:
            parts.append(_name(target) or kind)
        if status and status != "confirmed":
            parts.append(status)
        copy = str(obj.get("reservationNumber") or obj.get("reservationId") or "") or None
        if copy:
            parts.append(copy)
    text = " · ".join(p for p in parts if p)
    return Summary(kind, text, copy) if text else None


def summarise(objects: list[dict]) -> Summary | None:
    """The most telling summary among the objects: a delivery over an order over the rest."""
    best: Summary | None = None
    rank = {"ParcelDelivery": 0, "Order": 1, "Invoice": 2}
    for obj in objects:
        s = summarise_one(obj)
        if s is None:
            continue
        # an Order carrying its delivery inside: prefer the delivery
        if s.kind == "Order" and isinstance(obj.get("orderDelivery"), dict):
            inner = summarise_one(obj["orderDelivery"])
            if inner is not None:
                s = inner
        if best is None or rank.get(s.kind, 3) < rank.get(best.kind, 3):
            best = s
    return best


def summarise_html(html: str | None) -> Summary | None:
    return summarise(extract(html))
