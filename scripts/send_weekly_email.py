#!/usr/bin/env python3
"""Send LandRegistry26 weekly email summary via SMTP.

Required secrets/environment:
  SMTP_LANDREGISTRY_HOST
  SMTP_LANDREGISTRY_PORT
  SMTP_LANDREGISTRY_USER
  SMTP_LANDREGISTRY_PASSWORD
  SMTP_LANDREGISTRY_FROM
Optional:
  SMTP_LANDREGISTRY_TO - comma-separated recipients. Defaults to FROM.
  SITE_URL - public website URL.
"""
from __future__ import annotations

import csv
import json
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

REPORTS = Path("data/reports")


def getenv_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def read_manifest() -> dict[str, Any]:
    path = REPORTS / "run_manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv_rows(name: str, limit: int = 5) -> list[dict[str, str]]:
    path = REPORTS / name
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[:limit]


def money(value: str | int | float | None) -> str:
    try:
        amount = float(value)  # type: ignore[arg-type]
    except Exception:
        return "—"
    if amount >= 1_000_000:
        return f"£{amount/1_000_000:.2f}m"
    return f"£{amount:,.0f}"


def plain_table(rows: list[dict[str, str]], columns: list[str]) -> str:
    if not rows:
        return "No rows available yet."
    lines: list[str] = []
    for row in rows:
        parts = []
        for col in columns:
            val = row.get(col, "")
            if "price" in col or "value" in col:
                val = money(val)
            parts.append(f"{col}: {val}")
        lines.append("; ".join(parts))
    return "\n".join(lines)


def build_body(manifest: dict[str, Any]) -> str:
    site_url = os.getenv("SITE_URL", "https://scclandregistry.co.uk").strip()
    run_utc = manifest.get("run_utc", datetime.now(timezone.utc).isoformat())
    prefixes = ", ".join(manifest.get("postcode_prefixes", [])) or "configured areas"
    years = ", ".join(str(y) for y in manifest.get("years", [])) or "configured years"
    rows = manifest.get("rows", "unknown")

    year_rows = read_csv_rows("summary_by_year.csv", 8)
    area_rows = read_csv_rows("summary_by_postcode_district.csv", 10)
    latest_rows = read_csv_rows("latest_sales.csv", 8)

    return f"""LandRegistry26 weekly property intelligence update

Run: {run_utc}
Website: {site_url}
Postcode prefixes: {prefixes}
Years analysed: {years}
Filtered rows: {rows}

Annual summary
{plain_table(year_rows, ['year', 'transactions', 'median_price', 'mean_price', 'total_value', 'median_yoy_pct'])}

Top postcode districts
{plain_table(area_rows, ['postcode_district', 'transactions', 'median_price', 'mean_price', 'total_value'])}

Latest sales sample
{plain_table(latest_rows, ['transfer_date', 'price', 'postcode', 'property_type_label', 'town_city', 'district'])}

Notes
- Latest week/month can be provisional because registrations lag completions.
- England and Wales address-level Price Paid Data comes from HM Land Registry.
- This is analytical information, not legal, mortgage, valuation or investment advice.
"""


def main() -> int:
    host = getenv_required("SMTP_LANDREGISTRY_HOST")
    port = int(getenv_required("SMTP_LANDREGISTRY_PORT"))
    user = getenv_required("SMTP_LANDREGISTRY_USER")
    password = getenv_required("SMTP_LANDREGISTRY_PASSWORD")
    sender = getenv_required("SMTP_LANDREGISTRY_FROM")
    recipients = os.getenv("SMTP_LANDREGISTRY_TO", "").strip() or sender
    to_list = [x.strip() for x in recipients.replace(";", ",").split(",") if x.strip()]
    if not to_list:
        raise RuntimeError("No email recipients configured.")

    manifest = read_manifest()
    prefixes = ", ".join(manifest.get("postcode_prefixes", [])) or "areas"
    subject = f"LandRegistry26 weekly update - {prefixes}"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    msg.set_content(build_body(manifest))

    # Attach the light summary CSVs when available.
    for filename in ["summary_by_year.csv", "summary_by_month.csv", "summary_by_postcode_district.csv", "latest_sales.csv"]:
        path = REPORTS / filename
        if path.exists() and path.stat().st_size < 2_000_000:
            msg.add_attachment(path.read_bytes(), maintype="text", subtype="csv", filename=filename)

    if port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(user, password)
            smtp.send_message(msg)

    print(f"Sent LandRegistry26 email summary to {', '.join(to_list)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
