#!/usr/bin/env python3
"""
LandRegistry26 weekly build.

This is intentionally self-contained so it runs reliably in GitHub Actions.
It downloads HM Land Registry Price Paid yearly CSVs, filters to chosen postcode
prefixes, produces summaries and builds a static website.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import requests
import yaml

PPD_COLUMNS = [
    "transaction_id",
    "price",
    "transfer_date",
    "postcode",
    "property_type",
    "old_new",
    "duration",
    "paon",
    "saon",
    "street",
    "locality",
    "town_city",
    "district",
    "county",
    "ppd_category_type",
    "record_status",
]

PROPERTY_TYPE = {
    "D": "Detached",
    "S": "Semi-detached",
    "T": "Terraced",
    "F": "Flat/maisonette",
    "O": "Other",
}

DURATION = {"F": "Freehold", "L": "Leasehold"}
OLD_NEW = {"Y": "New build", "N": "Resale"}


def read_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def mkdirs(*paths: Path) -> None:
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)


def parse_years(s: str | None, cfg: dict) -> list[int]:
    """Parse workflow year input. Accepts: "2025 2026", "2025,2026", or "1995-2026"."""
    if s:
        years: list[int] = []
        tokens = [x.strip() for x in s.replace(",", " ").split() if x.strip()]
        for token in tokens:
            if "-" in token:
                a, b = token.split("-", 1)
                start, end = int(a), int(b)
                if end < start:
                    start, end = end, start
                years.extend(range(start, end + 1))
            else:
                years.append(int(token))
    else:
        a = int(cfg["analysis"].get("min_year", datetime.now().year - 1))
        b = int(cfg["analysis"].get("max_year", datetime.now().year))
        years = list(range(a, b + 1))
    return sorted(set(years))


def normalise_prefixes(s: str | None, cfg: dict) -> list[str]:
    if s:
        vals = [x.strip().upper().replace(" ", "") for x in s.replace(",", " ").split() if x.strip()]
    else:
        vals = [str(x).upper().replace(" ", "") for x in cfg["analysis"].get("postcode_prefixes", ["BN"])]
    vals = vals or ["BN"]
    # Special case for national England & Wales runs. Previously ALL was treated as
    # a literal postcode prefix, which matched nothing and produced empty CSV files.
    if any(v in {"ALL", "*", "UK", "ENGLANDWALES", "ENGLANDANDWALES"} for v in vals):
        return ["ALL"]
    return vals


def download_file(url: str, dest: Path, timeout: int = 60) -> bool:
    print(f"Trying download: {url}")
    try:
        with requests.get(url, stream=True, timeout=timeout, headers={"User-Agent": "LandRegistry26/1.0"}) as r:
            if r.status_code != 200:
                print(f"HTTP {r.status_code} for {url}")
                return False
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
            if tmp.stat().st_size < 100:
                tmp.unlink(missing_ok=True)
                return False
            tmp.replace(dest)
            print(f"Downloaded {dest} ({dest.stat().st_size:,} bytes)")
            return True
    except Exception as e:
        print(f"Download failed for {url}: {e}")
        return False


def ensure_year_csv(year: int, cfg: dict, raw_dir: Path) -> Path:
    dest = raw_dir / f"pp-{year}.csv"
    if dest.exists() and dest.stat().st_size > 100:
        print(f"Using cached {dest}")
        return dest
    for pattern in cfg["source"]["yearly_csv_url_patterns"]:
        if download_file(pattern.format(year=year), dest):
            return dest
    raise RuntimeError(f"Unable to download Price Paid CSV for {year}.")


def load_filter_year(csv_path: Path, prefixes: list[str], standard_only: bool) -> pd.DataFrame:
    national_run = any(p in {"ALL", "*", "UK", "ENGLANDWALES", "ENGLANDANDWALES"} for p in prefixes)
    wanted_prefix = tuple(p for p in prefixes if p not in {"ALL", "*", "UK", "ENGLANDWALES", "ENGLANDANDWALES"})
    chunks = []
    usecols = list(range(len(PPD_COLUMNS)))
    for chunk in pd.read_csv(
        csv_path,
        header=None,
        names=PPD_COLUMNS,
        usecols=usecols,
        dtype={
            "transaction_id": "string",
            "postcode": "string",
            "property_type": "string",
            "old_new": "string",
            "duration": "string",
            "paon": "string",
            "saon": "string",
            "street": "string",
            "locality": "string",
            "town_city": "string",
            "district": "string",
            "county": "string",
            "ppd_category_type": "string",
            "record_status": "string",
        },
        parse_dates=["transfer_date"],
        chunksize=200_000,
    ):
        chunk["postcode_clean"] = chunk["postcode"].fillna("").str.upper().str.replace(" ", "", regex=False)
        if national_run:
            mask = pd.Series(True, index=chunk.index)
        else:
            mask = chunk["postcode_clean"].str.startswith(wanted_prefix)
        if standard_only:
            mask &= chunk["ppd_category_type"].fillna("").eq("A")
        chunk = chunk.loc[mask].copy()
        if len(chunk):
            chunks.append(chunk)
    if not chunks:
        return pd.DataFrame(columns=PPD_COLUMNS + ["postcode_clean"])
    df = pd.concat(chunks, ignore_index=True)
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["year"] = df["transfer_date"].dt.year
    df["month"] = df["transfer_date"].dt.to_period("M").astype(str)
    df["week"] = df["transfer_date"].dt.to_period("W").astype(str)
    df["postcode_district"] = df["postcode"].fillna("").str.upper().str.extract(r"^([A-Z]{1,2}\d{1,2}[A-Z]?)", expand=False)
    df["property_type_label"] = df["property_type"].map(PROPERTY_TYPE).fillna(df["property_type"])
    df["duration_label"] = df["duration"].map(DURATION).fillna(df["duration"])
    df["old_new_label"] = df["old_new"].map(OLD_NEW).fillna(df["old_new"])
    return df


def agg_stats(g: pd.core.groupby.generic.DataFrameGroupBy) -> pd.DataFrame:
    return g["price"].agg(
        transactions="count",
        total_value="sum",
        mean_price="mean",
        median_price="median",
        min_price="min",
        max_price="max",
    ).reset_index()


def pct_change_safe(series: pd.Series) -> pd.Series:
    return series.pct_change().replace([np.inf, -np.inf], np.nan) * 100


def make_outputs(df: pd.DataFrame, out_dir: Path, high_value_threshold: int, top_n: int) -> dict[str, Path]:
    mkdirs(out_dir)
    outputs = {}
    if df.empty:
        for name in ["summary_by_year", "summary_by_month", "summary_by_week", "summary_by_postcode_district", "summary_by_property_type_year", "latest_sales", "high_value_sales"]:
            path = out_dir / f"{name}.csv"
            pd.DataFrame().to_csv(path, index=False)
            outputs[name] = path
        return outputs

    summary_by_year = agg_stats(df.groupby("year")).sort_values("year")
    summary_by_year["median_yoy_pct"] = pct_change_safe(summary_by_year["median_price"])
    summary_by_year["transactions_yoy_pct"] = pct_change_safe(summary_by_year["transactions"])

    summary_by_month = agg_stats(df.groupby("month")).sort_values("month")
    summary_by_month["median_mom_pct"] = pct_change_safe(summary_by_month["median_price"])
    summary_by_month["transactions_mom_pct"] = pct_change_safe(summary_by_month["transactions"])

    summary_by_week = agg_stats(df.groupby("week")).sort_values("week")
    summary_by_week["median_wow_pct"] = pct_change_safe(summary_by_week["median_price"])
    summary_by_week["transactions_wow_pct"] = pct_change_safe(summary_by_week["transactions"])

    summary_by_postcode = agg_stats(df.groupby("postcode_district")).sort_values("transactions", ascending=False)
    summary_by_type_year = agg_stats(df.groupby(["year", "property_type_label"])).sort_values(["year", "property_type_label"])

    latest_sales = df.sort_values("transfer_date", ascending=False).head(250)[[
        "transfer_date", "price", "postcode", "property_type_label", "old_new_label", "duration_label",
        "paon", "saon", "street", "locality", "town_city", "district", "county"
    ]]

    high_value = df[df["price"] >= high_value_threshold].sort_values("price", ascending=False).head(500)[[
        "transfer_date", "price", "postcode", "property_type_label", "old_new_label", "duration_label",
        "paon", "saon", "street", "town_city", "district", "county"
    ]]

    tables = {
        "summary_by_year": summary_by_year,
        "summary_by_month": summary_by_month,
        "summary_by_week": summary_by_week,
        "summary_by_postcode_district": summary_by_postcode,
        "summary_by_property_type_year": summary_by_type_year,
        "latest_sales": latest_sales,
        "high_value_sales": high_value,
    }
    for name, table in tables.items():
        path = out_dir / f"{name}.csv"
        table.to_csv(path, index=False)
        outputs[name] = path
    return outputs


def money(x) -> str:
    if pd.isna(x):
        return "—"
    x = float(x)
    if abs(x) >= 1_000_000:
        return f"£{x/1_000_000:.2f}m"
    return f"£{x:,.0f}"


def num(x) -> str:
    if pd.isna(x):
        return "—"
    return f"{float(x):,.0f}"


def pct(x) -> str:
    if pd.isna(x):
        return "—"
    sign = "+" if x > 0 else ""
    return f"{sign}{x:.1f}%"


def html_table(df: pd.DataFrame, max_rows: int = 20) -> str:
    if df.empty:
        return "<p class='muted'>No rows available for this selection.</p>"
    view = df.head(max_rows).copy()
    for col in view.columns:
        if "price" in col or "value" in col:
            view[col] = view[col].map(money)
        elif col in {"transactions"}:
            view[col] = view[col].map(num)
        elif col.endswith("_pct"):
            view[col] = view[col].map(pct)
    return view.to_html(index=False, classes="data-table", border=0, escape=True)


def write_site(df: pd.DataFrame, reports: dict[str, Path], cfg: dict, prefixes: list[str], site_dir: Path, out_dir: Path) -> None:
    mkdirs(site_dir, site_dir / "assets", site_dir / "data")
    # Copy CSV reports for browser downloads
    for name, path in reports.items():
        if path.exists():
            shutil_path = site_dir / "data" / path.name
            shutil_path.write_bytes(path.read_bytes())

    run_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = cfg["project"].get("title", "UK Land Registry Intelligence")
    brand = cfg["site"].get("brand", "LandRegistry26")
    attribution = cfg["site"].get("footer_attribution", "")

    def read(name):
        p = reports.get(name)
        if p and p.exists() and p.stat().st_size:
            try:
                return pd.read_csv(p)
            except pd.errors.EmptyDataError:
                return pd.DataFrame()
        return pd.DataFrame()

    y = read("summary_by_year")
    m = read("summary_by_month")
    w = read("summary_by_week")
    pc = read("summary_by_postcode_district")
    ty = read("summary_by_property_type_year")
    latest = read("latest_sales")
    hv = read("high_value_sales")

    latest_period = "—"
    total_tx = 0
    median_latest = np.nan
    total_value = np.nan
    if not df.empty:
        latest_period = str(df["transfer_date"].max().date())
        total_tx = len(df)
        median_latest = df["price"].median()
        total_value = df["price"].sum()

    # Signals
    signals = []
    if len(m) >= 2:
        last = m.iloc[-1]
        prev = m.iloc[-2]
        signals.append(f"Latest month median price: {money(last.get('median_price'))} ({pct(last.get('median_mom_pct'))} month-on-month).")
        signals.append(f"Latest month transactions: {num(last.get('transactions'))} ({pct(last.get('transactions_mom_pct'))} month-on-month).")
    if len(y) >= 2:
        last_y = y.iloc[-1]
        signals.append(f"Current/last year median: {money(last_y.get('median_price'))}; transactions: {num(last_y.get('transactions'))}.")
    if not pc.empty:
        top_area = pc.iloc[0]
        signals.append(f"Highest recorded transaction volume area: {top_area.get('postcode_district')} with {num(top_area.get('transactions'))} sales.")
    if not hv.empty:
        signals.append(f"High-value monitor: {len(hv):,} sales at or above the configured threshold in this build.")
    if not signals:
        signals.append("No signals generated yet; run the workflow with at least one populated year.")

    css = """
