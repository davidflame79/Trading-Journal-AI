import { useState, useEffect, useCallback } from 'react';
import { Plus, Pencil, Trash2, Ban, RotateCcw, Link2Off } from 'lucide-react';
import { plansApi, tradesApi } from '../api';
import { PageHeader } from './ui';

const errorText = (e) => e.response?.data?.detail || e.message;
const num = (v) => (v === '' || v == null || Number.isNaN(Number(v)) ? null : Number(v));
const usd = (v) => (v == null ? '—' : `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

/** The sheet's calculated outputs. A figure is null until the inputs it needs are filled in. */
export function planOutputs(plan) {
  const entry = num(plan.entry_price);
  const stop = num(plan.stop_price);
  const target = num(plan.target_price);
  const shares = num(plan.shares);
  const account = num(plan.account_size);
  const sign = plan.direction === 'SHORT' ? -1 : 1;
  const profitPerShare = entry != null && target != null ? sign * (target - entry) : null;
  const lossPerShare = entry != null && stop != null ? sign * (entry - stop) : null;
  const risk = lossPerShare != null && shares != null ? lossPerShare * shares : null;
  return {
    costBasis: entry != null && shares != null ? entry * shares : null,
    risk,
    reward: profitPerShare != null && shares != null ? profitPerShare * shares : null,
    riskReward: profitPerShare != null && lossPerShare > 0 ? profitPerShare / lossPerShare : null,
    riskPct: risk != null && account ? (risk / account) * 100 : null,
    profitPerShare,
    lossPerShare,
  };
}

const EMPTY_PLAN = {
  symbol: '', direction: 'LONG', status: 'planned', plan_date: '', idea_source: '', setup_type: '', timeframe: '',
  higher_tf_trend: '', key_level: '', volume_momentum: '', entry_trigger: '', invalidation: '',
  entry_price: '', stop_price: '', target_price: '', shares: '', account_size: '',
};

function Field({ label, children, wide }) {
  return (
    <label style={{ display: 'block', flex: wide ? '1 1 100%' : '1 1 170px', minWidth: 0 }}>
      <span className="field-label" style={{ marginBottom: 4 }}>{label}</span>
      {children}
    </label>
  );
}

const inputStyle = { width: '100%', fontSize: 14, minHeight: 34, padding: '5px 9px', boxSizing: 'border-box' };

export function PlanForm({ plan, accounts, accountId, defaultAccountSize, onSaved, onCancel }) {
  const attached = Boolean(plan?.trade_group);
  const [form, setForm] = useState(() => {
    const start = { ...EMPTY_PLAN, account_id: accountId ?? accounts[0]?.id ?? '', account_size: defaultAccountSize ?? '', plan_date: new Date().toLocaleDateString('en-CA') };
    if (!plan) return start;
    const filled = { ...start };
    Object.keys(EMPTY_PLAN).forEach((k) => { filled[k] = plan[k] ?? ''; });
    return { ...filled, account_id: plan.account_id };
  });
  const [options, setOptions] = useState({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => {
    plansApi.options().then(r => setOptions(r.data)).catch(e => setError(errorText(e)));
  }, []);
  const set = (key) => (e) => setForm(f => ({ ...f, [key]: e.target.value }));
  const text = (key, extra = {}) => <input type="text" value={form[key]} onChange={set(key)} style={inputStyle} {...extra} />;
  const number = (key) => <input type="number" step="any" min="0" value={form[key]} onChange={set(key)} style={inputStyle} />;
  const area = (key) => <textarea rows={3} value={form[key]} onChange={set(key)} style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }} />;
  // A value no longer on the list stays selectable, so saving never changes it unasked.
  const choice = (key, listKey) => {
    const list = options[listKey] || [];
    return (
      <select value={form[key]} onChange={set(key)} style={inputStyle}>
        <option value="">—</option>
        {form[key] && !list.includes(form[key]) && <option value={form[key]}>{form[key]}</option>}
        {list.map(name => <option key={name} value={name}>{name}</option>)}
      </select>
    );
  };
  const out = planOutputs(form);

  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    const body = { ...form, account_id: Number(form.account_id), plan_date: form.plan_date || null };
    ['entry_price', 'stop_price', 'target_price', 'shares', 'account_size'].forEach((k) => { body[k] = num(form[k]); });
    if (attached) delete body.status;
    try {
      const res = plan ? await plansApi.update(plan.id, body) : await plansApi.create(body);
      onSaved(res.data);
    } catch (err) {
      setError(errorText(err));
      setSaving(false);
    }
  };

  return (
    <form onSubmit={save} style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }} aria-label={plan ? 'Edit plan' : 'New plan'}>
      <Field label="Symbol">{text('symbol', { required: true, disabled: attached, style: { ...inputStyle, textTransform: 'uppercase' } })}</Field>
      <Field label="Direction">
        <select value={form.direction} onChange={set('direction')} disabled={attached} style={inputStyle}>
          <option value="LONG">Long</option>
          <option value="SHORT">Short</option>
        </select>
      </Field>
      <Field label="Account">
        <select value={form.account_id} onChange={set('account_id')} disabled={attached} required style={inputStyle}>
          {accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </Field>
      <Field label="Status">
        {attached ? (
          <select value="linked" disabled style={inputStyle}>
            <option value="linked">{plan.closed ? 'Closed' : 'Filled'}</option>
          </select>
        ) : (
          <select value={form.status} onChange={set('status')} style={inputStyle}>
            <option value="planned">Planned</option>
            <option value="cancelled">Cancelled</option>
          </select>
        )}
      </Field>
      <Field label="Date"><input type="date" value={form.plan_date} onChange={set('plan_date')} style={inputStyle} /></Field>
      <Field label="Idea source">{choice('idea_source', 'idea_sources')}</Field>
      <Field label="Setup type">{choice('setup_type', 'setup_types')}</Field>
      <Field label="Timeframe">{text('timeframe')}</Field>
      <Field label="Higher-TF trend">{choice('higher_tf_trend', 'higher_tf_trends')}</Field>
      <Field label="Key S/R level">{text('key_level')}</Field>
      <Field label="Volume / momentum">{text('volume_momentum')}</Field>
      <Field label="Entry trigger" wide>{area('entry_trigger')}</Field>
      <Field label="Invalidation" wide>{area('invalidation')}</Field>
      <Field label="Entry price">{number('entry_price')}</Field>
      <Field label="Stop-loss price">{number('stop_price')}</Field>
      <Field label="Profit target">{number('target_price')}</Field>
      <Field label="Number of shares">{number('shares')}</Field>
      <Field label="Account size ($)">{number('account_size')}</Field>
      <div style={{ flex: '1 1 100%' }}><Outputs out={out} /></div>
      {out.lossPerShare != null && out.lossPerShare <= 0 && (
        <div className="neg" role="alert" style={{ flex: '1 1 100%', fontSize: 13 }}>
          The stop is on the wrong side of the entry for a {form.direction === 'SHORT' ? 'short' : 'long'}.
        </div>
      )}
      {error && <div className="neg" role="alert" style={{ flex: '1 1 100%', fontSize: 13 }}>{error}</div>}
      <div style={{ flex: '1 1 100%', display: 'flex', gap: 6 }}>
        <button type="submit" className="btn btn-primary btn-sm" disabled={saving}>{saving ? 'Saving…' : 'Save plan'}</button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function Fact({ label, value, wide }) {
  if (value == null || value === '') return null;
  return (
    <div style={{ flex: wide ? '1 1 100%' : '1 1 130px', minWidth: 0 }}>
      <div className="field-label" style={{ marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{value}</div>
    </div>
  );
}

function Outputs({ out }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, padding: 10, background: 'var(--surface-inset)', borderRadius: 'var(--radius-md)' }}>
      <Fact label="Cost basis" value={usd(out.costBasis)} />
      <Fact label="Risk per trade" value={usd(out.risk)} />
      <Fact label="Reward potential" value={usd(out.reward)} />
      <Fact label="Risk : reward" value={out.riskReward == null ? '—' : `${out.riskReward.toFixed(2)}x`} />
      <Fact label="Risk % of account" value={out.riskPct == null ? '—' : `${out.riskPct.toFixed(2)}%`} />
      <Fact label="Profit / share" value={usd(out.profitPerShare)} />
      <Fact label="Loss / share" value={usd(out.lossPerShare)} />
    </div>
  );
}

function PlanFacts({ plan }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
      <Fact label="Date" value={plan.plan_date} />
      <Fact label="Idea source" value={plan.idea_source} />
      <Fact label="Setup type" value={plan.setup_type} />
      <Fact label="Timeframe" value={plan.timeframe} />
      <Fact label="Higher-TF trend" value={plan.higher_tf_trend} />
      <Fact label="Key S/R level" value={plan.key_level} />
      <Fact label="Volume / momentum" value={plan.volume_momentum} />
      <Fact label="Entry trigger" value={plan.entry_trigger} wide />
      <Fact label="Invalidation" value={plan.invalidation} wide />
      <Fact label="Entry price" value={plan.entry_price == null ? null : usd(plan.entry_price)} />
      <Fact label="Stop-loss" value={plan.stop_price == null ? null : usd(plan.stop_price)} />
      <Fact label="Target" value={plan.target_price == null ? null : usd(plan.target_price)} />
      <Fact label="Shares" value={plan.shares} />
      <Fact label="Account size" value={plan.account_size == null ? null : usd(plan.account_size)} />
    </div>
  );
}

const planLabel = (p) => [p.plan_date, p.setup_type, p.entry_price != null ? `entry ${usd(p.entry_price)}` : null].filter(Boolean).join(' · ') || `Plan ${p.id}`;

/** Offers a trade's possible plans. With several, the trader picks: same ticker alone proves nothing. */
function AttachPlan({ tradeId, plans, onAttached }) {
  const [planId, setPlanId] = useState(plans[0].id);
  const [error, setError] = useState(null);
  const attach = async () => {
    setError(null);
    try {
      await tradesApi.attachPlan(tradeId, Number(planId));
      onAttached();
    } catch (e) {
      setError(errorText(e));
    }
  };
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
      {plans.length > 1 ? (
        <select aria-label="Plan to attach" value={planId} onChange={e => setPlanId(e.target.value)} style={{ ...inputStyle, width: 'auto', maxWidth: '100%' }}>
          {plans.map(p => <option key={p.id} value={p.id}>{planLabel(p)}</option>)}
        </select>
      ) : <span style={{ fontSize: 13.5 }}>{planLabel(plans[0])}</span>}
      <button type="button" className="btn btn-primary btn-sm" onClick={attach}>Attach plan</button>
      {error && <span className="neg" role="alert" style={{ fontSize: 13 }}>{error}</span>}
    </div>
  );
}

/** The setup at the top of a trade's Strategy tab, with plan against actual. */
export function TradePlanCard({ trade, accounts = [], actualEntry, actualShares, onChanged }) {
  const [state, setState] = useState(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    tradesApi.getPlan(trade.id).then(r => setState(r.data)).catch(e => setError(errorText(e)));
  }, [trade.id]);
  useEffect(() => { load(); }, [load]);

  const refresh = () => { load(); if (onChanged) onChanged(); };

  const detach = async () => {
    if (!window.confirm('Detach this plan from the trade? It goes back to Planned, and the stop, target and reason it filled in are cleared unless you changed them.')) return;
    try {
      await tradesApi.detachPlan(trade.id);
      refresh();
    } catch (e) {
      setError(errorText(e));
    }
  };

  if (error) return <div className="neg" role="alert" style={{ fontSize: 13 }}>{error}</div>;
  if (!state) return null;
  const { plan, candidates } = state;

  if (!plan) {
    if (!candidates.length) return null;
    return (
      <section aria-label="Trade plan">
        <div className="field-label" style={{ marginBottom: 6 }}>Planned setups for {trade.ticker}</div>
        <AttachPlan tradeId={trade.id} plans={candidates} onAttached={refresh} />
      </section>
    );
  }

  if (editing) {
    return (
      <section aria-label="Trade plan">
        <PlanForm plan={plan} accounts={accounts} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); refresh(); }} />
      </section>
    );
  }

  return (
    <section aria-label="Trade plan" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div className="field-label">Setup</div>
        <div style={{ display: 'flex', gap: 4 }}>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(true)}><Pencil size={13} /> Edit plan</button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={detach}><Link2Off size={13} /> Detach</button>
        </div>
      </div>
      <PlanFacts plan={plan} />
      <Outputs out={planOutputs(plan)} />
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        <Fact label="Actual average entry" value={actualEntry ? usd(actualEntry) : null} />
        <Fact label="Actual shares" value={actualShares || null} />
      </div>
    </section>
  );
}

/** Trades an import just opened that a planned setup could belong to. */
export function PlanMatches({ matches }) {
  const [done, setDone] = useState({});
  const open = matches.filter(m => !done[m.trade.id]);
  if (!open.length) return null;
  return (
    <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {open.map(({ trade, plans }) => (
        <div key={trade.id}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>New {trade.ticker} trade: attach a plan?</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
            <AttachPlan tradeId={trade.id} plans={plans} onAttached={() => setDone(d => ({ ...d, [trade.id]: true }))} />
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setDone(d => ({ ...d, [trade.id]: true }))}>Not this trade</button>
          </div>
        </div>
      ))}
    </div>
  );
}

const STATUS_LABEL = { planned: 'Planned', active: 'Filled', cancelled: 'Cancelled' };
const STATUS_ORDER = ['planned', 'active', 'closed', 'cancelled'];
const statusOf = (p) => (p.closed ? 'closed' : p.status);

function PlanRow({ plan, accounts, onChanged, onOpenDetail }) {
  const [editing, setEditing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState(null);
  const run = async (action) => {
    setError(null);
    try {
      await action();
      onChanged();
    } catch (e) {
      setError(errorText(e));
    }
  };
  const out = planOutputs(plan);

  if (editing) {
    return (
      <div className="card" style={{ padding: 'var(--space-4)' }}>
        <PlanForm plan={plan} accounts={accounts} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); onChanged(); }} />
      </div>
    );
  }

  return (
    <div className="card" style={{ padding: 'var(--space-4)' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
        <button type="button" className="btn btn-ghost btn-sm" aria-expanded={expanded} onClick={() => setExpanded(x => !x)} style={{ fontWeight: 700 }}>
          {plan.symbol} · {plan.direction === 'SHORT' ? 'Short' : 'Long'}
        </button>
        <span className="text-muted" style={{ fontSize: 13, flex: 1, minWidth: 120 }}>
          {[plan.plan_date, plan.setup_type, out.riskReward != null ? `R:R ${out.riskReward.toFixed(2)}x` : null].filter(Boolean).join(' · ')}
        </span>
        {plan.trade && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => onOpenDetail(plan.trade, [], 'Strategy')}>Open trade</button>
        )}
        <button type="button" className="btn btn-ghost btn-icon" aria-label={`Edit plan for ${plan.symbol}`} title="Edit" onClick={() => setEditing(true)}><Pencil size={13} /></button>
        {plan.status === 'planned' && (
          <button type="button" className="btn btn-ghost btn-icon" aria-label={`Cancel plan for ${plan.symbol}`} title="Not taken" onClick={() => run(() => plansApi.setStatus(plan.id, 'cancelled'))}><Ban size={13} /></button>
        )}
        {plan.status === 'cancelled' && (
          <button type="button" className="btn btn-ghost btn-icon" aria-label={`Restore plan for ${plan.symbol}`} title="Back to planned" onClick={() => run(() => plansApi.setStatus(plan.id, 'planned'))}><RotateCcw size={13} /></button>
        )}
        {!plan.trade_group && (
          <button
            type="button"
            className="btn btn-ghost btn-icon"
            aria-label={`Delete plan for ${plan.symbol}`}
            title="Delete"
            onClick={() => { if (window.confirm(`Delete this ${plan.symbol} plan?`)) run(() => plansApi.delete(plan.id)); }}
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>
      {error && <div className="neg" role="alert" style={{ fontSize: 13, marginTop: 6 }}>{error}</div>}
      {expanded && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 10 }}>
          <PlanFacts plan={plan} />
          <Outputs out={out} />
        </div>
      )}
    </div>
  );
}

const OPTION_LISTS = [
  { key: 'idea_sources', label: 'Idea source' },
  { key: 'setup_types', label: 'Setup type' },
  { key: 'higher_tf_trends', label: 'Higher-TF trend' },
];
const toLines = (options) => Object.fromEntries(OPTION_LISTS.map(({ key }) => [key, (options[key] || []).join('\n')]));

/** Settings > General: what the plan form's dropdowns offer, one choice per line. */
export function PlanOptionSettings() {
  const [lines, setLines] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    plansApi.options().then(r => setLines(toLines(r.data))).catch(e => setError(errorText(e)));
  }, []);

  const save = async () => {
    setSaving(true);
    setError(null);
    setNotice(null);
    const body = Object.fromEntries(OPTION_LISTS.map(({ key }) => [key, lines[key].split('\n')]));
    try {
      const res = await plansApi.saveOptions(body);
      setLines(toLines(res.data));
      setNotice('Saved. Plans already written keep their values.');
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card" style={{ padding: 'var(--space-5)', maxWidth: 560, marginTop: 'var(--space-4)' }}>
      <h3 style={{ marginTop: 0 }}>Plan dropdowns</h3>
      <p className="text-muted" style={{ fontSize: 13, marginBottom: 'var(--space-4)' }}>
        The choices offered when you write a plan. Put one on each line, in the order you want them.
      </p>
      {error && <div className="notice neg" role="alert" style={{ marginBottom: 12 }}>{error}</div>}
      {notice && <div className="notice pos" role="status" style={{ marginBottom: 12 }}>{notice}</div>}
      {lines && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {OPTION_LISTS.map(({ key, label }) => (
            <Field key={key} label={label} wide>
              <textarea
                rows={Math.max(3, lines[key].split('\n').length)}
                value={lines[key]}
                onChange={e => { const { value } = e.target; setLines(l => ({ ...l, [key]: value })); }}
                style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }}
              />
            </Field>
          ))}
          <div>
            <button type="button" className="btn btn-primary btn-sm" disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Save lists'}</button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Plans({ accounts = [], accountId, onOpenDetail }) {
  const [plans, setPlans] = useState(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    plansApi.list(accountId != null ? { account_id: accountId } : {})
      .then(r => { setPlans(r.data); setError(null); })
      .catch(e => setError(errorText(e)));
  }, [accountId]);
  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <PageHeader
        title="Plans"
        subtitle="Write the setup before you enter. The first matching import offers to attach it to the trade."
      />
      {error && <div className="notice neg" role="alert" style={{ marginBottom: 12 }}>{error}</div>}
      {creating ? (
        <div className="card" style={{ padding: 'var(--space-4)', marginBottom: 16 }}>
          <PlanForm
            accounts={accounts}
            accountId={accountId}
            defaultAccountSize={plans?.[0]?.account_size}
            onCancel={() => setCreating(false)}
            onSaved={() => { setCreating(false); load(); }}
          />
        </div>
      ) : (
        <button type="button" className="btn btn-primary" style={{ marginBottom: 16 }} disabled={!accounts.length} onClick={() => setCreating(true)}>
          <Plus size={15} /> New plan
        </button>
      )}
      {plans && plans.length === 0 && !creating && (
        <div className="text-muted" style={{ fontSize: 14 }}>No plans yet.</div>
      )}
      {STATUS_ORDER.map((status) => {
        const group = (plans || []).filter(p => statusOf(p) === status);
        if (!group.length) return null;
        return (
          <section key={status} aria-label={`${STATUS_LABEL[status] || 'Closed'} plans`} style={{ marginBottom: 20 }}>
            <h2 className="section-title" style={{ marginBottom: 8 }}>{STATUS_LABEL[status] || 'Closed'}</h2>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {group.map(p => <PlanRow key={p.id} plan={p} accounts={accounts} onChanged={load} onOpenDetail={onOpenDetail} />)}
            </div>
          </section>
        );
      })}
    </div>
  );
}
