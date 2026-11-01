import { useState, useEffect, useCallback } from 'react';
import { Pencil, Trash2, MessageSquarePlus, Plus } from 'lucide-react';
import { tradesApi } from '../api';

/** Same string as fill_key in backend/trade_comments.py. */
export const fillKey = (e) =>
  `${e.date || ''}|${e.time || ''}|${e.action || ''}|${Number(e.qty || 0).toFixed(6)}|${Number(e.price || 0).toFixed(6)}`;

/**
 * Fills and comments in date order. Each fill carries the shares held and average cost
 * after it, and its own comments. A comment whose fill is gone shows as a note on its date.
 */
export function buildTimeline(execs, comments, side) {
  const fills = [...execs].sort((a, b) =>
    `${a.date || ''}T${a.time || ''}`.localeCompare(`${b.date || ''}T${b.time || ''}`));
  const entryAction = side === 'SHORT' ? 'SOLD' : 'BOT';
  const keys = new Set(fills.map(fillKey));
  const claimed = new Set();
  let held = 0;
  let avgCost = 0;

  const items = fills.map((fill) => {
    const qty = Number(fill.qty) || 0;
    if (fill.action === entryAction) {
      avgCost = held + qty ? (avgCost * held + Number(fill.price) * qty) / (held + qty) : 0;
      held += qty;
    } else {
      held -= qty;
    }
    const key = fillKey(fill);
    // Two identical fills share one key; their comments show on the first.
    const own = claimed.has(key) ? [] : comments.filter(c => c.fill_key === key);
    claimed.add(key);
    return { type: 'fill', sort: `${fill.date || ''}T${fill.time || ''}`, fill, key, held, avgCost, comments: own };
  });

  const notes = comments
    .filter(c => !c.fill_key || !keys.has(c.fill_key))
    .map(comment => ({ type: 'note', sort: `${comment.note_date}T99`, comment }));

  return [...items, ...notes].sort((a, b) => a.sort.localeCompare(b.sort));
}

const today = () => new Date().toLocaleDateString('en-CA');
const errorText = (e) => e.response?.data?.detail || e.message;

