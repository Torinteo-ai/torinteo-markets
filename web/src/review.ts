import type { Bias, Collection, Level, NewsEvent, Snapshot, SourceKind } from './types';

export const REVIEW_TIMEFRAMES = ['1M', '1w', '1d', '4h', '1h'] as const;
const BIASES = ['LONG', 'SHORT', 'NEUTRAL'];
const LEVEL_TIMEFRAMES = ['1M', '1w', 'historical'];
export interface DraftLevel {
  id: string; price: string; kind: Level['kind']; timeframe: Level['timeframe']; known_at: string; reference: string;
}
export interface WorkbenchDraft {
  version: 1; symbol: string; levels: DraftLevel[];
  reviews: Record<string, { bias: Bias; as_of: string; reference: string }>;
  news: { status: Snapshot['news']['status']; as_of: string; source: string; eventsText: string };
  saved_at: string;
}

export function draftKey(experiment: string, symbol: string) {
  return `torinteo-markets:review:v1:${encodeURIComponent(experiment)}:${encodeURIComponent(symbol)}`;
}
export function newLevel(index: number): DraftLevel {
  return { id: `level-${index}`, price: '', kind: index === 1 ? 'support' : 'resistance', timeframe: '1M', known_at: '', reference: '' };
}
export function createDraft(symbol: string): WorkbenchDraft {
  return {
    version: 1, symbol, levels: [newLevel(1), newLevel(2)],
    reviews: Object.fromEntries(REVIEW_TIMEFRAMES.map(tf => [tf, { bias: 'NEUTRAL' as Bias, as_of: '', reference: '' }])),
    news: { status: 'unknown', as_of: '', source: '', eventsText: '[]' }, saved_at: '',
  };
}
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function bounded(value: unknown, limit = 2048): value is string {
  return typeof value === 'string' && value.length <= limit;
}
function text(value: unknown, name: string, limit = 2048): string {
  if (!bounded(value, limit) || !value.trim()) throw new Error(`${name} needs a reference or value.`);
  return value.trim();
}
function finite(value: unknown, name: string, zero = false): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || (zero ? value < 0 : value <= 0)) {
    throw new Error(`${name} must be ${zero ? 'non-negative' : 'positive'} and finite.`);
  }
  return value;
}
function instant(value: unknown, name: string): number {
  if (!bounded(value, 80) || !/(Z|[+-]\d{2}:\d{2})$/.test(value)) throw new Error(`${name} needs an explicit timezone.`);
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) throw new Error(`${name} is not a valid date and time.`);
  return timestamp;
}
function before(value: unknown, name: string, cutoff: number): number {
  const timestamp = instant(value, name);
  if (timestamp > cutoff) throw new Error(`${name} is after the decision time.`);
  return timestamp;
}
function localUtc(value: string, name: string, cutoff: number): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,3})?)?$/.test(value)) {
    throw new Error(`${name} needs its original date and time in UTC.`);
  }
  const timestamp = before(`${value}Z`, name, cutoff);
  const canonical = new Date(timestamp).toISOString();
  if (canonical.slice(0, 16) !== value.slice(0, 16)) throw new Error(`${name} is not a valid calendar date.`);
  return canonical;
}

/** Browser storage is untrusted; partial drafts are allowed but their shape is bounded. */
export function readDraft(raw: string, symbol: string): WorkbenchDraft {
  if (raw.length > 150_000) throw new Error('The saved review draft is too large.');
  const value: unknown = JSON.parse(raw);
  if (!record(value) || value.version !== 1 || value.symbol !== symbol || !bounded(value.saved_at, 80)
    || !Array.isArray(value.levels) || value.levels.length < 2 || value.levels.length > 200
    || !record(value.reviews) || !record(value.news)) throw new Error('The saved review draft has an invalid shape.');
  const ids = new Set<string>();
  for (const level of value.levels) {
    if (!record(level) || !bounded(level.id, 120) || !level.id || ids.has(level.id)
      || !bounded(level.price, 64) || !['support', 'resistance'].includes(String(level.kind))
      || !LEVEL_TIMEFRAMES.includes(String(level.timeframe)) || !bounded(level.known_at, 80)
      || !bounded(level.reference)) throw new Error('A saved reviewed level is invalid.');
    ids.add(level.id);
  }
  for (const tf of REVIEW_TIMEFRAMES) {
    const review = value.reviews[tf];
    if (!record(review) || !BIASES.includes(String(review.bias)) || !bounded(review.as_of, 80)
      || !bounded(review.reference)) throw new Error('A saved timeframe review is invalid.');
  }
  if (!['unknown', 'clear', 'blocked'].includes(String(value.news.status)) || !bounded(value.news.as_of, 80)
    || !bounded(value.news.source) || !bounded(value.news.eventsText, 100_000)) throw new Error('The saved calendar review is invalid.');
  if (value.saved_at) instant(value.saved_at, 'Draft saved time');
  return value as unknown as WorkbenchDraft;
}

