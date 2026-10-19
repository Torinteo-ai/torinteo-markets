import { Activity, BookOpen, CheckSquare, ChevronRight, Download, FileClock, Link2, LogOut, Plus, Wallet } from 'lucide-react';
import type { ReactNode } from 'react';
import type { Experiment, View } from '../types';

export const views: { id: View; label: string; icon: typeof Activity }[] = [
  { id: 'desk', label: 'Desk', icon: Activity },
  { id: 'research', label: 'Research & levels', icon: BookOpen },
  { id: 'decisions', label: 'Decisions', icon: CheckSquare },
  { id: 'positions', label: 'Paper positions', icon: Wallet },
  { id: 'audit', label: 'Audit trail', icon: FileClock },
  { id: 'integrations', label: 'Integrations', icon: Link2 },
];
export function Brand() {
  return <div className="brand"><img src="/branding/torinteo-logo.svg" alt="Torinteo" /><div><strong>TORINTEO</strong><span>Markets</span></div></div>;
}
export function Shell({ view, onNavigate, experiments, selected, onSelect, onNew, onExport, busy, children, authRequired, onLogout }: {
  view: View; onNavigate: (view: View) => void; experiments: Experiment[]; selected: string;
  onSelect: (id: string) => void; onNew: () => void; onExport: () => void; busy: boolean;
  children: ReactNode; authRequired: boolean; onLogout: () => void;
}) {
  const heading = view === 'desk' ? 'Paper desk' : views.find(item => item.id === view)!.label;
  const descriptions: Record<View, string> = {
    desk: 'Evidence first. Every decision recorded.', research: 'Document the levels, direction and news behind your setup.',
    decisions: 'Every approval and every reason to wait.', positions: 'Follow each paper order from approval to its recorded outcome.',
    audit: 'A persistent history of observations, decisions and paper fills.', integrations: 'Know what is connected, configured and still to activate.',
  };
  return <div className="app-shell">
    <a className="skip-link" href="#main">Skip to content</a>
    <aside className="sidebar"><Brand /><nav aria-label="Main navigation">{views.map(({ id, label, icon: Icon }) => <button key={id} aria-current={view === id ? 'page' : undefined} onClick={() => onNavigate(id)}><Icon size={20} strokeWidth={1.6} /><span>{label}</span></button>)}</nav><div className="sidebar-foot"><strong>PAPER ONLY</strong><span>Orders use simulated funds</span></div></aside>
    <div className="main-shell"><header className="topbar"><div className="breadcrumb"><span>Workspace</span><ChevronRight size={14} /><span>{heading}</span></div><div className="topbar-actions"><span className="muted account-location">{authRequired ? 'Owner account' : 'Local account'}</span>{authRequired && <button className="icon-btn" title="Sign out" aria-label="Sign out" onClick={onLogout}><LogOut size={17} /></button>}<button className="btn btn-outline-accent" onClick={onNew} disabled={busy}><Plus size={15} />New experiment</button></div></header>
      <main id="main" className="main-content"><div className="page-heading"><div><h1>{heading}</h1><p>{descriptions[view]}</p></div><div className="page-actions"><label className="sr-only" htmlFor="experiment-select">Selected experiment</label><select id="experiment-select" value={selected} onChange={e => onSelect(e.target.value)} disabled={busy || !experiments.length}>{!experiments.length && <option value="">Choose an experiment</option>}{experiments.map(exp => <option key={exp.id} value={exp.id}>{exp.name}</option>)}</select><button className="btn btn-secondary" onClick={onExport} disabled={busy || !selected}><Download size={16} /><span>Export audit</span></button></div></div>
      {children}
      </main>
    </div>
  </div>;
}