function CommentEditor({ initial = '', date, onDate, label, onSave, onCancel }) {
  const [body, setBody] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave(body);
    } catch (e) {
      setError(errorText(e));
      setSaving(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }}>
      {onDate && (
        <input type="date" aria-label="Note date" value={date} onChange={e => onDate(e.target.value)} style={{ maxWidth: 170 }} />
      )}
      <textarea
        aria-label={label}
        value={body}
        rows={3}
        autoFocus
        onChange={e => setBody(e.target.value)}
        style={{ width: '100%', resize: 'vertical', fontFamily: 'inherit' }}
      />
      {error && <div className="neg" role="alert" style={{ fontSize: 13 }}>{error}</div>}
      <div style={{ display: 'flex', gap: 6 }}>
        <button type="button" className="btn btn-primary btn-sm" onClick={save} disabled={saving || !body.trim()}>{saving ? 'Saving…' : 'Save'}</button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function Comment({ comment, onChanged }) {
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState(null);

  if (editing) {
    return (
      <CommentEditor
        initial={comment.body}
        label="Edit comment"
        onCancel={() => setEditing(false)}
        onSave={async (body) => {
          await tradesApi.updateComment(comment.id, { body });
          setEditing(false);
          onChanged();
        }}
      />
    );
  }

  const remove = async () => {
    if (!window.confirm('Delete this comment?')) return;
    setError(null);
    try {
      await tradesApi.deleteComment(comment.id);
      onChanged();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, marginTop: 4 }}>
      <div style={{ flex: 1, fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
        {comment.body}
        {error && <div className="neg" role="alert" style={{ fontSize: 13 }}>{error}</div>}
      </div>
      <button type="button" className="btn btn-ghost btn-icon" aria-label="Edit comment" title="Edit" onClick={() => setEditing(true)}><Pencil size={13} /></button>
      <button type="button" className="btn btn-ghost btn-icon" aria-label="Delete comment" title="Delete" onClick={remove}><Trash2 size={13} /></button>
    </div>
  );
}

const rowStyle = { padding: '10px 0', borderTop: '1px solid var(--divider)' };

function FillRow({ item, tradeId, onChanged }) {
  const { fill, key, held, avgCost, comments } = item;
  const [adding, setAdding] = useState(false);
  return (
    <div style={rowStyle}>
      <div className="mono" style={{ fontSize: 13.5, display: 'flex', flexWrap: 'wrap', gap: '2px 8px' }}>
        <span className="text-muted">{fill.date} {fill.time?.slice(0, 5)}</span>
        <span className={fill.action === 'BOT' ? 'pos' : 'neg'} style={{ fontWeight: 600 }}>
          {fill.action === 'BOT' ? 'BUY' : 'SELL'} {fill.qty} @ ${Number(fill.price).toFixed(2)}
        </span>
      </div>
      <div className="text-muted" style={{ fontSize: 12.5, marginTop: 2 }}>
        {held === 0 ? 'Position closed' : `Holding ${Math.abs(held)}, avg $${avgCost.toFixed(2)}`}
      </div>
      {comments.map(c => <Comment key={c.id} comment={c} onChanged={onChanged} />)}
      {adding ? (
        <CommentEditor
          label={`Comment on ${fill.action === 'BOT' ? 'buy' : 'sell'} of ${fill.qty} on ${fill.date}`}
          onCancel={() => setAdding(false)}
          onSave={async (body) => {
            await tradesApi.addComment(tradeId, { body, fill_key: key });
            setAdding(false);
            onChanged();
          }}
        />
      ) : comments.length === 0 && (
        <button type="button" className="btn btn-ghost btn-sm" style={{ marginTop: 4 }} onClick={() => setAdding(true)}>
          <MessageSquarePlus size={13} /> Add comment
        </button>
      )}
    </div>
  );
}

/** Fills as they happened, with the reasons written beside them. */
export default function TradeTimeline({ trade, executions, onCommentsChange }) {
  const [comments, setComments] = useState([]);
  const [loadError, setLoadError] = useState(null);
  const [addingNote, setAddingNote] = useState(false);
  const [noteDate, setNoteDate] = useState(today);

  const load = useCallback(() => {
    tradesApi.listComments(trade.id)
      .then(r => { setComments(r.data); setLoadError(null); })
      .catch(e => setLoadError(errorText(e)));
  }, [trade.id]);

  useEffect(() => { load(); }, [load, trade.executions]);
  useEffect(() => { if (onCommentsChange) onCommentsChange(); }, [comments, onCommentsChange]);

  const items = buildTimeline(executions, comments, trade.side);

  return (
    <section aria-label="Trade timeline" style={{ marginTop: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <div className="field-label">Timeline</div>
        {!addingNote && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setNoteDate(today()); setAddingNote(true); }}>
            <Plus size={13} /> Add note
          </button>
        )}
      </div>
      {addingNote && (
        <div style={{ marginBottom: 10 }}>
          <CommentEditor
            label="New note"
            date={noteDate}
            onDate={setNoteDate}
            onCancel={() => setAddingNote(false)}
            onSave={async (body) => {
              await tradesApi.addComment(trade.id, { body, note_date: noteDate });
              setAddingNote(false);
              load();
            }}
          />
        </div>
      )}
      {loadError && <div className="neg" role="alert" style={{ fontSize: 13 }}>{loadError}</div>}
      {items.map((item) => item.type === 'fill' ? (
        <FillRow key={`fill-${item.sort}-${item.key}`} item={item} tradeId={trade.id} onChanged={load} />
      ) : (
        <div key={`note-${item.comment.id}`} style={rowStyle}>
          <div className="mono text-muted" style={{ fontSize: 13.5 }}>{item.comment.note_date} · Note</div>
          <Comment comment={item.comment} onChanged={load} />
        </div>
      ))}
      {items.length === 0 && !loadError && (
        <div className="text-muted" style={{ fontSize: 14 }}>No fills or notes yet.</div>
      )}
    </section>
  );
}
