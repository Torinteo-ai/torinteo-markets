import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { AlertCircle, LoaderCircle, X } from 'lucide-react';

export function Spinner({ label = 'Loading paper desk' }: { label?: string }) {
  return <span className="loading" role="status"><LoaderCircle className="spin" size={17} />{label}</span>;
}
export function Notice({ children, onClose, tone = 'error' }: { children: ReactNode; onClose?: () => void; tone?: 'error' | 'info' | 'success' }) {
  return <div className={`notice notice-${tone}`} role={tone === 'error' ? 'alert' : 'status'}><AlertCircle size={17} /><div>{children}</div>{onClose && <button className="icon-btn" aria-label="Dismiss notification" onClick={onClose}><X size={16} /></button>}</div>;
}
export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
export function Dialog({ title, children, onClose, wide = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const dialog = ref.current; dialog?.showModal(); return () => dialog?.close(); }, []);
  return <dialog ref={ref} className={`dialog ${wide ? 'dialog-wide' : ''}`} onCancel={onClose} aria-label={title} onClick={e => { if (e.target === ref.current) onClose(); }}>
    <div className="dialog-header"><h2>{title}</h2><button className="icon-btn" onClick={onClose} aria-label="Close dialog"><X size={20} /></button></div>
    <div className="dialog-body">{children}</div>
  </dialog>;
}
export function Empty({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return <div className="empty-state">{icon}<h3>{title}</h3>{children && <div className="muted">{children}</div>}</div>;
}
export function JsonDetails({ value, label = 'View complete evidence' }: { value: unknown; label?: string }) {
  return <details className="json-details"><summary>{label}</summary><pre className="code-block">{JSON.stringify(value, null, 2)}</pre></details>;
}