:root{--ink:#172033;--muted:#5d6678;--line:#d9e0ea;--bg:#f6f8fb;--card:#fff;--accent:#1558d6;--accent2:#0f766e}
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:var(--ink);background:var(--bg);line-height:1.55}
.header{background:linear-gradient(135deg,#0f172a,#1d4ed8);color:#fff;padding:28px 18px}.wrap{max-width:1180px;margin:0 auto}
.nav{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}.nav a{color:#fff;text-decoration:none;border:1px solid rgba(255,255,255,.35);padding:8px 11px;border-radius:999px;font-weight:700;font-size:14px}
.hero{display:grid;grid-template-columns:1.3fr .7fr;gap:18px;align-items:end}.hero h1{font-size:clamp(30px,5vw,58px);line-height:1;margin:12px 0}.hero p{font-size:18px;color:#dbeafe;max-width:760px}
main{padding:22px 18px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:0 8px 24px rgba(15,23,42,.06)}.card h2,.card h3{margin-top:0}
.kpi{font-size:28px;font-weight:900;margin-top:4px}.muted{color:var(--muted)}.pill{display:inline-block;background:#e0ecff;color:#123e8a;border-radius:999px;padding:4px 9px;font-weight:800;font-size:12px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}.one{margin-top:14px}
.data-table{width:100%;border-collapse:collapse;font-size:14px}.data-table th{text-align:left;background:#eef3fb}.data-table th,.data-table td{border-bottom:1px solid var(--line);padding:8px;vertical-align:top}.data-table tr:hover{background:#fafcff}
.notice{border-left:5px solid var(--accent2);background:#eefdf8}.footer{padding:30px 18px;color:#dbeafe;background:#0f172a;margin-top:30px}.footer a{color:#fff}
.searchbox{width:100%;padding:12px;border:1px solid var(--line);border-radius:12px;margin:8px 0 14px}
@media(max-width:850px){.hero,.grid,.two{grid-template-columns:1fr}.nav{gap:6px}.nav a{font-size:13px;padding:7px 9px}.card{padding:14px}}
"""
    (site_dir / "assets" / "style.css").write_text(css, encoding="utf-8")

    js = """
function filterTable(inputId, tableSelector){
  const input=document.getElementById(inputId); if(!input) return;
  input.addEventListener('input',()=> {
    const q=input.value.toLowerCase();
    document.querySelectorAll(tableSelector+' tbody tr').forEach(tr=>{
      tr.style.display = tr.innerText.toLowerCase().includes(q) ? '' : 'none';
    });
  });
}
document.addEventListener('DOMContentLoaded',()=>filterTable('areaSearch','.area-table'));
"""
    (site_dir / "assets" / "app.js").write_text(js, encoding="utf-8")

    nav = """
<nav class="nav">
<a href="index.html">Dashboard</a>
<a href="weekly.html">Weekly highlights</a>
<a href="monthly.html">Monthly</a>
<a href="annual.html">Annual</a>
<a href="areas.html">Area explorer</a>
<a href="methodology.html">Methodology</a>
</nav>
"""
    def page(name, heading, body):
        html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{heading} | {brand}</title>
<meta name="description" content="{cfg['project'].get('description','UK property sales intelligence.')}">
<link rel="stylesheet" href="assets/style.css">
<script src="assets/app.js" defer></script>
</head><body>
<header class="header"><div class="wrap">
<div class="pill">{brand}</div>
<div class="hero"><div><h1>{heading}</h1><p>{title} for postcode prefix {", ".join(prefixes)}. Build time: {run_utc}.</p></div>
<div class="card" style="background:rgba(255,255,255,.1);border-color:rgba(255,255,255,.25)"><strong>Latest sale date</strong><div class="kpi">{latest_period}</div><span>Source: HM Land Registry Price Paid Data</span></div></div>
{nav}
</div></header>
<main><div class="wrap">{body}</div></main>
<footer class="footer"><div class="wrap"><p>{attribution}</p><p class="muted">Generated by the LandRegistry26 GitHub workflow. Price paid information is property-related, not personal information.</p></div></footer>
</body></html>"""
        (site_dir / name).write_text(html, encoding="utf-8")

    kpis = f"""
<section class="grid">
<div class="card"><span class="muted">Transactions</span><div class="kpi">{num(total_tx)}</div></div>
<div class="card"><span class="muted">Median price</span><div class="kpi">{money(median_latest)}</div></div>
<div class="card"><span class="muted">Total recorded value</span><div class="kpi">{money(total_value)}</div></div>
<div class="card"><span class="muted">Postcode prefixes</span><div class="kpi">{", ".join(prefixes)}</div></div>
</section>
<section class="two">
<div class="card notice"><h2>AI-ready highlights</h2><ul>{''.join(f'<li>{s}</li>' for s in signals)}</ul></div>
<div class="card"><h2>Latest sales sample</h2>{html_table(latest, 10)}</div>
</section>
<section class="two">
<div class="card"><h2>Top postcode districts</h2>{html_table(pc, 15)}</div>
<div class="card"><h2>Annual trend</h2>{html_table(y, 15)}</div>
</section>
"""
    page("index.html", "Property sales intelligence", kpis)

    page("weekly.html", "Weekly highlights", f"""
<section class="card"><h2>Weekly market movement</h2><p class="muted">Use this page for short-cycle pattern detection. Weekly figures can be lumpy because registration dates lag transaction dates.</p>{html_table(w.tail(30).sort_values('week', ascending=False) if not w.empty else w, 30)}</section>
<section class="card one"><h2>Most recent sales</h2>{html_table(latest, 40)}</section>
""")

    page("monthly.html", "Monthly observations", f"""
<section class="card"><h2>Monthly sales and prices</h2>{html_table(m.tail(36).sort_values('month', ascending=False) if not m.empty else m, 36)}</section>
<section class="card one"><h2>Recommendations</h2><p>Look for districts where transaction volume rises while median price remains below the regional median. Treat the newest month cautiously until later registrations arrive.</p></section>
""")

    page("annual.html", "Annual observations", f"""
<section class="card"><h2>Annual sales and prices</h2>{html_table(y.sort_values('year', ascending=False) if not y.empty else y, 30)}</section>
<section class="card one"><h2>Property type by year</h2>{html_table(ty.tail(80).sort_values(['year','property_type_label'], ascending=[False, True]) if not ty.empty else ty, 80)}</section>
""")

    area = pc.copy()
    page("areas.html", "Area explorer", f"""
<section class="card"><h2>Postcode district search</h2><input id="areaSearch" class="searchbox" placeholder="Search area, e.g. BN7, BN1, Brighton, Lewes...">{html_table(area, 100).replace('class="data-table"', 'class="data-table area-table"')}</section>
<section class="card one"><h2>High-value monitor</h2>{html_table(hv, 50)}</section>
""")

    page("methodology.html", "Methodology", f"""
<section class="card"><h2>Method</h2>
<p>The workflow downloads yearly HM Land Registry Price Paid CSV files, applies postcode prefix filtering, optionally keeps standard transactions only, and produces weekly, monthly, annual and postcode-district summaries.</p>
<h3>Important limitations</h3>
<ul>
<li>Registration lag means the latest month and week are provisional.</li>
<li>Price Paid Data excludes sales that were not for value and sales not lodged with HM Land Registry.</li>
<li>Address fields are used only for residential property price information display and analysis.</li>
<li>This site is analytical support, not valuation, legal, mortgage or investment advice.</li>
</ul>
<h3>Generated downloads</h3>
<ul>
{''.join(f'<li><a href="data/{Path(p).name}">{Path(p).name}</a></li>' for p in reports.values())}
</ul>
</section>
""")

    # sitemap and robots
    urls = ["index.html","weekly.html","monthly.html","annual.html","areas.html","methodology.html"]
    (site_dir / "sitemap.xml").write_text("<?xml version='1.0' encoding='UTF-8'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>" + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>", encoding="utf-8")
    (site_dir / "robots.txt").write_text("User-agent: *\nAllow: /\nSitemap: sitemap.xml\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yml")
    ap.add_argument("--years", default="")
    ap.add_argument("--postcode-prefixes", default="")
    ap.add_argument("--standard-only", choices=["true","false","config"], default="config")
    ap.add_argument("--skip-download", action="store_true", help="Use existing data/raw/pp-YEAR.csv files only.")
    args = ap.parse_args()

    cfg = read_config(Path(args.config))
    years = parse_years(args.years, cfg)
    prefixes = normalise_prefixes(args.postcode_prefixes, cfg)
    standard_only = bool(cfg["analysis"].get("standard_only", True)) if args.standard_only == "config" else args.standard_only == "true"

    raw_dir = Path("data/raw")
    processed_dir = Path("data/processed")
    reports_dir = Path("data/reports")
    site_dir = Path(cfg["site"].get("output_dir", "site"))
    mkdirs(raw_dir, processed_dir, reports_dir, site_dir)

    frames = []
    manifests = []
    for year in years:
        csv_path = raw_dir / f"pp-{year}.csv"
        if not args.skip_download:
            csv_path = ensure_year_csv(year, cfg, raw_dir)
        elif not csv_path.exists():
            raise FileNotFoundError(f"Missing {csv_path} while --skip-download was set.")
        df_year = load_filter_year(csv_path, prefixes, standard_only)
        out_parquet = processed_dir / f"pp_{'_'.join(prefixes).lower()}_standard_{year}.parquet"
        try:
            df_year.to_parquet(out_parquet, index=False)
            year_output = str(out_parquet)
        except Exception as exc:
            year_csv = out_parquet.with_suffix(".csv")
            df_year.to_csv(year_csv, index=False)
            year_output = str(year_csv)
            print(f"Parquet write unavailable, wrote CSV instead: {exc}")
        frames.append(df_year)
        manifests.append({"year": year, "raw_csv": str(csv_path), "rows_after_filter": int(len(df_year)), "processed": year_output})
        print(f"{year}: {len(df_year):,} filtered rows")

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not df.empty:
        all_parquet = processed_dir / f"pp_{'_'.join(prefixes).lower()}_standard_all_years.parquet"
        try:
            df.to_parquet(all_parquet, index=False)
        except Exception as exc:
            all_csv = all_parquet.with_suffix(".csv")
            df.to_csv(all_csv, index=False)
            print(f"Parquet write unavailable, wrote CSV instead: {exc}")
    reports = make_outputs(
        df,
        reports_dir,
        int(cfg["analysis"].get("high_value_threshold", 1_000_000)),
        int(cfg["analysis"].get("top_n", 15)),
    )

    manifest = {
        "project": cfg["project"]["name"],
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "years": years,
        "postcode_prefixes": prefixes,
        "standard_only": standard_only,
        "rows": int(len(df)),
        "outputs": {k: str(v) for k, v in reports.items()},
        "year_manifests": manifests,
        "attribution": cfg["site"].get("footer_attribution"),
    }
    (reports_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_site(df, reports, cfg, prefixes, site_dir, reports_dir)

    # no blank page guard
    for page in ["index.html","weekly.html","monthly.html","annual.html","areas.html","methodology.html"]:
        p = site_dir / page
        if not p.exists() or p.stat().st_size < 800:
            raise RuntimeError(f"Blank or missing page guard failed: {p}")

    print("Build complete.")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
