/** Browser acceptance against a disposable local owner server; no live orders. */
import assert from 'node:assert/strict';
import { mkdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { chromium } from 'playwright';

const base = process.env.TORINTEO_TEST_URL ?? 'http://127.0.0.1:8787';
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname), 'Browser tests require a disposable loopback server.');
const output = process.env.TORINTEO_SCREENSHOTS ?? path.join(os.tmpdir(), 'torinteo-markets-browser');
await mkdir(output, { recursive: true });
const launch = process.env.TORINTEO_BROWSER_LAUNCH_FILE ? JSON.parse(await readFile(process.env.TORINTEO_BROWSER_LAUNCH_FILE, 'utf8')) : {};
const browser = await chromium.launch({ headless: true, ...launch });
const page = await browser.newPage({ viewport: { width: 1536, height: 1024 }, deviceScaleFactor: 1 });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.setDefaultTimeout(12000);

async function createExperiment(kind, name) {
  await page.getByRole('button', { name: 'New experiment', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'New paper experiment' });
  await dialog.getByRole('radio', { name: kind === 'synthetic' ? /Guided walkthrough/ : /Market observations/ }).check();
  await dialog.getByLabel('Experiment name', { exact: true }).fill(name);
  await dialog.getByRole('button', { name: 'Create paper experiment', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  await page.getByLabel('Selected experiment').locator('option:checked').waitFor({ state: 'attached' });
}
async function detail() {
  const id = await page.getByLabel('Selected experiment').inputValue();
  const response = await page.request.get(`${base}/api/experiments/${id}`);
  assert.equal(response.status(), 200);
  return response.json();
}
async function noOverflow() {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const result = await page.evaluate(() => ({ body: document.body.scrollWidth, document: document.documentElement.scrollWidth, viewport: window.innerWidth }));
  assert.ok(Math.max(result.body, result.document) <= result.viewport + 1, `Page overflow: ${JSON.stringify(result)}`);
}

try {
  await page.goto(base, { waitUntil: 'networkidle' });
  await createExperiment('synthetic', 'Synthetic walkthrough');
  await page.getByRole('button', { name: 'Evaluate paper setup', exact: true }).waitFor();
  assert.equal((await detail()).decisions.length, 0);
  await page.screenshot({ path: path.join(output, 'desk-desktop.png'), fullPage: true });
  await noOverflow();
  await page.getByRole('button', { name: 'Evaluate paper setup', exact: true }).click();
  await page.getByRole('button', { name: 'Process next candle', exact: true }).waitFor();
  let state = await detail();
  assert.equal(state.decisions[0].call, 'LONG');
  assert.equal(state.orders[0].status, 'PENDING');
  await page.getByRole('button', { name: 'Process next candle', exact: true }).click();
  await page.getByRole('button', { name: 'Process exit candle', exact: true }).waitFor();
  state = await detail();
  assert.equal(state.orders[0].status, 'OPEN');
  assert.ok(state.orders[0].fees > 0);
  await page.reload({ waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'Process exit candle', exact: true }).click();
  await page.getByRole('button', { name: 'Evaluate blocked example', exact: true }).waitFor();
  state = await detail();
  assert.equal(state.orders[0].status, 'CLOSED');
  assert.equal(state.orders[0].exit_reason, 'STOP_LOSS');
  assert.ok(state.orders[0].net_pnl < 0);
  await page.getByRole('button', { name: 'Evaluate blocked example', exact: true }).click();
  await page.getByRole('button', { name: 'Inspect walkthrough audit', exact: true }).waitFor();
  state = await detail();
  assert.equal(state.decisions.length, 2);
  assert.equal(state.decisions[1].call, 'NO_TRADE');
  await page.getByRole('button', { name: 'Decisions', exact: true }).click();
  await page.getByLabel('Filter decision call').selectOption('NO_TRADE');
  assert.equal(await page.locator('tbody tr').count(), 1);
  await page.getByRole('button', { name: /^Inspect / }).click();
  await page.getByRole('dialog', { name: 'Decision evidence' }).waitFor();
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
  await page.getByRole('button', { name: 'Paper positions', exact: true }).click();
  await page.getByRole('button', { name: 'Closed', exact: true }).click();
  assert.equal(await page.locator('tbody tr').count(), 1);
  await page.screenshot({ path: path.join(output, 'positions-desktop.png'), fullPage: true });
  await page.getByRole('button', { name: 'Audit trail', exact: true }).click();
  assert.equal(await page.locator('tbody tr').count(), state.events.length);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export audit', exact: true }).click();
  const audit = await download;
  assert.ok(audit.suggestedFilename().startsWith('torinteo-paper-'));
  await page.getByRole('button', { name: 'Integrations', exact: true }).click();
  await page.getByRole('heading', { name: 'Torinteo paper engine', exact: true }).waitFor();
  assert.ok((await page.locator('main').innerText()).includes('No live exchange credentials'));

  // Capture a fresh reference state, then check the complete mobile surface.
  await createExperiment('synthetic', 'Synthetic walkthrough');
  await page.getByRole('button', { name: 'Evaluate paper setup', exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow();
  await page.screenshot({ path: path.join(output, 'desk-mobile.png'), fullPage: true });
  await page.getByRole('button', { name: 'Research & levels', exact: true }).click();
  await page.getByRole('heading', { name: 'Import synthetic evidence' }).waitFor();
  await noOverflow();
  await page.screenshot({ path: path.join(output, 'research-mobile.png'), fullPage: true });

  // A failed public feed must stay an explicit error, with blank market evidence.
  await page.setViewportSize({ width: 1536, height: 1024 });
  await page.route('**/api/market?**', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'Public market data is unavailable. Browser acceptance failure scenario.' }) }));
  await createExperiment('market', 'Empty market review');
  await page.getByRole('alert').filter({ hasText: 'Public market data is unavailable.' }).waitFor();
  await page.getByRole('button', { name: 'Research & levels', exact: true }).click();
  await page.getByRole('heading', { name: 'Major and opposing levels', exact: true }).waitFor();
  assert.equal(await page.getByPlaceholder('Enter reviewed price').first().inputValue(), '');
  const calendar = page.getByRole('group', { name: 'Calendar evidence' });
  assert.equal(await calendar.getByRole('combobox').inputValue(), 'unknown');
  assert.equal(await page.getByRole('button', { name: 'Evaluate paper setup', exact: true }).isEnabled(), false);
  await page.getByPlaceholder('Enter reviewed price').first().fill('123.45');
  await page.getByRole('button', { name: 'Save review draft', exact: true }).click();
  await page.getByRole('status').filter({ hasText: 'Draft saved in this browser.' }).waitFor();
  await page.reload({ waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'Research & levels', exact: true }).click();
  assert.equal(await page.getByPlaceholder('Enter reviewed price').first().inputValue(), '123.45');
  await page.screenshot({ path: path.join(output, 'research-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow();
  await page.unroute('**/api/market?**');
  assert.deepEqual(errors, []);
  process.stdout.write(JSON.stringify({ status: 'passed', screenshots: output, desktop: '1536x1024', mobile: '390x844', calls: state.decisions.map(item => item.call), order: state.orders[0].status, exit: state.orders[0].exit_reason, events: state.events.length, consoleErrors: errors.length }) + '\n');
} finally { await browser.close(); }
