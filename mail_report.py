#!/usr/bin/env python3
"""Run the CPU price checker and email its output.

Sends a multipart message: the plain-text table plus an HTML table so the
columns render properly in Gmail and other rich clients.

Configure SMTP in mail.conf (next to this file) or via CPU_MAIL_CONF.
Pass --dry-run to print the message instead of sending it.
"""

from __future__ import annotations

import json
import os
import smtplib
import subprocess
import sys
import tempfile
from datetime import datetime
from email.message import EmailMessage
from html import escape

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKER = os.path.join(HERE, "cpu_price_check.py")
CONF = os.environ.get("CPU_MAIL_CONF", os.path.join(HERE, "mail.conf"))


def fail(msg: str) -> int:
    print(f"mail_report: {msg}", file=sys.stderr)
    return 1


def _num(value, fmt: str | None = None) -> str:
    if value is None:
        return "—"
    return str(value) if fmt is None else format(value, fmt)


def _delta_cell(value) -> str:
    if value is None:
        return "—"
    if abs(value) < 0.005:
        return "="
    color = "#0a7d32" if value < 0 else "#b3261e"
    return f'<span style="color:{color}">{value:+.2f}</span>'


def render_html(data: dict) -> str:
    rows = data.get("rows", [])
    baseline = data.get("baseline", {})
    b_mark = baseline.get("mark")
    stamp = data.get("date", "")
    head = ("background:#1f2937;color:#ffffff;font-weight:600;"
            "text-align:left;padding:8px 10px;white-space:nowrap")
    num_head = head + ";text-align:right"
    cell = "padding:6px 10px;border-bottom:1px solid #e5e7eb;white-space:nowrap"
    num = cell + ";text-align:right;font-variant-numeric:tabular-nums"

    out = [
        '<html><body style="margin:0;padding:16px;'
        'font-family:Arial,Helvetica,sans-serif;color:#111827">',
        f'<h2 style="margin:0 0 4px;font-size:18px">AM4 CPU prices — {escape(stamp)}</h2>',
        f'<p style="margin:0 0 14px;color:#6b7280;font-size:13px">'
        f'{len(rows)} listings vs baseline {escape("Ryzen 5 3600")} '
        f'({b_mark:,} mark), best upgrade value first.</p>',
        '<table cellspacing="0" cellpadding="0" '
        'style="border-collapse:collapse;font-size:13px;'
        'border:1px solid #d1d5db;border-radius:6px">',
        "<thead><tr>",
        f'<th style="{head}">CPU</th>',
        f'<th style="{num_head}">C</th>',
        f'<th style="{num_head}">W</th>',
        f'<th style="{num_head}">Price €</th>',
        f'<th style="{num_head}">Off</th>',
        f'<th style="{num_head}">Mark</th>',
        f'<th style="{num_head}">Single</th>',
        f'<th style="{num_head}">Mark/€</th>',
        f'<th style="{num_head}">vs 3600</th>',
        f'<th style="{num_head}">Extra/€</th>',
        f'<th style="{num_head}">Δ€</th>',
        "</tr></thead><tbody>",
    ]
    for i, r in enumerate(rows):
        bg = "#ffffff" if i % 2 == 0 else "#f9fafb"
        name = escape(r.get("model") or "")
        url = r.get("url")
        name_html = (f'<a href="{escape(url)}" '
                     f'style="color:#1d4ed8;text-decoration:none">{name}</a>'
                     if url else name)
        uplift = r.get("uplift")
        uplift_html = "—" if uplift is None else (
            f'<span style="color:{"#0a7d32" if uplift > 0 else "#6b7280"}">'
            f'{uplift:+.0f}%</span>')
        tdp = r.get("tdp")
        tdp_html = "—" if tdp is None else (
            f'<b>{tdp}</b>' if tdp <= 65 else str(tdp))
        out.append(
            f'<tr style="background:{bg}">'
            f'<td style="{cell}">{name_html}</td>'
            f'<td style="{num}">{_num(r.get("cores"))}</td>'
            f'<td style="{num}">{tdp_html}</td>'
            f'<td style="{num}">{_num(r.get("price"), ".2f")}</td>'
            f'<td style="{num}">{_num(r.get("offers"))}</td>'
            f'<td style="{num}">{_num(r.get("multi"), ",")}</td>'
            f'<td style="{num}">{_num(r.get("single"), ",")}</td>'
            f'<td style="{num}">{_num(r.get("mark_per_eur"), ".0f")}</td>'
            f'<td style="{num}">{uplift_html}</td>'
            f'<td style="{num}"><b>{_num(r.get("extra_per_eur"), ".0f")}</b></td>'
            f'<td style="{num}">{_delta_cell(r.get("delta"))}</td>'
            "</tr>")
    out += [
        "</tbody></table>",
        '<p style="margin:14px 0 0;color:#6b7280;font-size:12px">'
        "Extra/€ = mark points gained over the 3600 per euro. "
        "Δ€ = price change vs the last saved run. "
        "Bold W = 65 W or lower.</p>",
        "</body></html>",
    ]
    return "".join(out)


