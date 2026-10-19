import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, BarChart3, FlaskConical } from 'lucide-react';
import { ApiError, api, downloadJson, request } from './api';
import type { Collection, DemoStep, Experiment, ExperimentDetail, Session, Snapshot, SourceKind, View } from './types';
import { Shell } from './components/Shell';
import { Notice, Spinner } from './components/Primitives';
import { CreateExperiment, Login } from './features/AccountForms';
import { Desk, MarketToolbar, Metrics } from './features/Desk';
import { AuditTrail, DecisionsTable, Positions } from './features/History';
import { Integrations } from './features/Integrations';
import { ResearchWorkbench } from './features/ResearchWorkbench';

function readSelection() { try { return localStorage.getItem('torinteo.experiment') ?? ''; } catch { return ''; } }
function saveSelection(id: string) { try { localStorage.setItem('torinteo.experiment', id); } catch { /* Session still works without browser persistence. */ } }
function message(error: unknown) { return error instanceof Error ? error.message : 'The action could not be completed.'; }

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState<ExperimentDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [view, setView] = useState<View>('desk');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState<SourceKind | null>(null);
  const [symbol, setSymbol] = useState('BTC/USD');
  const [timeframe, setTimeframe] = useState('15m');
  const [collection, setCollection] = useState<Collection | null>(null);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketError, setMarketError] = useState<string | null>(null);
  const [, tick] = useState(0);
  const detailsVersion = useRef(0);
  const marketVersion = useRef(0);
  const marketAbort = useRef<AbortController | null>(null);
  const authRequired = Boolean(session?.requires_login);

  async function loadExperiments() {
    const result = await api.experiments(); setExperiments(result.experiments);
    const saved = readSelection();
    setSelected(result.experiments.some(item => item.id === saved) ? saved : result.experiments[0]?.id ?? '');
  }
  const boot = useCallback(async () => {
    setError(null);
    try { const status = await api.session(); setSession(status); if (status.authenticated) await loadExperiments(); }
    catch (err) { setError(message(err)); }
  }, []);
  useEffect(() => { void boot(); }, [boot]);
  useEffect(() => { const interval = setInterval(() => tick(value => value + 1), 15000); return () => clearInterval(interval); }, []);
  useEffect(() => {
    const version = ++detailsVersion.current;
    setCollection(null); setDetail(null); setMarketError(null); setError(null); setNotice(null);
    if (!selected) return;
    saveSelection(selected); setDetailLoading(true);
    api.detail(selected).then(value => { if (version === detailsVersion.current) setDetail(value); }).catch(err => { if (version === detailsVersion.current) { setError(message(err)); if (err instanceof ApiError && err.status === 401) setSession({ authenticated: false, requires_login: true }); } }).finally(() => { if (version === detailsVersion.current) setDetailLoading(false); });
  }, [selected]);

  const refreshMarket = useCallback(async () => {
    if (detail?.experiment.kind !== 'market') return;
    const version = ++marketVersion.current;
    marketAbort.current?.abort();
    const controller = new AbortController(); marketAbort.current = controller;
    setMarketLoading(true); setMarketError(null);
    try { const value = await api.market(symbol, timeframe, controller.signal); if (version === marketVersion.current) setCollection(value); }
    catch (err) { if (version === marketVersion.current && !(err instanceof DOMException && err.name === 'AbortError')) { setCollection(null); setMarketError(message(err)); } }
    finally { if (version === marketVersion.current) setMarketLoading(false); }
  }, [detail?.experiment.id, detail?.experiment.kind, symbol, timeframe]);
  useEffect(() => { setCollection(null); if (detail?.experiment.kind === 'market') void refreshMarket(); else { marketAbort.current?.abort(); ++marketVersion.current; setMarketLoading(false); } return () => { marketAbort.current?.abort(); ++marketVersion.current; }; }, [refreshMarket, detail?.experiment.kind]);

  async function perform(fn: () => Promise<void>) {
    setBusy(true); setError(null); setNotice(null);
    try { await fn(); } catch (err) { setError(message(err)); if (err instanceof ApiError && err.status === 401) setSession({ authenticated: false, requires_login: true }); } finally { setBusy(false); }
  }
  async function create(name: string, kind: SourceKind, config?: Record<string, unknown>) {
    setBusy(true); setError(null);
    try {
      const result = await api.create(name, kind, config);
      const experiment = 'experiment' in result ? result.experiment : result;
      const all = await api.experiments(); setExperiments(all.experiments); setSelected(experiment.id); setView('desk'); setCreating(null);
    } finally { setBusy(false); }
  }
  async function runDemo(step: DemoStep) {
    if (!detail) return;
    await perform(async () => { const result = await api.demo(detail.experiment.id, step); setDetail(result); setNotice(step === 'fill' ? 'The execution candle was processed. Inspect the recorded fill and entry fee.' : step === 'close' ? 'The exit candle was processed. The realised outcome and fees are recorded.' : 'The paper decision is recorded. Inspect its evidence below.'); });
  }
  async function evaluate(snapshot: Snapshot) {
    if (!detail) throw new Error('Select an experiment first.');
    setBusy(true); setNotice(null);
    try { const result = await api.evaluate(detail.experiment.id, snapshot); setDetail(result); setNotice('The paper decision is recorded. Open Decisions to inspect the result.'); }
    finally { setBusy(false); }
  }
  async function exportAudit() { if (!selected) return; await perform(async () => { const payload = await request(`/experiments/${selected}/export`); downloadJson(payload, `torinteo-paper-${selected}.json`); setNotice('Your complete paper audit has been downloaded.'); }); }
  async function logout() { await perform(async () => { await api.logout(); setSession({ authenticated: false, requires_login: true }); setExperiments([]); setDetail(null); setSelected(''); }); }

  if (!session) return <div className="boot-page"><h1>Torinteo Markets</h1>{error ? <><Notice>{error}</Notice><button className="btn btn-primary" onClick={boot}>Retry connection</button></> : <Spinner />}</div>;
  if (!session.authenticated) return <Login error={error} busy={busy} onLogin={token => perform(async () => { const result = await api.login(token); setSession(result); await loadExperiments(); })} />;
  return <Shell view={view} onNavigate={next => { setView(next); setNotice(null); }} experiments={experiments} selected={selected} onSelect={setSelected} onNew={() => setCreating('market')} onExport={exportAudit} busy={busy} authRequired={authRequired} onLogout={logout}>
    {error && <Notice onClose={() => setError(null)}>{error}</Notice>}{notice && <Notice tone="success" onClose={() => setNotice(null)}>{notice}</Notice>}
    {view === 'integrations' ? <Integrations /> : detailLoading ? <div className="loading-region"><Spinner label="Loading experiment" /></div> : detail ? <>
      <Metrics detail={detail} />
      {detail.experiment.kind === 'market' && (view === 'desk' || view === 'research') && <><MarketToolbar symbol={symbol} onSymbol={setSymbol} collection={collection} loading={marketLoading || busy} onRefresh={refreshMarket} />{marketError && <Notice>{marketError}</Notice>}</>}
      {view === 'desk' && <Desk detail={detail} collection={collection} marketSymbol={symbol} timeframe={timeframe} onTimeframe={setTimeframe} marketLoading={marketLoading} busy={busy} onDemo={runDemo} onNavigate={setView} />}
      {view === 'research' && <ResearchWorkbench experiment={detail.experiment} collection={collection} snapshot={detail.latest_snapshot} onEvaluate={evaluate} onRefresh={refreshMarket} busy={busy || marketLoading} />}
      {view === 'decisions' && <DecisionsTable decisions={detail.decisions} orders={detail.orders} />}
      {view === 'positions' && <Positions detail={detail} busy={busy} onResult={value => { setDetail(value); setNotice('The paper account is updated.'); }} onError={setError} runAction={perform} />}
      {view === 'audit' && <AuditTrail events={detail.events} />}
    </> : <section className="welcome-panel"><div className="welcome-copy"><FlaskConical size={32} strokeWidth={1.4} /><h2>Your research desk starts here.</h2><p>Create an isolated experiment to inspect evidence, run a paper decision and follow its outcome.</p><div className="form-actions"><button className="btn btn-primary" onClick={() => setCreating('synthetic')}>Start the walkthrough <ArrowRight size={16} /></button><button className="btn btn-secondary" onClick={() => setCreating('market')}><BarChart3 size={16} />Explore market data</button></div></div><div className="welcome-flow"><div><span>01</span><h3>Observe & review</h3><p>Prices, major levels, direction and news.</p></div><div><span>02</span><h3>Evaluate the evidence</h3><p>Deterministic rules and recorded decisions.</p></div><div><span>03</span><h3>Follow the paper order</h3><p>Simulated fills, fees and persistent history.</p></div></div></section>}
    {creating && <CreateExperiment onClose={() => setCreating(null)} onCreate={create} busy={busy} initialKind={creating} />}
  </Shell>;
}