function validateEvents(value: unknown, asOf: number): NewsEvent[] {
  if (!Array.isArray(value) || value.length > 500) throw new Error('Calendar events must be an array with at most 500 entries.');
  for (const [index, event] of value.entries()) {
    const name = `Calendar event ${index + 1}`;
    if (!record(event) || typeof event.currency !== 'string' || !/^[A-Z]{3}$/.test(event.currency)
      || !['high', 'medium', 'low'].includes(String(event.impact))) throw new Error(`${name} needs a currency and valid impact.`);
    instant(event.starts_at, `${name} start time`);
    before(event.known_at, `${name} known-at time`, asOf);
    text(event.reference, name);
  }
  return value as NewsEvent[];
}

/** Validate the imported/assembled evidence; the server remains authoritative. */
export function validateSnapshot(value: unknown, kind: SourceKind, now = new Date()): Snapshot {
  if (!record(value) || value.schema_version !== 1 || value.timeframe !== '15m') throw new Error('Use a version 1 snapshot with 15-minute setup candles.');
  if (typeof value.symbol !== 'string' || !/^[A-Z0-9][A-Z0-9./:=_-]{0,39}$/.test(value.symbol)
    || !['crypto', 'forex', 'equity', 'index', 'commodity'].includes(String(value.asset_class))) throw new Error('The snapshot instrument is invalid.');
  if (typeof value.quote_currency !== 'string' || !/^[A-Z]{3,6}$/.test(value.quote_currency)) throw new Error('An explicit quote currency is required.');
  if (!record(value.source) || value.source.kind !== kind) throw new Error(`This experiment requires ${kind} evidence; source labels cannot be changed during import.`);
  text(value.source.provider, 'Source provider', 120); text(value.source.reference, 'Source');
  const asOf = before(value.as_of, 'Snapshot time', now.getTime());
  if (!record(value.quote)) throw new Error('A timestamped bid/ask quote is required.');
  const bid = finite(value.quote.bid, 'Bid'), ask = finite(value.quote.ask, 'Ask');
  if (ask < bid) throw new Error('The quote has crossed bid/ask prices.');
  before(value.quote.as_of, 'Quote time', asOf);
  if (!Array.isArray(value.bars) || value.bars.length < 5 || value.bars.length > 1000) throw new Error('Provide 5–1000 closed setup candles.');
  let previousClose: number | null = null;
  for (const [index, bar] of value.bars.entries()) {
    if (!record(bar)) throw new Error(`Candle ${index + 1} is invalid.`);
    const open = instant(bar.open_time, 'Candle open'), close = before(bar.close_time, 'Candle close', asOf);
    if (close - open !== 900_000 || (previousClose !== null && open !== previousClose)) throw new Error('Setup candles must be contiguous, ordered, closed 15-minute intervals.');
    previousClose = close;
    const o = finite(bar.open, 'Candle open price'), h = finite(bar.high, 'Candle high'),
      l = finite(bar.low, 'Candle low'), c = finite(bar.close, 'Candle close price');
    finite(bar.volume, 'Candle volume', true);
    if (l > Math.min(o, c) || h < Math.max(o, c)) throw new Error('Candle OHLC prices contradict one another.');
  }
  if (!Array.isArray(value.levels) || value.levels.length < 2 || value.levels.length > 200) throw new Error('Provide at least two reviewed major/opposing levels.');
  const ids = new Set<string>();
  for (const [index, level] of value.levels.entries()) {
    const name = `Level ${index + 1}`;
    if (!record(level) || !['support', 'resistance'].includes(String(level.kind))
      || !LEVEL_TIMEFRAMES.includes(String(level.timeframe))) throw new Error(`${name} needs a valid type and timeframe.`);
    const id = text(level.id, `${name} identity`, 120);
    if (ids.has(id)) throw new Error('Reviewed level identities must be unique.');
    ids.add(id); finite(level.price, `${name} price`); before(level.known_at, `${name} known-at time`, asOf); text(level.reference, name);
  }
  if (!record(value.reviews)) throw new Error('All five timeframe reviews are required.');
  for (const tf of REVIEW_TIMEFRAMES) {
    const review = value.reviews[tf];
    if (!record(review) || !BIASES.includes(String(review.bias))) throw new Error(`${tf} needs an explicit reviewed direction.`);
    before(review.as_of, `${tf} review time`, asOf); text(review.reference, `${tf} review`);
  }
  if (!record(value.news) || !['unknown', 'clear', 'blocked'].includes(String(value.news.status))) throw new Error('A calendar review status is required.');
  before(value.news.as_of, 'Calendar reviewed-at time', asOf); text(value.news.source, 'Calendar review');
  validateEvents(value.news.events, asOf);
  return value as unknown as Snapshot;
}

