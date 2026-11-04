"""Explicit QA-only additive enrichment for the public reference card catalog.

Run through the QA container, never production. Does not create accounts,
reset data, or modify decks. Existing public-card records are preserved.
"""

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def seed(db_path: Path, snapshot: Path) -> int:
    cards = json.loads(snapshot.read_text())["cards"]
    with sqlite3.connect(db_path) as conn:
        count = 0
        for card in cards:
            if conn.execute(
                "SELECT 1 FROM cards WHERE oracle_id=? OR name=?",
                (card["oracle_id"], card["name"]),
            ).fetchone():
                continue
            conn.execute(
                "INSERT INTO cards (id,oracle_id,name,type_line,mana_cost,cmc,oracle_text,"
                "color_identity,is_legal_in_99,is_legal_commander,set_code,rarity,last_updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    card["id"],
                    card["oracle_id"],
                    card["name"],
                    card["type_line"],
                    card["mana_cost"],
                    card["cmc"],
                    card["oracle_text"],
                    json.dumps(card["color_identity"]),
                    int(card["legalities"].get("commander") == "legal"),
                    0,
                    card["set"],
                    card["rarity"],
                    datetime.now(UTC).isoformat(),
                ),
            )
            count += 1
        conn.commit()
    return count


if __name__ == "__main__":
    if os.environ.get("GOLDFISH_QA_ENRICHMENT") != "qa-only":
        raise SystemExit("Requires explicit GOLDFISH_QA_ENRICHMENT=qa-only.")
    print(
        json.dumps(
            {
                "inserted_public_cards": seed(
                    Path(os.environ.get("SABER_DB_PATH", "/data/sabermetrics.db")),
                    Path("/app/fixtures/goldfish/public-card-additions.json"),
                )
            }
        )
    )
