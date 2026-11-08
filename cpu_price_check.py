#!/usr/bin/env python3
"""Weekly AM4 CPU price + performance/cost checker.

Scrapes the first page of idealo.de's "Sockel AM4 CPU/Prozessor" category
with a real headless Chrome (idealo blocks plain HTTP clients), looks up each
listing's PassMark scores in the bundled offline database, then prints a
price / performance-per-EUR table and records a price snapshot so weekly runs
show price deltas.

Typical use:
    ./cpu_price_check.py                 # best upgrade value first (default)
    ./cpu_price_check.py --csv out.csv    # also write CSV
    ./cpu_price_check.py --sort price     # price|value|multi|single|name
    ./cpu_price_check.py --all            # include non-upgrades too
    ./cpu_price_check.py --html page.html # parse a saved DOM (no network)

Benchmarks are static (passmark_db.json) so weekly runs are fast and never
hit PassMark. Refresh that DB occasionally with:
    ./cpu_price_check.py --refresh-benchmarks     # needs node + Chrome

Setup: needs Python 3.10+, Chrome/Chromium, and the files in this folder
(cpu_price_check.py, passmark_db.json). Node is only needed to refresh
benchmarks.
"""

from __future__ import annotations

import argparse
import csv
import html as htmlmod
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_URL = "https://www.idealo.de/preisvergleich/ProductCategory/3019F2196403.html"
PASSMARK_DB = os.path.join(HERE, "passmark_db.json")
HISTORY_FILE = os.path.join(HERE, "price_history.json")

BASELINE_MODEL = "AMD Ryzen 5 3600"
BASELINE_FALLBACK = {"mark": 17645, "single": 2557}

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# ---------------------------------------------------------------------------
# idealo fetch (headless Chrome --dump-dom)
# ---------------------------------------------------------------------------

CHROME_CANDIDATES = ["google-chrome", "google-chrome-stable", "chromium",
                     "chromium-browser", "chrome"]