export function composeMarketSnapshot(collection: Collection | null, draft: WorkbenchDraft, now = new Date(),
  limits = { quoteSeconds: 60, snapshotSeconds: 120 }): Snapshot {
  if (!collection) throw new Error('Refresh market data before evaluating a setup.');
  if (collection.source_kind !== 'market' || collection.symbol !== draft.symbol || collection.timeframe !== '15m') {
    throw new Error('Load matching 15-minute market data for this review.');
  }
  const cutoff = now.getTime();
  const observed = before(collection.observed_at, 'Market receipt time', cutoff);
  const collected = before(collection.as_of, 'Candle collection time', observed);
  const quoteAt = before(collection.quote.as_of, 'Provider quote time', observed);
  if (cutoff - quoteAt > limits.quoteSeconds * 1000 || cutoff - collected > limits.snapshotSeconds * 1000) {
    throw new Error('Market observations are stale. Refresh data; your original review timestamps will be preserved.');
  }
  text(collection.quote.reference, 'Provider quote');
  const reviews = Object.fromEntries(REVIEW_TIMEFRAMES.map(tf => [tf, {
    bias: draft.reviews[tf].bias,
    as_of: localUtc(draft.reviews[tf].as_of, `${tf} reviewed-at time`, cutoff),
    reference: text(draft.reviews[tf].reference, `${tf} review`),
  }]));
  let events: unknown;
  try { events = JSON.parse(draft.news.eventsText); }
  catch { throw new Error('Calendar events need valid JSON. Use [] when the reviewed calendar contains no relevant events.'); }
  const source = { provider: collection.source, kind: 'market' as const, reference: collection.reference,
    collected_at: collection.observed_at, data_as_of: collection.as_of, point_in_time_archive: false };
  const result: Snapshot = {
    schema_version: 1, symbol: collection.symbol, asset_class: collection.asset_class,
    quote_currency: collection.quote_currency, timeframe: '15m', as_of: now.toISOString(),
    source,
    quote: { ...collection.quote }, bars: collection.candles.map(bar => ({ ...bar })),
    levels: draft.levels.map((level, index) => ({
      ...level, price: finite(Number(level.price), `Level ${index + 1} price`),
      known_at: localUtc(level.known_at, `Level ${index + 1} known-at time`, cutoff),
      reference: text(level.reference, `Level ${index + 1}`),
    })),
    reviews, news: { status: draft.news.status,
      as_of: localUtc(draft.news.as_of, 'Calendar reviewed-at time', cutoff),
      source: text(draft.news.source, 'Calendar review'), events: validateEvents(events, cutoff) },
  };
  return validateSnapshot(result, 'market', now);
}

export function parseSnapshotImport(raw: string, kind: SourceKind, now = new Date()): Snapshot {
  if (raw.length > 2 * 1024 * 1024) throw new Error('Snapshot JSON exceeds 2 MiB.');
  let value: unknown;
  try { value = JSON.parse(raw); }
  catch { throw new Error('The snapshot must be valid JSON.'); }
  return validateSnapshot(value, kind, now);
}
