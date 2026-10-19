import { useEffect, useState } from 'react';
import { ExternalLink, RefreshCw } from 'lucide-react';
import { api } from '../api';
import { human } from '../format';
import { Badge, JsonDetails, Notice, Spinner } from '../components/Primitives';

interface Provider { id: string; name: string; role: string; status: string; message?: string; connection_verified?: boolean }
const notes: Record<string, string> = {
  public_market: 'BTC/USD, ETH/USD and SOL/USD. Closed candles and provider bid/ask. Refresh a chart to test the connection.',
  native: 'The active executor for this desk. Records deterministic gates, orders, conservative fills, fees and the audit trail.',
  openbb: 'The Python adapter is included. Install OpenBB and configure a supported data provider to extend the data path.',
  tradingagents: 'Install TradingAgents and configure the provider plus quick/deep model names on the server. Select shadow or required mode in a new experiment.',
  jev: 'Set TYPESAFE_API_KEY on the server. Shadow mode records an assessment; required mode blocks entry when the service rejects or is unavailable.',
  freqtrade: 'The repository includes a dry-run confirmation component. A full host strategy and execution reconciliation are still required.',
  quantdinger: 'Reviewed as an alternative platform. It is not running inside this application.',
};
const links: Record<string, string> = { public_market: 'https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/rest-api', native: 'https://github.com/Torinteo-ai/torinteo-markets', openbb: 'https://github.com/openbq-org/OpenBB', tradingagents: 'https://github.com/TauricResearch/TradingAgents', jev: 'https://github.com/Torinteo-ai/torinteo-markets/blob/main/docs/JEV.md', freqtrade: 'https://github.com/freqtrade/freqtrade', quantdinger: 'https://github.com/OpenByteInc/QuantDinger' };
export function Integrations() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  async function refresh() { setLoading(true); setError(null); try { setData(await api.integrations()); } catch (err) { setError(err instanceof Error ? err.message : 'Could not read integration status.'); } finally { setLoading(false); } }
  useEffect(() => { void refresh(); }, []);
  const providers = Array.isArray(data?.providers) ? data.providers as Provider[] : [];
  return <><section className="panel"><div className="panel-header"><div><h2>Integration status</h2><p className="muted small">Configuration presence is separate from a verified provider response.</p></div><button className="btn btn-secondary" onClick={refresh} disabled={loading}>{loading ? <Spinner label="Checking" /> : <><RefreshCw size={15} />Refresh status</>}</button></div>{error && <div className="panel-padding"><Notice>{error}</Notice></div>}<div className="integration-list">{providers.map(provider => <article className="integration-row" key={provider.id}><div className="integration-heading"><h3>{provider.name}</h3><Badge tone={['ready', 'available'].includes(provider.status) ? 'cyan' : 'neutral'}>{human(provider.status)}</Badge></div><p className="integration-role">{provider.role}</p><p className="muted">{notes[provider.id] ?? provider.message}</p><a href={links[provider.id] ?? 'https://github.com/Torinteo-ai/torinteo-markets'} target="_blank" rel="noreferrer" className="text-btn">Documentation <ExternalLink size={13} /></a></article>)}</div></section><p className="footnote">This app runs paper execution on demand. No live exchange credentials or background trading loop are enabled.</p>{data && <JsonDetails value={data} label="Server capability report" />}</>;
}
