#!/usr/bin/env python3
"""Run the categoriser's rules over a cached account and show what they decided (#18).

    python tools/categoriser_report.py                 # the last account's cache
    python tools/categoriser_report.py path/to.sqlite3 --samples 30 --category newsletters

Reads the SQLite cache only; nothing is sent anywhere.  Use it after changing a rule,
or on a new account, and read the samples of the categories you doubt: every line
shows the rule that fired, the sender, the subject and the start of the preview.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from den_mail.classify.rules import CATEGORIES, CLASSIFY_HEADERS, classify, sender_address


def default_cache() -> Path:
    from den_mail.config import Config, database_path

    account = Config().get("last_account")
    if not account:
        sys.exit("no account has signed in yet; pass the cache path")
    return database_path(account)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cache", nargs="?", type=Path, help="the account's .sqlite3 cache (default: last account)")
    ap.add_argument("--samples", type=int, default=25, help="lines to show per category")
    ap.add_argument("--category", choices=CATEGORIES, help="show samples of this category only")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--include-junk", action="store_true", help="also count mail in Spam and Trash")
    args = ap.parse_args()
    path = args.cache or default_cache()
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    roles = {r["role"]: r["id"] for r in db.execute("SELECT id, role FROM mailboxes WHERE role IS NOT NULL")}
    skip = {roles.get("sent")} | (set() if args.include_junk else {roles.get("junk"), roles.get("trash")})
    identities = {r[0].lower() for r in db.execute("SELECT email FROM identities")}
    domains = {i[2:] for i in identities if i.startswith("*@")}
    correspondents = {r[0] for r in db.execute("SELECT email FROM correspondents")}

    def is_own(addr: str) -> bool:
        return addr in identities or ("@" in addr and addr.rsplit("@", 1)[1] in domains)

    emails = [json.loads(r["json"]) for r in db.execute("SELECT json FROM emails")]
    rows = [e for e in emails if not set(e.get("mailboxIds") or {}) & skip]
    with_headers = sum(1 for e in rows if CLASSIFY_HEADERS[0] in e)
    print(f"{path}\n{len(rows)} messages ({with_headers} with headers fetched), "
          f"{len(correspondents)} correspondents known\n")
    by_cat: dict[str, list] = collections.defaultdict(list)
    reasons: collections.Counter = collections.Counter()
    for e in rows:
        c = classify(e, correspondents.__contains__, is_own)
        by_cat[c.category].append((c, e))
        reasons[(c.category, c.reason)] += 1
    for cat in sorted(by_cat, key=lambda k: -len(by_cat[k])):
        print(f"{cat:13}{len(by_cat[cat]):6}")
    print("\nrule that fired:")
    for (cat, reason), n in reasons.most_common():
        print(f"{n:6}  {cat:13} {reason}")
    random.seed(args.seed)
    for cat in ([args.category] if args.category else CATEGORIES):
        items = by_cat.get(cat, [])
        print(f"\n== {cat} ({len(items)})")
        for c, e in random.sample(items, min(args.samples, len(items))):
            print(f"  [{c.confidence:.2f} {c.reason[:34]:34}] {sender_address(e)[:34]:34} | "
                  f"{(e.get('subject') or '')[:64]:64} | {(e.get('preview') or '')[:40]!r}")


if __name__ == "__main__":
    main()
