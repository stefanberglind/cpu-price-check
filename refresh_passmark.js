#!/usr/bin/env node
/*
 * Refresh the PassMark CPU database used by cpu_price_check.py.
 *
 * PassMark's mega list renders its full table client-side (DataTables), and
 * its /data/ endpoint returns nothing to plain HTTP clients. So we drive a
 * real headless Chrome via the DevTools protocol and read the DataTables
 * in-memory rows (name, id, cpumark, thread, tdp, cores, socket, samples).
 *
 * Usage:  node refresh_passmark.js [outfile]      (default: passmark_raw.json)
 *
 * Requires Node >= 18 (global WebSocket + fetch) and Chrome/Chromium.
 */

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const OUT = process.argv[2] || 'passmark_raw.json';
const PORT = 9341;
const UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ' +
           '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';
const URL = 'https://www.cpubenchmark.net/CPU_mega_page.html';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findChrome() {
  const names = ['google-chrome', 'google-chrome-stable', 'chromium',
                 'chromium-browser', 'chrome'];
  const pathDirs = (process.env.PATH || '').split(path.delimiter);
  for (const n of names) {
    for (const d of pathDirs) {
      const p = path.join(d, n);
      try { if (fs.existsSync(p)) return p; } catch (_) {}
    }
  }
  throw new Error('no Chrome/Chromium found on PATH');
}

const chrome = spawn(findChrome(), [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
  '--disable-blink-features=AutomationControlled',
  '--user-agent=' + UA, '--window-size=1366,900',
  '--user-data-dir=/tmp/passmark_refresh_' + Date.now(),
  '--remote-debugging-port=' + PORT, 'about:blank',
], { stdio: 'ignore' });

async function waitPort() {
  for (let i = 0; i < 100; i++) {
    try { const r = await fetch(`http://127.0.0.1:${PORT}/json/version`); if (r.ok) return; } catch (_) {}
    await sleep(100);
  }
  throw new Error('Chrome DevTools port did not open');
}

function cdp(ws) {
  let id = 0;
  const pending = new Map();
  const events = new Map();
  ws.addEventListener('message', (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    } else if (m.method && events.has(m.method)) {
      events.get(m.method).forEach((f) => f(m.params));
    }
  });
  return {
    send: (method, params = {}) => new Promise((res, rej) => {
      const i = ++id; pending.set(i, { res, rej });
      ws.send(JSON.stringify({ id: i, method, params }));
    }),
    on: (method, f) => {
      if (!events.has(method)) events.set(method, []);
      events.get(method).push(f);
    },
  };
}

(async () => {
  try {
    await waitPort();
    const target = await (await fetch(
      `http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' })).json();
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      ws.addEventListener('open', res);
      ws.addEventListener('error', rej);
    });
    const c = cdp(ws);
    await c.send('Page.enable');
    await c.send('Runtime.enable');
    const loaded = new Promise((res) => c.on('Page.loadEventFired', res));
    await c.send('Page.navigate', { url: URL });
    await Promise.race([loaded, sleep(12000)]);

    let n = 0;
    for (let i = 0; i < 30; i++) {
      await sleep(1000);
      const r = await c.send('Runtime.evaluate', {
        expression: `(window.jQuery && jQuery.fn.DataTable && jQuery('#cputable').length)`
          + ` ? (() => { try { return jQuery('#cputable').DataTable().rows().data().toArray().length; }`
          + ` catch (e) { return -1; } })() : -2`,
        returnByValue: true,
      });
      n = r.result.value;
      if (n > 100) break;
    }
    if (!(n > 0)) throw new Error('DataTable never populated (got ' + n + ')');

    const r = await c.send('Runtime.evaluate', {
      expression: `JSON.stringify(jQuery('#cputable').DataTable().rows().data().toArray())`,
      returnByValue: true,
    });
    fs.writeFileSync(OUT, r.result.value);
    console.log(`wrote ${OUT} (${n} rows)`);
    ws.close();
  } catch (err) {
    console.error('ERROR:', err.message);
    process.exitCode = 1;
  } finally {
    chrome.kill('SIGKILL');
  }
})();
