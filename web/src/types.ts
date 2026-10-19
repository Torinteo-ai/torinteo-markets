export type SourceKind = 'synthetic' | 'market' | 'historical';
export type Bias = 'LONG' | 'SHORT' | 'NEUTRAL';
export type View = 'desk' | 'research' | 'decisions' | 'positions' | 'audit' | 'integrations';
export interface Bar { open_time: string; close_time: string; open: number; high: number; low: number; close: number; volume?: number }
export interface Level { id: string; price: number; kind: 'support' | 'resistance'; timeframe: '1M' | '1w' | 'historical'; known_at: string; reference: string }
export interface Review { as_of: string; reference: string; bias: Bias }
export interface NewsEvent { currency: string; impact: string; starts_at: string; known_at: string; reference: string }
export interface Quote { bid: number; ask: number; as_of: string; reference?: string; observed_at?: string }
export interface Snapshot {
  schema_version: 1; symbol: string; asset_class: string; quote_currency: string;
  as_of: string; timeframe: string; source: { provider: string; kind: SourceKind; reference: string };
  quote: Quote; bars: Bar[]; levels: Level[]; reviews: Record<string, Review>;
  news: { status: 'clear' | 'blocked' | 'unknown'; as_of: string; source: string; events: NewsEvent[] };
}
export interface Collection {
  schema_version: 1; symbol: string; asset_class: string; quote_currency: string;
  source: string; source_kind: 'market'; reference: string; timeframe: string;
  as_of: string; observed_at: string; point_in_time_archive: boolean; candles: Bar[]; quote: Quote;
}
export interface RiskConfig {
  initial_equity: number; risk_fraction: number; daily_loss_fraction: number;
  max_open_positions: number; max_notional_fraction: number; max_total_notional_fraction: number;
  min_reward_risk: number; account_currency: string; fee_bps: number; slippage_bps: number;
  jev_mode: string; research_mode: string; [key: string]: string | number | boolean;
}
export interface Experiment { id: string; name: string; kind: SourceKind; created_at: string; config: RiskConfig }
export interface Intent {
  side: 'LONG' | 'SHORT'; entry: number; stop: number; target: number;
  quantity: number; notional: number; risk_cash: number; reward_risk: number; fee_bps: number; slippage_bps: number;
}
export interface Decision {
  decision_id: string; call: 'LONG' | 'SHORT' | 'NO_TRADE'; reason_codes: string[];
  symbol: string; as_of: string; approved_at: string; expires_at: string; provenance: SourceKind;
  intent: Intent | null; evidence: Record<string, unknown>; assessments?: Record<string, unknown>;
  executor: string; mode: 'paper';
}
export interface Order {
  id: number; decision_id: string; symbol: string; side: string; status: 'PENDING' | 'OPEN' | 'CLOSED' | 'CANCELLED';
  quantity: number; intended_entry: number; entry: number | null; stop: number; target: number;
  notional: number; risk_cash: number; fees: number; net_pnl: number | null;
  approved_at: string; expires_at: string; opened_at: string | null; closed_at: string | null;
  exit_reason: string | null;
}
export interface AuditEvent { sequence: number; occurred_at: string; kind: string; decision_id?: string; payload: Record<string, unknown> }
export type DemoStep = 'prepare' | 'evaluate' | 'fill' | 'close' | 'blocked';
export interface ExperimentDetail {
  experiment: Experiment;
  account: { equity: number; daily_pnl: number; open_symbols: string[]; open_notional: number };
  orders: Order[]; decisions: Decision[]; events: AuditEvent[]; latest_snapshot: Snapshot | null;
  demo?: { enabled: boolean; stage: string; next_step: DemoStep | null; available_steps: DemoStep[] };
}
export interface Session { authenticated: boolean; requires_login?: boolean; mode?: string }

export interface ReviewDraft {
  symbol: string; levels: Level[]; reviews: Record<string, Review>; news: Snapshot['news']; saved_at: string;
}
