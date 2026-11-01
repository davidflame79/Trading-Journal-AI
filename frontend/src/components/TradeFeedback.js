import { useState, useEffect } from 'react';
import { Sparkles } from 'lucide-react';
import { tradesApi } from '../api';
import { PanelHead } from './ui';

const errorText = (e) => e.response?.data?.detail || e.message;

function Points({ label, items }) {
  if (!items?.length) return null;
  return (
    <div>
      <div className="field-label" style={{ marginBottom: 4 }}>{label}</div>
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 14, lineHeight: 1.5 }}>
        {items.map((item, i) => <li key={i}>{item}</li>)}
      </ul>
    </div>
  );
}

function Passage({ label, text }) {
  if (!text) return null;
  return (
    <div>
      <div className="field-label" style={{ marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 14, lineHeight: 1.5 }}>{text}</div>
    </div>
  );
}

/** Brain's feedback beside the chart. It runs only on a click, since each run costs API credit. */
export default function TradeFeedback({ trade, changed, closed }) {
  const [state, setState] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let current = true;
    tradesApi.getReview(trade.id)
      .then(r => { if (current) setState(r.data); })
      .catch(e => { if (current) setError(errorText(e)); });
    return () => { current = false; };
  }, [trade.id, trade.executions, changed]);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      const res = await tradesApi.runReview(trade.id);
      setState(res.data);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setRunning(false);
    }
  };

  const review = state?.review;
  return (
    <section className="card td-feedback" aria-label="Trade feedback">
      <PanelHead title="Brain Feedback" />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {!review && state && (
          <div className="text-muted" style={{ fontSize: 13.5, lineHeight: 1.5 }}>
            Reviews this trade's plan, fills, comments and daily prices. Each run uses your Anthropic API credit.
          </div>
        )}
        {error && <div className="notice neg" role="alert" style={{ fontSize: 13 }}>{error}</div>}
        {review && state.out_of_date && (
          <div className="notice accent" role="status" style={{ fontSize: 13 }}>
            The trade has changed since this feedback was written.
          </div>
        )}
        {review && (
          <>
            {review.grade && <div><span className="chip">Process grade {review.grade}</span></div>}
            <div style={{ fontSize: 14, lineHeight: 1.55 }}>{review.summary}</div>
            <Passage label="Against the plan" text={review.plan_check} />
            <Points label="Went well" items={review.went_well} />
            <Points label="To improve" items={review.improve} />
            <Passage label={closed ? 'Lesson for next time' : 'What to watch'} text={review.next_step} />
            <div className="text-muted" style={{ fontSize: 12 }}>Written {state.generated_at} UTC</div>
          </>
        )}
        {state && (
          <div>
            <button type="button" className={`btn btn-sm ${review ? 'btn-ghost' : 'btn-primary'}`} disabled={running} onClick={run}>
              <Sparkles size={13} /> {running ? 'Analysing…' : review ? 'Analyse again' : 'Analyse trade'}
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
