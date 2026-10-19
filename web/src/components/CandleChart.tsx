import { useEffect, useRef, useState } from 'react';
import { BarChart3 } from 'lucide-react';
import type { Bar, Level, Quote, SourceKind } from '../types';
import { compact, price, time } from '../format';
import { Badge, Empty } from './Primitives';

export function CandleChart({ bars, levels = [], symbol, kind, quote, timeframe = '15m', onTimeframe, loading = false }: {
  bars: Bar[]; levels?: Level[]; symbol: string; kind: SourceKind; quote?: Quote;
  timeframe?: string; onTimeframe?: (value: string) => void; loading?: boolean;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(780);
  const [hovered, setHovered] = useState<number | null>(null);
  useEffect(() => {
    if (!holder.current) return;
    const observer = new ResizeObserver(entries => setWidth(Math.max(290, entries[0].contentRect.width)));
    observer.observe(holder.current); return () => observer.disconnect();
  }, []);
  useEffect(() => setHovered(null), [bars]);
  const selectedIndex = Math.max(0, Math.min(bars.length - 1, hovered ?? bars.length - 1));
  const selected = bars[selectedIndex];
  const height = 276, left = 16, right = width < 450 ? 65 : 79, top = 20, bottom = 34;
  const chartWidth = width - left - right, chartHeight = height - top - bottom;
  const minimum = bars.length ? Math.min(...bars.map(bar => bar.low)) : 0;
  const maximum = bars.length ? Math.max(...bars.map(bar => bar.high)) : 1;
  const span = Math.max(maximum - minimum, maximum * 0.002);
  const visibleLevels = levels.filter(level => level.price > minimum - span * .3 && level.price < maximum + span * .3);
  const low = Math.min(minimum, ...visibleLevels.map(level => level.price)) - span * .17;
  const high = Math.max(maximum, ...visibleLevels.map(level => level.price)) + span * .17;
  const y = (value: number) => top + (high - value) / (high - low) * chartHeight;
  const slot = chartWidth / Math.max(1, bars.length), candleWidth = Math.min(28, Math.max(2, slot * .62));
  const x = (i: number) => left + slot * (i + .5);
  const ticks = Array.from({ length: 5 }, (_, i) => low + (high - low) * i / 4);
  const maxVolume = Math.max(1, ...bars.map(bar => bar.volume ?? 0));
  const last = bars.at(-1);
  return <section className="chart-region" aria-label={`${symbol} chart`}>
    <div className="chart-header"><div className="chart-identity"><h2>{symbol.replace('/', ' / ')}</h2><Badge tone={kind === 'market' ? 'cyan' : 'neutral'}>{kind === 'synthetic' ? 'Synthetic data' : kind === 'market' ? 'Market observation' : 'Historical replay'}</Badge></div><label className="chart-timeframe"><span className="sr-only">Chart timeframe</span><select value={timeframe} onChange={e => onTimeframe?.(e.target.value)} disabled={!onTimeframe || loading}>{(onTimeframe ? ['1m', '5m', '15m', '1h', '4h', '1d'] : [timeframe]).map(value => <option key={value} value={value}>{value}</option>)}</select></label></div>
    <div className="candle-readout" aria-live="off">{selected ? <><span className="muted">{time(selected.open_time).slice(0, 5)} UTC</span>{(['open', 'high', 'low', 'close'] as const).map(key => <span key={key}><span className="muted">{key[0].toUpperCase()}</span> <b>{price(selected[key])}</b></span>)}<span className="candle-volume"><span className="muted">Vol</span> {compact(selected.volume ?? 0)}</span></> : <span className="muted">Closed candles will appear here</span>}</div>
    <div ref={holder} className="chart-canvas" tabIndex={bars.length ? 0 : -1} role="group" aria-label="Candlestick chart. Use left and right arrow keys to inspect each closed candle." onKeyDown={e => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); setHovered(Math.max(0, Math.min(bars.length - 1, selectedIndex + (e.key === 'ArrowRight' ? 1 : -1)))); } }}>
      {!bars.length ? <Empty icon={<BarChart3 size={36} />} title={loading ? 'Loading market observations' : 'No candles loaded'}>Create an experiment or refresh the public data feed.</Empty> : <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${bars.length} closed ${timeframe} candles. Latest close ${price(last?.close)}.`} onPointerMove={e => { const bounds = e.currentTarget.getBoundingClientRect(); setHovered(Math.max(0, Math.min(bars.length - 1, Math.floor((e.clientX - bounds.left - left) / slot)))); }} onPointerLeave={() => setHovered(null)}>
        <defs><clipPath id="plot-clip"><rect x={left} y={top} width={chartWidth} height={chartHeight} /></clipPath></defs>
        {ticks.map((tick, index) => <g key={index}><line x1={left} y1={y(tick)} x2={width - right} y2={y(tick)} className="chart-grid" /><text x={width - right + 10} y={y(tick) + 4} className="chart-label">{price(tick)}</text></g>)}
        {Array.from({ length: Math.min(6, bars.length) }, (_, index) => Math.floor(index * (bars.length - 1) / Math.max(1, Math.min(5, bars.length - 1)))).map((i, j) => <g key={j}><line x1={x(i)} x2={x(i)} y1={top} y2={height - bottom} className="chart-grid chart-grid-vertical" /><text x={x(i)} y={height - 12} textAnchor="middle" className="chart-label">{time(bars[i].open_time).slice(0, 5)}</text></g>)}
        <g clipPath="url(#plot-clip)">{bars.map((bar, i) => { const up = bar.close >= bar.open; const cy = Math.min(y(bar.open), y(bar.close)); return <g key={bar.open_time} className={up ? 'candle-up' : 'candle-down'}><rect x={x(i) - candleWidth / 2} y={height - bottom - ((bar.volume ?? 0) / maxVolume) * 30} width={candleWidth} height={((bar.volume ?? 0) / maxVolume) * 30} opacity=".12" /><line x1={x(i)} x2={x(i)} y1={y(bar.high)} y2={y(bar.low)} strokeWidth="1.3" /><rect x={x(i) - candleWidth / 2} y={cy} width={candleWidth} height={Math.max(1.5, Math.abs(y(bar.close) - y(bar.open)))} rx=".5" /></g>; })}</g>
        {visibleLevels.map(level => <g key={level.id} className="chart-level"><line x1={left} x2={width - right} y1={y(level.price)} y2={y(level.price)} /><text x={left + 6} y={y(level.price) - 8}>{level.timeframe === '1M' ? 'Monthly level' : level.kind === 'support' ? 'Support' : 'Resistance'} {price(level.price)}</text></g>)}
        {last && <g className="chart-last"><line x1={left} x2={width - right} y1={y(last.close)} y2={y(last.close)} /><rect x={width - right + 1} y={y(last.close) - 10} width={right - 5} height="20" rx="3" /><text x={width - right + 8} y={y(last.close) + 4}>{price(last.close)}</text></g>}
        {hovered !== null && <line x1={x(selectedIndex)} x2={x(selectedIndex)} y1={top} y2={height - bottom} className="chart-crosshair" />}
      </svg>}
    </div>
    <div className="chart-caption"><span>{bars.length} closed candles · UTC</span>{quote ? <span>Bid <b>{price(quote.bid)}</b><span className="caption-divider">/</span>Ask <b>{price(quote.ask)}</b></span> : <span>Awaiting quote</span>}</div>
    {visibleLevels.length < levels.length && <div className="off-chart-levels">{levels.filter(level => !visibleLevels.includes(level)).map(level => <span key={level.id}>{level.timeframe} {level.kind} <b>{price(level.price)}</b><span className="muted"> · outside chart range</span></span>)}</div>}
  </section>;
}
