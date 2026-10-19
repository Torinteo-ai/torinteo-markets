export function money(value: number | null | undefined, currency = 'USD') {
  if (value == null) return '—';
  if (!/^[A-Z]{3}$/.test(currency)) return `${new Intl.NumberFormat('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value)} ${currency}`;
  return new Intl.NumberFormat('en-GB', { style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
}
export function price(value: number | null | undefined) {
  return value == null ? '—' : new Intl.NumberFormat('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: value < 1 ? 6 : 2 }).format(value);
}
export function compact(value: number) { return new Intl.NumberFormat('en-GB', { notation: 'compact', maximumFractionDigits: 2 }).format(value); }
export function time(value: string | null | undefined, full = false) {
  if (!value) return '—';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', ...(full ? { day: '2-digit', month: 'short', year: 'numeric' } as const : {}), hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(date);
}
export function human(value: string) { return value.toLowerCase().replaceAll('_', ' ').replace(/^./, c => c.toUpperCase()); }
export function percent(value: number) { return `${Number((value * 100).toFixed(2))}%`; }
