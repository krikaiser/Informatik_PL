"""Lokaler Verlauf der in der Oberfläche verwendeten Prüfregeln."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from ifc_pset_check import Rule, parse_rule


HISTORY_PATH = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Attributpruefung" / "Regeln.sqlite3"


def load_rule_history(path: Path) -> list[Rule]:
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(rules)")}
        if not columns:
            return []
        classes_column = "classes" if "classes" in columns else "''"
        rows = connection.execute(f"SELECT pset, properties, {classes_column} FROM rules ORDER BY id DESC")
        return [
            parse_rule(pset, properties, tuple(classes.split(",")) if classes else None)
            for pset, properties, classes in rows
        ]


def save_rule_history(path: Path, rules: tuple[Rule, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(rules)")}
        migrate = bool(columns) and "classes" not in columns
        if migrate:
            connection.execute("ALTER TABLE rules RENAME TO legacy_rules")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY,
                pset TEXT NOT NULL,
                properties TEXT NOT NULL,
                classes TEXT NOT NULL,
                UNIQUE (pset, properties, classes)
            )
            """
        )
        if migrate:
            connection.execute("INSERT INTO rules SELECT id, pset, properties, '' FROM legacy_rules")
            connection.execute("DROP TABLE legacy_rules")
        connection.executemany(
            "INSERT OR REPLACE INTO rules (pset, properties, classes) VALUES (?, ?, ?)",
            (
                (rule.pset, ",".join(rule.properties), "" if rule.include_subtypes else ",".join(rule.classes))
                for rule in reversed(rules)
            ),
        )
