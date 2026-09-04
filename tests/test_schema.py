"""schema.org data in mail (#20): extraction, summaries, and what the cache does with them."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from den_mail.classify.rules import SOURCE_RULES, SOURCE_USER, TRANSACTIONS
from den_mail.html import schema

from .test_engine import engine, pump, server  # noqa: F401 - fixtures
from .test_views import db, email  # noqa: F401 - fixture and helper

ORDER = """<html><head><script type="application/ld+json">
{"@context": "http://schema.org", "@type": "Order", "orderNumber": "4711", "orderStatus": "http://schema.org/OrderInTransit",
 "merchant": {"@type": "Organization", "name": "Blum Manufaktur"},
 "acceptedOffer": [{"@type": "Offer", "itemOffered": {"@type": "Product", "name": "Kitchen scissors"}},
                   {"@type": "Offer", "itemOffered": {"@type": "Product", "name": "Whetstone"}},
                   {"@type": "Offer", "itemOffered": {"@type": "Product", "name": "Oil"}}],
 "orderDelivery": {"@type": "ParcelDelivery", "deliveryStatus": "http://schema.org/InTransit",
   "carrier": {"@type": "Organization", "name": "DHL"}, "trackingNumber": "00340434161094015902"}}
</script></head><body>hi</body></html>"""


def test_json_ld_and_microdata_are_found():
    objs = schema.extract(ORDER)
    assert [o["@type"] for o in objs] == ["Order"]
    graph = '<script type="application/ld+json">{"@graph": [{"@type": "Invoice", "accountId": "A-1"}, {"@type": "Thing"}]}</script>'
    assert [o["@type"] for o in schema.extract(graph)] == ["Invoice", "Thing"]
    arr = '<SCRIPT TYPE="application/ld+json">[{"@type": ["FlightReservation"]}]</SCRIPT><script type="application/ld+json">not json</script>'
    assert schema._type_of(schema.extract(arr)[0]) == "FlightReservation"
    micro = """<div itemscope itemtype="http://schema.org/LodgingReservation">
      <meta itemprop="reservationNumber" content="H-77">
      <div itemprop="reservationFor" itemscope itemtype="http://schema.org/LodgingBusiness"><span itemprop="name">Hotel Marski</span></div>
      <time itemprop="checkinTime" datetime="2026-09-10T15:00:00+03:00">10 Sep</time>
      <time itemprop="checkoutTime" datetime="2026-09-12T11:00:00+03:00">12 Sep</time></div>"""
    objs = schema.extract(micro)
    assert objs and objs[0]["@type"] == "LodgingReservation" and objs[0]["reservationNumber"] == "H-77"
    assert objs[0]["reservationFor"]["name"] == "Hotel Marski" and objs[0]["checkinTime"].startswith("2026-09-10")
    assert schema.extract(None) == [] and schema.extract("<p>nothing</p>") == []


def test_summaries_per_type():
    s = schema.summarise_html(ORDER)
    assert s.kind == "ParcelDelivery" and s.copy == "00340434161094015902"   # the delivery inside the order wins
    assert s.text.startswith("Parcel in transit · DHL") and s.copy in s.text
    order = schema.summarise_one(schema.extract(ORDER)[0])
    assert order.text == "Order shipped · Blum Manufaktur · Kitchen scissors, Whetstone +1 · 4711" and order.copy == "4711"
    flight = {"@type": "FlightReservation", "reservationNumber": "ABC123", "reservationStatus": "http://schema.org/ReservationConfirmed",
              "reservationFor": {"@type": "Flight", "airline": {"iataCode": "LH"}, "flightNumber": "123",
                                 "departureAirport": {"iataCode": "FRA"}, "arrivalAirport": {"iataCode": "HEL"},
                                 "departureTime": "2026-10-12T09:40:00+02:00"}}
    t = schema.summarise_one(flight)
    assert t.text.startswith("Flight LH 123 · FRA → HEL · 12 Oct ") and t.text.endswith("· ABC123") and t.copy == "ABC123"
    train = {"@type": "TrainReservation", "reservationFor": {"trainNumber": "ICE 703", "departureStation": {"name": "Berlin Hbf"},
             "arrivalStation": {"name": "München Hbf"}, "departureTime": "2026-09-18T19:14:00+02:00"}}
    assert schema.summarise_one(train).text.startswith("Train ICE 703 · Berlin Hbf → München Hbf · 18 Sep")
    invoice = {"@type": "Invoice", "paymentStatus": "PaymentDue", "provider": {"name": "MVG"}, "accountId": "403806215993",
               "totalPaymentDue": {"@type": "PriceSpecification", "price": "49.00", "priceCurrency": "EUR"}, "paymentDueDate": "2026-09-30"}
    assert schema.summarise_one(invoice).text == "Invoice payment due · MVG · 49.00 EUR · due 30 Sep"
    event = {"@type": "EventReservation", "reservationFor": {"name": "GTK meetup", "startDate": "2026-09-11T18:00:00",
             "location": {"name": "Room 2.04"}}, "reservationNumber": "E-9"}
    assert schema.summarise_one(event).text.startswith("GTK meetup · 11 Sep 18:00 · Room 2.04")
    table = {"@type": "FoodEstablishmentReservation", "reservationFor": {"name": "Trattoria"}, "startTime": "2026-09-05T20:00:00+02:00", "partySize": 4}
    assert "Table at Trattoria" in schema.summarise_one(table).text and "4 people" in schema.summarise_one(table).text
    assert schema.summarise_one({"@type": "Thing", "name": "x"}) is None
    assert schema.summarise([{"@type": "Person"}]) is None


def test_cached_body_gets_a_summary_and_transactions_for_sure(db):  # noqa: F811
    db.upsert_emails([email("a", "Hello", "bestellung@blum.example", "Blum"), email("b", "Hello", "bestellung@blum.example", "Blum")])
    assert db.get_categories(["a"])["a"] != TRANSACTIONS
    body = {**email("a", "Hello", "bestellung@blum.example", "Blum"), "htmlBody": [{"partId": "h", "type": "text/html"}],
            "bodyValues": {"h": {"value": ORDER}}}
    db.set_email_body(body)
    info = db.get_structured("a")
    assert info["kind"] == "ParcelDelivery" and info["copy"] == "00340434161094015902"
    row = db.get_classification("a")
    assert row["category"] == TRANSACTIONS and row["source"] == SOURCE_RULES and row["reason"] == "schema.org ParcelDelivery"
    assert row["confidence"] >= 0.9
    # the user's own choice stays
    db.set_category(["b"], "primary")
    db.set_email_body({**body, "id": "b"})
    assert db.get_classification("b")["source"] == SOURCE_USER and db.get_structured("b") is not None
    # a body without data clears an old summary
    db.set_email_body({**body, "bodyValues": {"h": {"value": "<p>plain</p>"}}})
    assert db.get_structured("a") is None


def test_engine_summarises_the_fixture_shipping_notice(engine, server):  # noqa: F811
    shipped = next(e for e in server.data.emails.values() if e["subject"].startswith("Your order 4711"))
    engine.fetch_body(shipped["id"])
    # body-ready comes after the body and its structured row are both written; reading the
    # body alone could catch the moment in between (#109)
    pump(lambda: any(a[0] == shipped["id"] for a in engine.events.get("body-ready", [])), timeout=10)
    info = engine.db.get_structured(shipped["id"])
    assert info and info["kind"] == "ParcelDelivery" and "DHL" in info["text"]
    assert engine.db.get_categories([shipped["id"]])[shipped["id"]] == TRANSACTIONS
