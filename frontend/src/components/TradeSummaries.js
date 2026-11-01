import { useState, useEffect, useCallback } from 'react';
import { Sparkles, Trash2 } from 'lucide-react';
import { diaryApi, tradesApi } from '../api';

const errorText = (e) => e.response?.data?.detail || e.message;
const POLL_MS = 5000;

/** Trade summaries for the Diary, refreshed while any is still being written. */
export function useTradeSummaries(accountId) {
  const [summaries, setSummaries] = useState([]);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    diaryApi.listSummaries(accountId != null ? { account_id: accountId } : {})
      .then(r => { setSummaries(r.data); setError(null); })
      .catch(e => setError(errorText(e)));
  }, [accountId]);
  useEffect(() => { load(); }, [load]);

  const writing = summaries.some(s => s.status === 'writing');
  useEffect(() => {
    if (!writing) return undefined;
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [writing, load]);

  return { summaries, error, reload: load };
}

export function TradeSummaryCard({ summary, onChanged, onOpenDetail }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const { trade, content, status } = summary;
  const pnl = trade.net_pnl ?? 0;

  const write = async () => {
    setBusy(true);
    setError(null);
    try {
      await tradesApi.writeSummary(trade.id);
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!window.confirm(`Delete the ${trade.ticker} summary? The trade itself stays.`)) return;
    try {
      await diaryApi.deleteSummary(summary.id);
      onChanged();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <article className="card" style={{ marginBottom: 10, padding: 'var(--space-4)' }} aria-label={`${trade.ticker} trade summary`}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ fontWeight: 700 }}>{trade.ticker} · {trade.side === 'SHORT' ? 'Short' : 'Long'}</span>
        <span className={`num ${pnl >= 0 ? 'pos' : 'neg'}`} style={{ fontWeight: 600 }}>
          {pnl >= 0 ? '+' : '-'}${Math.abs(pnl).toFixed(2)}
        </span>
        <span className="chip">Trade summary</span>
        <span style={{ flex: 1 }} />
        {onOpenDetail && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => onOpenDetail(trade, [], 'Strategy')}>Open trade</button>
        )}
        <button type="button" className="btn btn-ghost btn-icon" aria-label={`Delete ${trade.ticker} summary`} title="Delete" onClick={remove}><Trash2 size={13} /></button>
      </div>

      {status === 'writing' && <div className="text-muted" role="status" style={{ fontSize: 14 }}>Writing this summary…</div>}
      {status === 'failed' && <div className="notice neg" role="alert" style={{ fontSize: 13, marginBottom: 8 }}>{summary.error}</div>}
      {status === 'pending' && !content && (
        <div className="text-muted" style={{ fontSize: 14, marginBottom: 8 }}>
          This import closed several trades, so this one waits for you to ask.
        </div>
      )}
      {error && <div className="notice neg" role="alert" style={{ fontSize: 13, marginBottom: 8 }}>{error}</div>}
      {summary.out_of_date && status === 'ready' && (
        <div className="notice accent" role="status" style={{ fontSize: 13, marginBottom: 8 }}>The trade has changed since this was written.</div>
      )}

      {content && status !== 'writing' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 14, lineHeight: 1.55 }}>
          {content.headline && <div style={{ fontWeight: 600 }}>{content.headline}</div>}
          <div style={{ whiteSpace: 'pre-wrap' }}>{content.story}</div>
          {content.against_plan && (
            <div>
              <div className="field-label" style={{ marginBottom: 4 }}>Against the plan</div>
              {content.against_plan}
            </div>
          )}
          {content.lessons?.length > 0 && (
            <div>
              <div className="field-label" style={{ marginBottom: 4 }}>Lessons</div>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {content.lessons.map((lesson, i) => <li key={i}>{lesson}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}

      {status !== 'writing' && (status !== 'ready' || summary.out_of_date) && (
        <button type="button" className="btn btn-primary btn-sm" style={{ marginTop: 10 }} disabled={busy} onClick={write}>
          <Sparkles size={13} /> {busy ? 'Writing…' : content ? 'Write again' : 'Generate summary'}
        </button>
      )}
    </article>
  );
}

/** On a closed trade's feedback panel: reaches trades closed before summaries existed, or too old to get one by themselves. */
export function WriteSummaryButton({ trade }) {
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let current = true;
    tradesApi.getSummary(trade.id)
      .then(r => { if (current) setState(r.data.summary || 'none'); })
      .catch(e => { if (current) setError(errorText(e)); });
    return () => { current = false; };
  }, [trade.id, trade.executions]);

  const write = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await tradesApi.writeSummary(trade.id);
      setState(res.data.summary);
      if (res.data.summary.status === 'failed') setError(res.data.summary.error);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (error && !state) return <div className="neg" role="alert" style={{ fontSize: 13 }}>{error}</div>;
  if (!state) return null;
  if (state.status === 'ready' && !state.out_of_date) {
    return <div className="text-muted" style={{ fontSize: 13 }}>The summary of this trade is in your Diary.</div>;
  }
  if (state.status === 'writing') return <div className="text-muted" style={{ fontSize: 13 }}>Writing the Diary summary…</div>;
  return (
    <div>
      {error && <div className="neg" role="alert" style={{ fontSize: 13, marginBottom: 6 }}>{error}</div>}
      <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={write}>
        <Sparkles size={13} /> {busy ? 'Writing…' : state.content ? 'Rewrite Diary summary' : 'Write Diary summary'}
      </button>
    </div>
  );
}
