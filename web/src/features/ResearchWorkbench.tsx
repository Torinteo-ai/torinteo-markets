import { useEffect, useId, useState } from 'react';
import type { ChangeEvent, FormEvent } from 'react';
import type { Bias, Collection, Experiment, Snapshot } from '../types';
import { human, price, time } from '../format';
import {
  composeMarketSnapshot, createDraft, draftKey, newLevel, parseSnapshotImport,
  readDraft, REVIEW_TIMEFRAMES,
} from '../review';
import type { DraftLevel, WorkbenchDraft } from '../review';

interface Props {
  experiment: Experiment; collection: Collection | null; snapshot: Snapshot | null;
  onEvaluate: (snapshot: Snapshot) => Promise<void>; onRefresh: () => Promise<void>; busy: boolean;
}
const FRAME_NAMES: Record<string, string> = { '1M': 'Monthly', '1w': 'Weekly', '1d': 'Daily', '4h': '4 hour', '1h': '1 hour' };
const UTC_HELP = 'Enter the original observation time in UTC.';

function SourceEvidence({ snapshot }: { snapshot: Snapshot | null }) {
  if (!snapshot) return <p className="muted">No research snapshot is recorded in this experiment yet.</p>;
  return <>
    <div className="evidence-grid">
      <div><span className="muted">Instrument</span><p><strong>{snapshot.symbol}</strong> · {snapshot.timeframe}</p></div>
      <div><span className="muted">Evidence time</span><p>{time(snapshot.as_of, true)} UTC</p></div>
      <div><span className="muted">Provenance</span><p>{human(snapshot.source.kind)} · {snapshot.source.provider}</p></div>
      <div><span className="muted">Calendar review</span><p>{human(snapshot.news.status)} · {time(snapshot.news.as_of)} UTC</p></div>
    </div>
    <div className="table-scroll"><table className="data-table">
      <caption className="muted">Recorded levels and the time each was known</caption>
      <thead><tr><th scope="col">Level</th><th scope="col">Type</th><th scope="col">Timeframe</th><th scope="col">Known at · UTC</th><th scope="col">Reference</th></tr></thead>
      <tbody>{snapshot.levels.map(level => <tr key={level.id}>
        <td>{price(level.price)}</td><td>{human(level.kind)}</td><td>{FRAME_NAMES[level.timeframe] ?? 'Historical'}</td>
        <td>{time(level.known_at, true)}</td><td>{level.reference}</td>
      </tr>)}</tbody>
    </table></div>
    <div className="table-scroll"><table className="data-table">
      <caption className="muted">Recorded timeframe reviews</caption>
      <thead><tr><th scope="col">Timeframe</th><th scope="col">Direction</th><th scope="col">Reviewed · UTC</th><th scope="col">Reference</th></tr></thead>
      <tbody>{REVIEW_TIMEFRAMES.map(tf => {
        const review = snapshot.reviews[tf];
        return review ? <tr key={tf}><td>{FRAME_NAMES[tf]}</td><td>{human(review.bias)}</td><td>{time(review.as_of, true)}</td><td>{review.reference}</td></tr> : null;
      })}</tbody>
    </table></div>
    <details><summary>View the complete recorded evidence</summary><pre className="code-block">{JSON.stringify(snapshot, null, 2)}</pre></details>
  </>;
}