def find_chrome() -> str | None:
    for name in CHROME_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def fetch_idealo_html(url: str, timeout: int = 28) -> str:
    """Render the page with headless Chrome and return the DOM.

    idealo serves a bot wall to plain HTTP clients; a real Chrome with
    automation-control disabled gets the normal page.
    """
    chrome = find_chrome()
    if not chrome:
        sys.exit("error: no Chrome/Chromium found. Install google-chrome or "
                 "use --html FILE with a page you saved manually.")
    profile = tempfile.mkdtemp(prefix="cpuchk_")
    cmd = [
        chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
        "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled",
        "--user-agent=" + UA, "--window-size=1366,900",
        "--user-data-dir=" + profile, "--virtual-time-budget=20000",
        "--dump-dom", url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        sys.exit(f"error: Chrome timed out after {timeout}s fetching idealo.")
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    dom = proc.stdout
    if "sr-resultItemTile" not in dom:
        sys.exit("error: idealo page did not contain product tiles "
                 "(blocked or layout changed). Try again or use --html.")
    return dom


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def clean_text(fragment: str) -> str:
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = htmlmod.unescape(fragment).replace("\xa0", " ")
    return re.sub(r"\s+", " ", fragment).strip()


def parse_price(text: str) -> float | None:
    m = re.search(r"(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})\s*€", text)
    if not m:
        return None
    return float(m.group(1).replace(".", "") + "." + m.group(2))


def normalize_model(title: str) -> str:
    """'AMD Ryzen 7 5700X Boxed (Wraith Stealth)' -> 'AMD Ryzen 7 5700X'."""
    name = re.sub(r"\([^)]*\)", " ", title)          # drop parentheticals
    name = re.sub(r"\b10th Anniversary Edition\b", " ", name, flags=re.I)
    name = re.sub(r"\s+(Tray|Boxed|Box|WOF|OEM|MPK)\b.*$", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip(" -,/")
    return name


def parse_products(dom: str) -> list[dict]:
    products: list[dict] = []
    pieces = re.split(r"sr-resultList__item_", dom)
    for piece in pieces[1:]:
        link = re.search(
            r'OffersOfProduct/(\d+)_[^"]*"[^>]*>\s*'
            r'<div[^>]*sr-productSummary__title[^>]*>(.*?)</div>', piece, re.S)
        if not link:
            continue
        pid = link.group(1)
        title = clean_text(link.group(2))

        price_m = re.search(r'sr-detailedPriceInfo__price_[^"]*">(.*?)</div>',
                            piece, re.S)
        price = parse_price(clean_text(price_m.group(1))) if price_m else None

        offers_m = re.search(r'sr-detailedPriceInfo__offerCount_[^"]*">\s*(\d+)',
                             piece)
        offers = int(offers_m.group(1)) if offers_m else None

        desc_m = re.search(r'sr-productSummary__description[^>]*>(.*?)</div>',
                           piece, re.S)
        desc = clean_text(desc_m.group(1)) if desc_m else ""

        cores = None
        m = re.search(r"Anzahl Prozessorkerne\s*(\d+)", desc)
        if m:
            cores = int(m.group(1))
        else:
            m = re.search(r"(\d+)\s*Kern Prozessor", desc)
            if m:
                cores = int(m.group(1))
            else:
                word = re.search(r"(Dual|Quad|Hexa|Octa)-Core", desc, re.I)
                if word:
                    cores = {"dual": 2, "quad": 4, "hexa": 6, "octa": 8}[
                        word.group(1).lower()]

        threads = None
        m = re.search(r"Anzahl Threads\s*(\d+)", desc)
        if m:
            threads = int(m.group(1))

        tdp = None
        m = re.search(r"(\d+)\s*Watt", desc)
        if m:
            tdp = int(m.group(1))

        socket = None
        m = re.search(r"Sockel\s+([A-Za-z0-9+/]+)", desc)
        if m:
            socket = m.group(1)

        cooler = None
        if re.search(r"ohne K.hler", desc):
            cooler = "no"
        elif re.search(r"mit K.hler", desc):
            cooler = "yes"

        products.append({
            "id": pid,
            "title": title,
            "model": normalize_model(title),
            "price": price,
            "offers": offers,
            "cores": cores,
            "threads": threads,
            "tdp": tdp,
            "socket": socket,
            "cooler": cooler,
            "url": f"https://www.idealo.de/preisvergleich/OffersOfProduct/{pid}",
        })
    return products


# ---------------------------------------------------------------------------
# PassMark database
# ---------------------------------------------------------------------------

def _to_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _norm_row(r: dict) -> dict | None:
    """Accept both trimmed rows (mark/single) and raw PassMark rows
    (cpumark/thread), returning one canonical schema."""
    name = (r.get("name") or "").strip()
    mark = _to_int(r.get("mark", r.get("cpumark")))
    single = _to_int(r.get("single", r.get("thread")))
    if not name or mark is None or single is None:
        return None
    return {
        "id": str(r.get("id")) if r.get("id") is not None else None,
        "name": name,
        "mark": mark,
        "single": single,
        "tdp": _to_int(r.get("tdp")),
        "cores": _to_int(r.get("cores")),
        "socket": (r.get("socket") or "").strip() or None,
        "samples": _to_int(r.get("samples")) or 0,
    }


class BenchmarkDB:
    def __init__(self, rows: list[dict]):
        best: dict[str, dict] = {}
        for raw in rows:
            r = _norm_row(raw)
            if r is None:
                continue
            cur = best.get(r["name"])
            if cur is None or r["samples"] > cur["samples"]:
                best[r["name"]] = r
        self.by_name = best
        self.lower = {n.lower(): n for n in best}
        self.names = sorted(best, key=len, reverse=True)

    def lookup(self, model: str) -> dict | None:
        if model in self.by_name:
            return self.by_name[model]
        hit = self.lower.get(model.lower())
        if hit:
            return self.by_name[hit]
        low = model.lower()
        cands = [n for n in self.names
                 if n.lower().startswith(low + " ") or low.startswith(n.lower() + " ")]
        if not cands:
            return None
        cands.sort(key=lambda n: (-(self.by_name[n].get("samples") or 0), len(n)))
        return self.by_name[cands[0]]


def load_benchmarks() -> BenchmarkDB:
    try:
        with open(PASSMARK_DB, encoding="utf-8") as fh:
            rows = json.load(fh)
    except FileNotFoundError:
        sys.exit(f"error: {PASSMARK_DB} missing. Run "
                 f"`{os.path.basename(__file__)} --refresh-benchmarks`.")
    return BenchmarkDB(rows)


def attach_benchmarks(products: list[dict], db: BenchmarkDB) -> None:
    for p in products:
        b = db.lookup(p["model"])
        p["bench"] = b
        if b:
            if p["cores"] is None:
                p["cores"] = b.get("cores")
            if p["tdp"] is None:
                p["tdp"] = b.get("tdp")


def refresh_benchmarks() -> int:
    js = os.path.join(HERE, "refresh_passmark.js")
    if not os.path.exists(js):
        sys.exit(f"error: {js} not found.")
    if not shutil.which("node"):
        sys.exit("error: node (>=18) is required to refresh benchmarks.")
    raw = os.path.join(HERE, "passmark_raw.json")
    print("# refreshing PassMark database via headless Chrome ...",
          file=sys.stderr)
    proc = subprocess.run(["node", js, raw], timeout=90)
    if proc.returncode != 0:
        sys.exit("error: refresh failed.")
    with open(raw, encoding="utf-8") as fh:
        rows = json.load(fh)
    db = BenchmarkDB(rows)
    out = sorted(db.by_name.values(), key=lambda r: r["name"].lower())
    with open(PASSMARK_DB, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    os.remove(raw)
    print(f"# wrote {PASSMARK_DB} ({len(out)} CPUs)", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# presentation
# ---------------------------------------------------------------------------

def build_rows(products: list[dict], baseline: dict) -> list[dict]:
    rows = []
    b_mark = baseline.get("mark") or BASELINE_FALLBACK["mark"]
    for p in products:
        b = p.get("bench") or {}
        multi = b.get("mark")
        single = b.get("single")
        tdp = p["tdp"] if p["tdp"] is not None else b.get("tdp")
        price = p["price"]
        mark_per_eur = multi / price if (multi and price) else None
        if multi is not None:
            uplift = (multi / b_mark - 1) * 100
            extra_per_eur = (multi - b_mark) / price if price else None
        else:
            uplift = None
            extra_per_eur = None
        rows.append({
            "model": p["model"],
            "title": p["title"],
            "cores": p["cores"],
            "tdp": tdp,
            "price": price,
            "offers": p["offers"],
            "multi": multi,
            "single": single,
            "mark_per_eur": mark_per_eur,
            "uplift": uplift,
            "extra_per_eur": extra_per_eur,
            "cooler": p["cooler"],
            "url": p["url"],
        })
    return rows


def print_table(rows: list[dict], deltas: dict[str, float | None]) -> None:
    header = (f"{'CPU':<30} {'C':>2} {'W':>4} {'Price €':>9} {'Off':>4} "
              f"{'Mark':>7} {'Single':>7} {'Mark/€':>7} {'vs3600':>7} "
              f"{'Extra/€':>8} {'Δ€':>7}")
    print(header)
    print("-" * len(header))
    for r in rows:
        cores = "" if r["cores"] is None else str(r["cores"])
        tdp = "" if r["tdp"] is None else str(r["tdp"])
        price = "—" if r["price"] is None else f"{r['price']:.2f}"
        offers = "" if r["offers"] is None else str(r["offers"])
        multi = "—" if r["multi"] is None else f"{r['multi']:,}"
        single = "—" if r["single"] is None else f"{r['single']:,}"
        mpe = "—" if r["mark_per_eur"] is None else f"{r['mark_per_eur']:.0f}"
        uplift = "—" if r["uplift"] is None else f"{r['uplift']:+.0f}%"
        extra = "—" if r["extra_per_eur"] is None else f"{r['extra_per_eur']:.0f}"
        d = deltas.get(r["model"])
        if d is None:
            delta = ""
        elif abs(d) < 0.005:
            delta = "="
        else:
            delta = f"{d:+.2f}"
        print(f"{r['model'][:30]:<30} {cores:>2} {tdp:>4} {price:>9} {offers:>4} "
              f"{multi:>7} {single:>7} {mpe:>7} {uplift:>7} {extra:>8} {delta:>7}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL, help="idealo category URL")
    ap.add_argument("--html", help="parse a saved DOM file instead of fetching")
    ap.add_argument("--sort", default="value",
                    choices=["price", "value", "multi", "single", "name"],
                    help="sort order (default: value = best Extra/€ first)")
    ap.add_argument("--all", action="store_true",
                    help="also show CPUs that do not outperform the current one")
    ap.add_argument("--csv", help="write the table to a CSV file")
    ap.add_argument("--json", help="write the raw snapshot to a JSON file")
    ap.add_argument("--min-offers", type=int, default=0,
                    help="hide listings with fewer than N offers")
    ap.add_argument("--refresh-benchmarks", action="store_true",
                    help="re-download the PassMark CPU database and exit")
    args = ap.parse_args()

    if args.refresh_benchmarks:
        return refresh_benchmarks()

    if args.html:
        with open(args.html, encoding="utf-8") as fh:
            dom = fh.read()
    else:
        dom = fetch_idealo_html(args.url)

    products = parse_products(dom)
    if not products:
        sys.exit("error: parsed 0 products; idealo layout may have changed.")
    if args.min_offers:
        products = [p for p in products if (p["offers"] or 0) >= args.min_offers]

    db = load_benchmarks()
    attach_benchmarks(products, db)
    baseline = db.lookup(BASELINE_MODEL) or BASELINE_FALLBACK

    # price deltas vs the previous recorded run (per base model, min price)
    try:
        with open(HISTORY_FILE, encoding="utf-8") as fh:
            history = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        history = {"runs": []}
    prev_prices: dict[str, float] = {}
    if history["runs"]:
        prev_prices = history["runs"][-1].get("prices", {})
    curr_prices: dict[str, float] = {}
    for p in products:
        if p["price"] is not None:
            curr_prices[p["model"]] = min(curr_prices.get(p["model"], 1e9),
                                          p["price"])
    deltas: dict[str, float | None] = {
        m: (curr_prices[m] - prev_prices[m]) if m in prev_prices else None
        for m in curr_prices
    }

    rows = build_rows(products, baseline)
    b_mark = baseline.get("mark") or BASELINE_FALLBACK["mark"]
    if not args.all:
        rows = [r for r in rows
                if r["multi"] is None or r["multi"] >= b_mark]
    sorters = {
        "price": lambda r: (r["price"] is None, r["price"] or 0),
        "value": lambda r: (r["extra_per_eur"] is None,
                            -(r["extra_per_eur"] or 0), r["price"] or 0),
        "multi": lambda r: (r["multi"] is None, -(r["multi"] or 0)),
        "single": lambda r: (r["single"] is None, -(r["single"] or 0)),
        "name": lambda r: r["model"].lower(),
    }
    rows.sort(key=sorters[args.sort])

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"# idealo AM4 CPU prices — {stamp} — {len(rows)} listings, "
          f"baseline {BASELINE_MODEL} ({baseline.get('mark'):,} mark)")
    print_table(rows, deltas)
    print()
    print("Legend: Mark=PassMark CPU Mark (multithread), Mark/€=Mark per euro, "
          "vs3600=multi vs your Ryzen 5 3600,")
    print("        Extra/€=upgrade mark points bought per euro, Δ€=price change "
          "vs last saved run, W=TDP.")

    # persist snapshot
    today = datetime.now().strftime("%Y-%m-%d")
    runs = history.get("runs", [])
    if runs and runs[-1].get("date") == today:
        runs[-1] = {"date": today, "prices": curr_prices}
    else:
        runs.append({"date": today, "prices": curr_prices})
    history["runs"] = runs[-200:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
        json.dump(history, fh, indent=2, ensure_ascii=False)

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["model", "cores", "tdp_w", "price_eur", "offers",
                        "passmark_multi", "passmark_single", "mark_per_eur",
                        "vs_3600_pct", "extra_mark_per_eur", "price_delta_eur",
                        "cooler", "url"])
            for r in rows:
                d = deltas.get(r["model"])
                w.writerow([
                    r["model"], r["cores"], r["tdp"],
                    "" if r["price"] is None else f"{r['price']:.2f}",
                    r["offers"], r["multi"], r["single"],
                    "" if r["mark_per_eur"] is None else f"{r['mark_per_eur']:.1f}",
                    "" if r["uplift"] is None else f"{r['uplift']:.1f}",
                    "" if r["extra_per_eur"] is None else f"{r['extra_per_eur']:.1f}",
                    "" if d is None else f"{d:+.2f}",
                    r["cooler"], r["url"],
                ])
        print(f"# wrote {args.csv}", file=sys.stderr)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"date": stamp, "baseline": baseline, "rows": rows}, fh,
                      indent=2, ensure_ascii=False)
        print(f"# wrote {args.json}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
