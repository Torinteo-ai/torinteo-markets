import type { Collection, DemoStep, Experiment, ExperimentDetail, Session, Snapshot, SourceKind } from './types';

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}
export async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? { Accept: 'application/json' } : { Accept: 'application/json', 'Content-Type': 'application/json' },
      credentials: 'same-origin', cache: 'no-store', signal,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError('The paper desk is unreachable. Check that the Torinteo server is running, then retry.', 0);
  }
  const text = await response.text();
  let data: Record<string, unknown>;
  try { data = JSON.parse(text); }
  catch { throw new ApiError('The server did not return a valid API response. Run the web app through the Torinteo Python server.', response.status); }
  if (!response.ok) {
    const detail = data.detail ?? data.error ?? data.message;
    const message = typeof detail === 'string' ? detail : detail && typeof detail === 'object' && 'message' in detail ? String(detail.message) : `Request failed (${response.status}).`;
    throw new ApiError(message, response.status);
  }
  return data as T;
}

export const api = {
  session: () => request<Session>('/session'),
  login: (token: string) => request<Session>('/login', { token }),
  logout: () => request('/logout', {}),
  experiments: () => request<{ experiments: Experiment[] }>('/experiments'),
  create: (name: string, kind: SourceKind, config?: Record<string, unknown>) => request<ExperimentDetail | Experiment>('/experiments', { name, kind, ...(config ? { config } : {}) }),
  detail: (id: string) => request<ExperimentDetail>(`/experiments/${encodeURIComponent(id)}`),
  demo: (id: string, step: DemoStep) => request<ExperimentDetail>(`/experiments/${encodeURIComponent(id)}/demo`, { step }),
  evaluate: (id: string, snapshot: Snapshot) => request<ExperimentDetail>(`/experiments/${encodeURIComponent(id)}/evaluate`, { snapshot }),
  advance: (id: string, envelope: unknown) => request<ExperimentDetail>(`/experiments/${encodeURIComponent(id)}/advance`, envelope),
  market: (symbol: string, timeframe = '15m', signal?: AbortSignal) => request<Collection>(`/market?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&limit=100`, undefined, signal),
  integrations: () => request<Record<string, unknown>>('/integrations'),
};

export function downloadJson(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + '\n'], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