export function ResearchWorkbench({ experiment, collection, snapshot, onEvaluate, onRefresh, busy }: Props) {
  const prefix = useId();
  const [draft, setDraft] = useState<WorkbenchDraft>(() => createDraft(collection?.symbol
    ?? (['BTC/USD', 'ETH/USD', 'SOL/USD'].includes(snapshot?.symbol ?? '') ? snapshot!.symbol : 'BTC/USD')));
  // A refresh may temporarily clear collection; retain the in-progress review.
  const symbol = collection?.symbol ?? draft.symbol;
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [importText, setImportText] = useState('');
  const [working, setWorking] = useState(false);
  const disabled = busy || working;
  const market = experiment.kind === 'market';

  useEffect(() => {
    let restored = createDraft(symbol);
    setError(''); setMessage(''); setDirty(false); setImportText('');
    if (experiment.kind === 'market') {
      try {
        const raw = localStorage.getItem(draftKey(experiment.id, symbol));
        if (raw) restored = readDraft(raw, symbol);
      } catch {
        setError('The saved browser draft could not be loaded. A blank review is shown; your stored draft has not been overwritten.');
      }
    }
    setDraft(restored);
  }, [experiment.id, experiment.kind, symbol]);

  function update(change: (current: WorkbenchDraft) => WorkbenchDraft) {
    setDraft(change); setDirty(true); setMessage(''); setError('');
  }
  function updateLevel(index: number, changes: Partial<DraftLevel>) {
    update(current => ({ ...current, levels: current.levels.map((level, at) => at === index ? { ...level, ...changes } : level) }));
  }
  function addLevel() {
    update(current => {
      let next = current.levels.length + 1;
      while (current.levels.some(level => level.id === `level-${next}`)) next += 1;
      return { ...current, levels: [...current.levels, newLevel(next)] };
    });
  }
  function saveDraft() {
    try {
      const saved = { ...draft, saved_at: new Date().toISOString() };
      const raw = JSON.stringify(saved);
      readDraft(raw, symbol);
      localStorage.setItem(draftKey(experiment.id, symbol), raw);
      setDraft(saved); setDirty(false); setError(''); setMessage('Draft saved in this browser. Observation and review times were preserved.');
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'This browser could not save the draft.');
    }
  }
  async function run(action: () => Promise<void>) {
    setWorking(true); setError(''); setMessage('');
    try { await action(); }
    catch (problem) { setError(problem instanceof Error ? problem.message : 'The action could not be completed.'); }
    finally { setWorking(false); }
  }
  function evaluate(event: FormEvent) {
    event.preventDefault();
    void run(async () => {
      const evidence = composeMarketSnapshot(collection, draft, new Date(), {
        quoteSeconds: Number(experiment.config.max_quote_age_seconds ?? 60),
        snapshotSeconds: Number(experiment.config.max_snapshot_age_seconds ?? 120),
      });
      await onEvaluate(evidence);
      setMessage('Paper setup submitted. Its outcome is recorded in Decisions.');
    });
  }
  async function loadFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { setError('Snapshot JSON exceeds 2 MiB.'); return; }
    try { setImportText(await file.text()); setError(''); setMessage('File loaded for review. Select Evaluate imported snapshot when ready.'); }
    catch { setError('The selected file could not be read.'); }
  }

  return <div className="research-workbench">
    {error && <div className="notice notice-error" role="alert">{error}</div>}
    {message && <div className="notice" role="status" aria-live="polite">{message}</div>}

    {market ? <form onSubmit={evaluate} noValidate>
      <section className="panel">
        <div className="panel-header"><div><h2>{symbol} · reviewed context</h2>
          <p className="muted">Enter evidence from your chart and calendar review. All input times below are UTC.</p></div>
          <button className="btn btn-secondary" type="button" disabled={disabled} onClick={() => void run(onRefresh)}>Refresh data</button>
        </div>
        <div className="notice">
          {collection ? <>{collection.source} · quote {time(collection.quote.as_of, true)} UTC · received {time(collection.observed_at)} UTC · {collection.candles.length} closed {collection.timeframe} candles.</>
            : <>Market data has not been loaded. You can prepare and save a review draft before refreshing.</>}
        </div>
        <p className="muted">{dirty ? 'Unsaved review changes.' : draft.saved_at ? `Draft saved in this browser · ${time(draft.saved_at, true)} UTC.` : 'No browser draft saved yet.'}</p>
      </section>

      <section className="panel">
        <div className="panel-header"><div><h2>Major and opposing levels</h2><p className="muted">Record at least two sourced levels. “Known at” is when the level was identified, not when this form is saved.</p></div></div>
        {draft.levels.map((level, index) => <fieldset className="review-level" key={level.id} disabled={disabled}>
          <legend>Reviewed level {index + 1}</legend>
          <div className="form-grid">
            <label className="field" htmlFor={`${prefix}-price-${level.id}`}><span>Price · {collection?.quote_currency ?? 'USD'}</span>
              <input id={`${prefix}-price-${level.id}`} type="number" min="0" step="any" inputMode="decimal" value={level.price} onChange={event => updateLevel(index, { price: event.target.value })} placeholder="Enter reviewed price" required /></label>
            <label className="field" htmlFor={`${prefix}-kind-${level.id}`}><span>Level type</span>
              <select id={`${prefix}-kind-${level.id}`} value={level.kind} onChange={event => updateLevel(index, { kind: event.target.value as DraftLevel['kind'] })}><option value="support">Support</option><option value="resistance">Resistance</option></select></label>
            <label className="field" htmlFor={`${prefix}-frame-${level.id}`}><span>Level timeframe</span>
              <select id={`${prefix}-frame-${level.id}`} value={level.timeframe} onChange={event => updateLevel(index, { timeframe: event.target.value as DraftLevel['timeframe'] })}><option value="1M">Monthly</option><option value="1w">Weekly</option><option value="historical">Historical reaction level</option></select></label>
            <label className="field" htmlFor={`${prefix}-known-${level.id}`}><span>Known at · UTC</span>
              <input id={`${prefix}-known-${level.id}`} type="datetime-local" step="1" value={level.known_at} title={UTC_HELP} onChange={event => updateLevel(index, { known_at: event.target.value })} required /></label>
            <label className="field field-wide" htmlFor={`${prefix}-ref-${level.id}`}><span>Chart or analysis reference</span>
              <input id={`${prefix}-ref-${level.id}`} maxLength={2048} value={level.reference} onChange={event => updateLevel(index, { reference: event.target.value })} placeholder="Chart URL or a precise saved-chart reference" required /></label>
          </div>
          <button className="btn btn-secondary" type="button" disabled={disabled || draft.levels.length <= 2} aria-label={`Remove reviewed level ${index + 1}`} onClick={() => update(current => ({ ...current, levels: current.levels.filter((_, at) => at !== index) }))}>Remove level</button>
        </fieldset>)}
        <button className="btn btn-secondary" type="button" disabled={disabled || draft.levels.length >= 200} onClick={addLevel}>Add reviewed level</button>
      </section>

      <section className="panel">
        <div className="panel-header"><div><h2>Timeframe reviews</h2><p className="muted">Direction comes from your recorded review. Neutral is the initial unreviewed selection.</p></div></div>
        {REVIEW_TIMEFRAMES.map(tf => <fieldset className="review-row" key={tf} disabled={disabled}>
          <legend>{FRAME_NAMES[tf]}</legend><div className="form-grid">
            <label className="field" htmlFor={`${prefix}-bias-${tf}`}><span>Reviewed direction</span><select id={`${prefix}-bias-${tf}`} value={draft.reviews[tf].bias} onChange={event => update(current => ({ ...current, reviews: { ...current.reviews, [tf]: { ...current.reviews[tf], bias: event.target.value as Bias } } }))}><option value="NEUTRAL">Neutral</option><option value="LONG">Long bias</option><option value="SHORT">Short bias</option></select></label>
            <label className="field" htmlFor={`${prefix}-review-time-${tf}`}><span>Reviewed at · UTC</span><input id={`${prefix}-review-time-${tf}`} type="datetime-local" step="1" title={UTC_HELP} value={draft.reviews[tf].as_of} onChange={event => update(current => ({ ...current, reviews: { ...current.reviews, [tf]: { ...current.reviews[tf], as_of: event.target.value } } }))} required /></label>
            <label className="field field-wide" htmlFor={`${prefix}-review-ref-${tf}`}><span>Review reference</span><input id={`${prefix}-review-ref-${tf}`} maxLength={2048} value={draft.reviews[tf].reference} onChange={event => update(current => ({ ...current, reviews: { ...current.reviews, [tf]: { ...current.reviews[tf], reference: event.target.value } } }))} placeholder="Chart URL or a precise recorded-review reference" required /></label>
          </div>
        </fieldset>)}
      </section>

      <section className="panel">
        <div className="panel-header"><div><h2>Economic calendar review</h2><p className="muted">Unknown calendar status blocks an entry. An empty events list only describes the calendar you actually reviewed.</p></div></div>
        <fieldset disabled={disabled}><legend className="muted">Calendar evidence</legend><div className="form-grid">
          <label className="field" htmlFor={`${prefix}-news-status`}><span>Status</span><select id={`${prefix}-news-status`} value={draft.news.status} onChange={event => update(current => ({ ...current, news: { ...current.news, status: event.target.value as Snapshot['news']['status'] } }))}><option value="unknown">Unknown / not cleared</option><option value="clear">Reviewed and clear</option><option value="blocked">Blocked by calendar</option></select></label>
          <label className="field" htmlFor={`${prefix}-news-time`}><span>Reviewed at · UTC</span><input id={`${prefix}-news-time`} type="datetime-local" step="1" title={UTC_HELP} value={draft.news.as_of} onChange={event => update(current => ({ ...current, news: { ...current.news, as_of: event.target.value } }))} required /></label>
          <label className="field field-wide" htmlFor={`${prefix}-news-ref`}><span>Calendar reference</span><input id={`${prefix}-news-ref`} maxLength={2048} value={draft.news.source} onChange={event => update(current => ({ ...current, news: { ...current.news, source: event.target.value } }))} placeholder="Calendar URL or dated calendar-record reference" required /></label>
        </div>
        <details><summary>Calendar events · optional JSON</summary><p className="muted">Each event needs currency, impact, starts_at, known_at and reference. Event timestamps need a timezone, such as Z for UTC.</p>
          <label className="field" htmlFor={`${prefix}-news-events`}><span>Recorded events</span><textarea id={`${prefix}-news-events`} className="import-area" rows={7} maxLength={100000} spellCheck={false} value={draft.news.eventsText} onChange={event => update(current => ({ ...current, news: { ...current.news, eventsText: event.target.value } }))} /></label>
        </details></fieldset>
      </section>

      <section className="panel"><div className="panel-header"><div><h2>Evaluate the paper setup</h2><p className="muted">The server checks current observations, recorded evidence and this experiment’s risk limits before recording a decision.</p></div></div>
        <div className="form-actions"><button className="btn btn-secondary" type="button" disabled={disabled} onClick={saveDraft}>Save review draft</button>
          <button className="btn btn-primary" type="submit" disabled={disabled || !collection}>{disabled ? 'Working…' : 'Evaluate paper setup'}</button></div>
        <p className="muted">Drafts stay in this browser. Saving does not change when a level or review was originally known.</p>
      </section>
    </form> : <section className="panel">
      <div className="panel-header"><div><h2>Import {experiment.kind} evidence</h2><p className="muted">Import a complete sourced snapshot for this experiment. Its original provenance and timestamps are preserved.</p></div></div>
      <label className="field" htmlFor={`${prefix}-import-file`}><span>Load a snapshot JSON file · maximum 2 MiB</span><input id={`${prefix}-import-file`} type="file" accept="application/json,.json" disabled={disabled} onChange={event => void loadFile(event)} /></label>
      <label className="field" htmlFor={`${prefix}-import-json`}><span>Snapshot JSON</span><textarea id={`${prefix}-import-json`} className="import-area" rows={12} value={importText} maxLength={2 * 1024 * 1024} spellCheck={false} placeholder="Paste a complete version 1 snapshot with matching source provenance" onChange={event => { setImportText(event.target.value); setMessage(''); setError(''); }} disabled={disabled} /></label>
      <button className="btn btn-primary" type="button" disabled={disabled || !importText.trim()} onClick={() => void run(async () => {
        const imported = parseSnapshotImport(importText, experiment.kind);
        await onEvaluate(imported); setMessage('Imported snapshot evaluated. Its outcome is recorded in Decisions.');
      })}>{disabled ? 'Working…' : 'Evaluate imported snapshot'}</button>
    </section>}

    <section className="panel"><div className="panel-header"><div><h2>Latest recorded research</h2><p className="muted">Evidence from the latest snapshot recorded by the server.</p></div></div><SourceEvidence snapshot={snapshot} /></section>
  </div>;
}
