# AM4 CPU price / performance checker

Weekly snapshot of **Socket AM4 CPU prices on idealo.de**, enriched with
PassMark scores, printed as a price / performance-per-EUR table. Each run
saves a price snapshot so the next run shows a `Δ€` column.

Built for a B450 (AM4, DDR4) system. The table covers every CPU on the first
page of idealo's AM4 category (new models appear automatically), collapsed to
**one row per model** — idealo's tray/boxed/OEM/anniversary packagings of the
same chip are merged, keeping the cheapest.

## Files

| File | Purpose |
| --- | --- |
| `cpu_price_check.py` | main script (stdlib only) |
| `passmark_db.json` | offline PassMark DB (~6,000 CPUs: mark, single, TDP, cores) |
| `refresh_passmark.js` | regenerates `passmark_db.json` (only needed occasionally) |
| `price_history.json` | created on first run; per-day price snapshots |
| `cron.log` | optional, if you use the cron example below |

## Requirements

- Python 3.10+
- **Google Chrome / Chromium** on `PATH` (idealo blocks plain HTTP clients, so a
  real headless browser is used)
- Node.js ≥ 18 — **only** for `--refresh-benchmarks`

## Usage

```sh
./cpu_price_check.py                 # fetch idealo, best upgrade value first
./cpu_price_check.py --sort price     # cheapest first (value|price|multi|single|name)
./cpu_price_check.py --all            # also show CPUs that are not upgrades
./cpu_price_check.py --csv prices.csv # also write CSV (semicolon-separated)
./cpu_price_check.py --json snap.json # also write raw JSON
./cpu_price_check.py --min-offers 10  # hide thin listings
./cpu_price_check.py --html page.html # parse a saved DOM (offline)
./cpu_price_check.py --refresh-benchmarks   # update PassMark DB (node + Chrome)
```

By default the table hides any CPU that does not beat the current Ryzen 5 3600
(17,645 multithread mark); pass `--all` to see everything. `--sort` is one of
`value` (default), `price`, `multi`, `single`, `name`. The `value` order ranks
by **Extra/€** — mark points gained over the 3600 per euro spent — highest first.

### Columns

- **C / W** — cores / TDP (watts)
- **Price €** — lowest offer on idealo
- **Off** — number of offers
- **Mark / Single** — PassMark CPU Mark (multithread) / single-thread
- **Mark/€** — raw efficiency (Mark ÷ price)
- **vs3600** — multithread vs your Ryzen 5 3600 (17,645 mark)
- **Extra/€** — upgrade value: `(Mark − 17,645) ÷ price` (default sort key)
- **Δ€** — price change vs the previous saved run

## Weekly cron + email

`mail_report.py` runs the checker and emails the table over SMTP (stdlib only,
no MTA needed). Configure it in `mail.conf` (chmod 600, gitignored) using
`mail.conf.example` as the template:

```json
{
  "host": "smtp.gmail.com",
  "port": 587,
  "starttls": true,
  "username": "you@gmail.com",
  "password": "GMAIL_APP_PASSWORD",
  "from": "you@gmail.com",
  "to": "you@gmail.com"
}
```

For Gmail, `password` must be an **App Password** (Google account → Security →
2-Step Verification → App passwords), not your normal login. Any SMTP provider
works; use `"ssl": true` for implicit TLS on port 465 instead of STARTTLS.

Test without sending:

```sh
./mail_report.py --dry-run
```

Crontab (Monday and Wednesday at 18:00 local time):

```cron
0 18 * * 1,3 cd /home/stefan/cpu-price-check && /usr/bin/python3 mail_report.py >> /home/stefan/cpu-price-check/cron.log 2>&1
```

Check `cron.log` if no mail arrives.

## Notes / design

- **Why headless Chrome?** `curl`/`requests` get a 403 bot wall from idealo.
  Chrome is launched with `--disable-blink-features=AutomationControlled`, a
  real UA and a throwaway profile, then `--dump-dom` captures the rendered
  first page (~36 listings; the remaining ~120 need a JS "show all" button,
  which we intentionally skip).
- **Benchmarks are offline** (`passmark_db.json`), so weekly runs are fast and
  never hammer PassMark. PassMark's *name-based* URL does fuzzy redirects
  (e.g. `5600` → `5600XT`), which is why the DB is built from PassMark's
  DataTables mega-list and looked up by exact name (with a safe
  prefix fallback for OEM suffixes like `5500F 6-Core`).
- **New CPU missing from the table?** Run `--refresh-benchmarks` once; if a
  model still shows `—`, it simply isn't in PassMark yet.
- Prices are Amazon/idealo lowest offers and change often; treat the table as
  a snapshot, not a quote.
