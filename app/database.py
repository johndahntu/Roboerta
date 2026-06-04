from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import DATABASE_PATH, REPORT_RETENTION_DAYS, ensure_directories


GROUP_ORDER = [
    "front_page_items",
    "price_lock_items",
    "just_4_u_items",
    "five_friday_items",
    "member_price_items",
    "regular_items",
]

GROUP_LABELS = {
    "front_page_items": "Front Page Items",
    "price_lock_items": "Price Lock Items",
    "just_4_u_items": "Just 4 U Items",
    "five_friday_items": "$5 Friday Items",
    "member_price_items": "Member Price Items",
    "regular_items": "Regular Items",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def get_connection() -> sqlite3.Connection:
    ensure_directories()
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS weekly_ads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ad_date TEXT NOT NULL,
                source_filenames_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ad_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                weekly_ad_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                price_text TEXT,
                page_number INTEGER,
                tags_json TEXT NOT NULL,
                notes TEXT,
                FOREIGN KEY (weekly_ad_id) REFERENCES weekly_ads(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                source_filename TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                highlights_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS report_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                group_name TEXT NOT NULL,
                item_name TEXT NOT NULL,
                matched_ad_name TEXT NOT NULL,
                ad_price TEXT,
                source_section TEXT,
                notes TEXT,
                done INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
            );
            """
        )


def normalize_name(name: str) -> str:
    return " ".join("".join(character.lower() if character.isalnum() else " " for character in name).split())


def prune_expired_reports() -> None:
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM reports WHERE expires_at < ?",
            (utc_now().isoformat(),),
        )


def replace_weekly_ad(ad_date: str, source_filenames: list[str], items: list[dict[str, Any]]) -> None:
    created_at = utc_now().isoformat()
    with get_connection() as connection:
        connection.execute("DELETE FROM reports")
        connection.execute("DELETE FROM ad_items")
        connection.execute("DELETE FROM weekly_ads")
        cursor = connection.execute(
            "INSERT INTO weekly_ads (ad_date, source_filenames_json, created_at) VALUES (?, ?, ?)",
            (ad_date, json.dumps(source_filenames), created_at),
        )
        weekly_ad_id = cursor.lastrowid
        for item in items:
            connection.execute(
                """
                INSERT INTO ad_items (
                    weekly_ad_id,
                    name,
                    normalized_name,
                    price_text,
                    page_number,
                    tags_json,
                    notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    weekly_ad_id,
                    item["name"],
                    normalize_name(item["name"]),
                    item.get("price_text"),
                    item.get("page_number"),
                    json.dumps(item.get("tags", [])),
                    item.get("notes"),
                ),
            )


def get_active_ad() -> dict[str, Any] | None:
    with get_connection() as connection:
        ad_row = connection.execute(
            "SELECT id, ad_date, source_filenames_json, created_at FROM weekly_ads ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if ad_row is None:
            return None
        item_rows = connection.execute(
            "SELECT id, name, normalized_name, price_text, page_number, tags_json, notes FROM ad_items WHERE weekly_ad_id = ? ORDER BY page_number, name",
            (ad_row["id"],),
        ).fetchall()
    items = []
    counts = {group_name: 0 for group_name in GROUP_ORDER}
    for row in item_rows:
        tags = json.loads(row["tags_json"])
        for tag in tags:
            if tag in counts:
                counts[tag] += 1
        items.append(
            {
                "id": row["id"],
                "name": row["name"],
                "normalized_name": row["normalized_name"],
                "price_text": row["price_text"],
                "page_number": row["page_number"],
                "tags": tags,
                "notes": row["notes"],
            }
        )
    return {
        "id": ad_row["id"],
        "ad_date": ad_row["ad_date"],
        "source_filenames": json.loads(ad_row["source_filenames_json"]),
        "created_at": ad_row["created_at"],
        "items": items,
        "counts": counts,
        "item_count": len(items),
    }


def create_report(kind: str, source_filename: str, highlights: list[str], groups: dict[str, list[dict[str, Any]]]) -> int:
    created_at = utc_now()
    expires_at = created_at + timedelta(days=REPORT_RETENTION_DAYS)
    summary = {group_name: len(groups.get(group_name, [])) for group_name in GROUP_ORDER}
    with get_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO reports (kind, source_filename, created_at, expires_at, summary_json, highlights_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                kind,
                source_filename,
                created_at.isoformat(),
                expires_at.isoformat(),
                json.dumps(summary),
                json.dumps(highlights),
            ),
        )
        report_id = cursor.lastrowid
        for group_name in GROUP_ORDER:
            for item in groups.get(group_name, []):
                connection.execute(
                    """
                    INSERT INTO report_matches (
                        report_id,
                        group_name,
                        item_name,
                        matched_ad_name,
                        ad_price,
                        source_section,
                        notes,
                        done
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report_id,
                        group_name,
                        item.get("item_name") or item.get("matched_ad_name") or "Unnamed Item",
                        item.get("matched_ad_name") or item.get("item_name") or "Unnamed Item",
                        item.get("ad_price"),
                        item.get("source_section"),
                        item.get("notes"),
                        1 if item.get("done") else 0,
                    ),
                )
    return report_id


def list_reports() -> list[dict[str, Any]]:
    with get_connection() as connection:
        report_rows = connection.execute(
            "SELECT id, kind, source_filename, created_at, expires_at, summary_json, highlights_json FROM reports ORDER BY created_at DESC"
        ).fetchall()
        match_rows = connection.execute(
            "SELECT id, report_id, group_name, item_name, matched_ad_name, ad_price, source_section, notes, done FROM report_matches ORDER BY report_id DESC, id ASC"
        ).fetchall()

    grouped_matches: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for row in match_rows:
        report_groups = grouped_matches.setdefault(row["report_id"], {group_name: [] for group_name in GROUP_ORDER})
        report_groups.setdefault(row["group_name"], []).append(
            {
                "id": row["id"],
                "item_name": row["item_name"],
                "matched_ad_name": row["matched_ad_name"],
                "ad_price": row["ad_price"],
                "source_section": row["source_section"],
                "notes": row["notes"],
                "done": bool(row["done"]),
            }
        )

    reports: list[dict[str, Any]] = []
    for row in report_rows:
        summary = json.loads(row["summary_json"])
        reports.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "source_filename": row["source_filename"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "summary": summary,
                "highlights": json.loads(row["highlights_json"]),
                "groups": grouped_matches.get(row["id"], {group_name: [] for group_name in GROUP_ORDER}),
                "total_matches": sum(summary.values()),
            }
        )
    return reports


def get_report(report_id: int) -> dict[str, Any] | None:
    for report in list_reports():
        if report["id"] == report_id:
            return report
    return None


def toggle_match_done(report_id: int, match_id: int) -> None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT done FROM report_matches WHERE id = ? AND report_id = ?",
            (match_id, report_id),
        ).fetchone()
        if row is None:
            return
        next_value = 0 if row["done"] else 1
        connection.execute(
            "UPDATE report_matches SET done = ? WHERE id = ? AND report_id = ?",
            (next_value, match_id, report_id),
        )


def delete_report(report_id: int) -> None:
    with get_connection() as connection:
        connection.execute("DELETE FROM reports WHERE id = ?", (report_id,))
