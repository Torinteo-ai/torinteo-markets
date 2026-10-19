import { useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowRight, BarChart3, Database, FlaskConical, LockKeyhole } from 'lucide-react';
import type { SourceKind } from '../types';
import { Brand } from '../components/Shell';
import { Dialog, Notice, Spinner } from '../components/Primitives';

export function Login({ onLogin, error, busy }: { onLogin: (token: string) => Promise<void>; error: string | null; busy: boolean }) {
  const [token, setToken] = useState('');
  return <main className="auth-page"><div className="auth-panel"><Brand /><div className="auth-icon"><LockKeyhole size={25} /></div><h1>Open your paper desk</h1><p className="muted">Enter the owner token configured on your Torinteo Markets server.</p>{error && <Notice>{error}</Notice>}<form onSubmit={e => { e.preventDefault(); void onLogin(token); setToken(''); }}><label className="field"><span>Owner access token</span><input type="password" autoComplete="current-password" value={token} onChange={e => setToken(e.target.value)} minLength={32} maxLength={512} required autoFocus /></label><button className="btn btn-primary btn-full" disabled={busy}>{busy ? <Spinner label="Signing in" /> : <>Open paper desk <ArrowRight size={16} /></>}</button></form><p className="footnote">Paper trading only. No exchange account or live orders.</p></div></main>;
}

export function CreateExperiment({ onClose, onCreate, busy, initialKind = 'market' }: { onClose: () => void; onCreate: (name: string, kind: SourceKind, config?: Record<string, unknown>) => Promise<void>; busy: boolean; initialKind?: SourceKind }) {
  const [kind, setKind] = useState<SourceKind>(initialKind);
  const [name, setName] = useState(initialKind === 'synthetic' ? 'Synthetic walkthrough' : 'Market research');
  const [equity, setEquity] = useState('10000');
  const [risk, setRisk] = useState('0.5');
  const [dailyLoss, setDailyLoss] = useState('2');
  const [jev, setJev] = useState('disabled');
  const [research, setResearch] = useState('disabled');
  const [error, setError] = useState<string | null>(null);
  const options: { kind: SourceKind; title: string; text: string; icon: typeof Database }[] = [
    { kind: 'synthetic', title: 'Guided walkthrough', text: 'An invented market with a complete paper order lifecycle.', icon: FlaskConical },
    { kind: 'market', title: 'Market observations', text: 'Public prices, your reviewed evidence and simulated funds.', icon: BarChart3 },
    { kind: 'historical', title: 'Historical replay', text: 'Import snapshots with documented point-in-time evidence.', icon: Database },
  ];
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(null);
    try {
      if (kind !== 'synthetic' && Number(risk) > Number(dailyLoss)) throw new Error('Risk per trade must not exceed the daily loss limit.');
      await onCreate(name, kind, kind === 'synthetic' ? undefined : { initial_equity: Number(equity), risk_fraction: Number(risk) / 100, daily_loss_fraction: Number(dailyLoss) / 100, jev_mode: jev, research_mode: research });
    } catch (err) { setError(err instanceof Error ? err.message : 'The experiment could not be created.'); }
  };
  return <Dialog title="New paper experiment" onClose={onClose}><form onSubmit={submit}>{error && <Notice>{error}</Notice>}<label className="field"><span>Experiment name</span><input value={name} onChange={e => setName(e.target.value)} required maxLength={80} autoFocus /></label><fieldset className="experiment-options"><legend>Evidence source</legend>{options.map(({ kind: option, title, text, icon: Icon }) => <label className={`experiment-option ${kind === option ? 'selected' : ''}`} key={option}><input type="radio" name="kind" value={option} checked={kind === option} onChange={() => { setKind(option); if (['Synthetic walkthrough', 'Market research', 'Historical replay'].includes(name)) setName(option === 'synthetic' ? 'Synthetic walkthrough' : option === 'market' ? 'Market research' : 'Historical replay'); }} /><Icon size={22} /><span><strong>{title}</strong><small>{text}</small></span></label>)}</fieldset>
    {kind !== 'synthetic' ? <details className="config-options"><summary>Paper risk & optional assessments</summary><div className="form-grid"><label className="field"><span>Starting paper funds (USD)</span><input type="number" min="1" max="1000000000" step="1" value={equity} onChange={e => setEquity(e.target.value)} required /></label><label className="field"><span>Risk per trade (%)</span><input type="number" min="0.01" max="100" step="0.01" value={risk} onChange={e => setRisk(e.target.value)} required /></label><label className="field"><span>Daily loss limit (%)</span><input type="number" min="0.01" max="100" step="0.01" value={dailyLoss} onChange={e => setDailyLoss(e.target.value)} required /></label><label className="field"><span>Jev entry assessment</span><select value={jev} onChange={e => setJev(e.target.value)}><option value="disabled">Disabled</option><option value="shadow">Shadow — record only</option><option value="required">Required — blocks if unavailable</option></select></label><label className="field"><span>TradingAgents assessment</span><select value={research} onChange={e => setResearch(e.target.value)}><option value="disabled">Disabled</option><option value="shadow">Shadow — record only</option><option value="required">Required — blocks if unavailable</option></select></label></div><p className="muted small">Optional assessments require server configuration and may make paid provider calls. Risk settings are fixed when the experiment is created.</p></details> : <p className="muted small">The walkthrough uses the tested default risk profile: $10,000 paper funds, 0.5% risk per trade and a 2% daily loss limit.</p>}
    <div className="form-actions dialog-actions"><button className="btn btn-secondary" type="button" onClick={onClose} disabled={busy}>Cancel</button><button className="btn btn-primary" type="submit" disabled={busy || !name.trim()}>{busy ? <Spinner label="Creating" /> : 'Create paper experiment'}</button></div></form></Dialog>;
}