def build_message(conf: dict, text: str, html: str | None) -> EmailMessage:
    to = conf["to"]
    sender = conf.get("from", conf["username"])
    msg = EmailMessage()
    msg["Subject"] = conf.get("subject",
                              f"AM4 CPU prices — {datetime.now():%Y-%m-%d}")
    msg["From"] = sender
    msg["To"] = ", ".join(to) if isinstance(to, list) else to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def send(conf: dict, msg: EmailMessage) -> None:
    host = conf["host"]
    port = int(conf.get("port", 587))
    timeout = int(conf.get("timeout", 30))
    if conf.get("ssl"):
        smtp = smtplib.SMTP_SSL(host, port, timeout=timeout)
    else:
        smtp = smtplib.SMTP(host, port, timeout=timeout)
        smtp.ehlo()
        if conf.get("starttls", True):
            smtp.starttls()
            smtp.ehlo()
    try:
        smtp.login(conf["username"], conf["password"])
        smtp.send_message(msg)
    finally:
        try:
            smtp.quit()
        except Exception:
            pass


def run_checker() -> tuple[str, dict | None, int]:
    fd, path = tempfile.mkstemp(prefix="cpu_prices_", suffix=".json")
    os.close(fd)
    try:
        proc = subprocess.run([sys.executable, CHECKER, "--json", path],
                              capture_output=True, text=True, timeout=120)
        text = proc.stdout
        if proc.returncode != 0:
            text = (text + "\n\n[checker stderr]\n" + proc.stderr).strip()
        data = None
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            data = None
        return text, data, proc.returncode
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main() -> int:
    dry_run = "--dry-run" in sys.argv[1:]
    try:
        with open(CONF, encoding="utf-8") as fh:
            conf = json.load(fh)
    except FileNotFoundError:
        return fail(f"missing config {CONF} (copy mail.conf.example)")
    except json.JSONDecodeError as exc:
        return fail(f"bad config {CONF}: {exc}")

    text, data, code = run_checker()
    html = render_html(data) if data else None
    msg = build_message(conf, text, html)

    if dry_run:
        print(f"To: {msg['To']}")
        print(f"Subject: {msg['Subject']}")
        print(f"Parts: {[p.get_content_type() for p in msg.walk()]}")
        print()
        print(text)
        if html:
            with open(os.path.join(HERE, "last_email.html"), "w",
                      encoding="utf-8") as fh:
                fh.write(html)
            print("\n[html preview written to last_email.html]")
        return code

    try:
        send(conf, msg)
    except Exception as exc:
        print(text)
        return fail(f"sending failed: {exc}")
    print(f"mail_report: sent to {msg['To']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
