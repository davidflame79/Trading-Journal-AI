import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { planOutputs, PlanForm, PlanOptionSettings, TradePlanCard } from './TradePlans';
import { plansApi, tradesApi } from '../api';

jest.mock('../api', () => ({
  plansApi: { options: jest.fn(), saveOptions: jest.fn(), create: jest.fn(), update: jest.fn() },
  tradesApi: { getPlan: jest.fn(), attachPlan: jest.fn(), detachPlan: jest.fn() },
}));

test('a long works out the same figures as the setup sheet', () => {
  // the NU sheet: 50 shares, entry 14.015, stop 13.56, target 15.50, account 25,000
  // loss 0.455 x 50 = 22.75, profit 1.485 x 50 = 74.25, 1.485 / 0.455 = 3.26
  const out = planOutputs({ direction: 'LONG', entry_price: 14.015, stop_price: 13.56, target_price: 15.5, shares: 50, account_size: 25000 });
  expect(out.costBasis).toBeCloseTo(700.75);
  expect(out.risk).toBeCloseTo(22.75);
  expect(out.reward).toBeCloseTo(74.25);
  expect(out.riskReward).toBeCloseTo(3.26, 2);
  expect(out.riskPct).toBeCloseTo(0.091, 3);
  expect(out.profitPerShare).toBeCloseTo(1.485);
  expect(out.lossPerShare).toBeCloseTo(0.455);
});

test('a short risks the move up', () => {
  // entry 50, stop 52, target 44: loss 2, profit 6, R:R 3
  const out = planOutputs({ direction: 'SHORT', entry_price: 50, stop_price: 52, target_price: 44, shares: 10 });
  expect(out.risk).toBeCloseTo(20);
  expect(out.reward).toBeCloseTo(60);
  expect(out.riskReward).toBeCloseTo(3);
});

test('figures stay empty until their inputs are filled in', () => {
  const out = planOutputs({ direction: 'LONG', entry_price: '', stop_price: '13', target_price: '', shares: '50', account_size: '' });
  expect(Object.values(out).every(v => v === null)).toBe(true);
});

test('a stop on the wrong side gives no risk : reward', () => {
  expect(planOutputs({ direction: 'LONG', entry_price: 10, stop_price: 11, target_price: 12 }).riskReward).toBeNull();
});

describe('dropdowns', () => {
  const OPTIONS = { idea_sources: ['Scanner', 'Didi'], setup_types: ['Breakout'], higher_tf_trends: ['Uptrend'] };
  const ACCOUNTS = [{ id: 1, name: 'Swing' }];
  const choices = (select) => Array.from(select.options).map(o => o.textContent);

  beforeEach(() => {
    plansApi.options.mockResolvedValue({ data: OPTIONS });
  });

  test('the plan form offers the lists from Settings', async () => {
    render(<PlanForm accounts={ACCOUNTS} onSaved={jest.fn()} onCancel={jest.fn()} />);
    await screen.findByRole('option', { name: 'Didi' });
    expect(choices(screen.getByLabelText('Idea source'))).toEqual(['—', 'Scanner', 'Didi']);
    expect(choices(screen.getByLabelText('Setup type'))).toEqual(['—', 'Breakout']);
    expect(choices(screen.getByLabelText('Higher-TF trend'))).toEqual(['—', 'Uptrend']);
  });

  test('a saved value no longer on the list is kept', async () => {
    const plan = { id: 3, account_id: 1, symbol: 'NU', direction: 'LONG', status: 'planned', idea_source: 'NN Scanner' };
    plansApi.update.mockResolvedValue({ data: plan });
    render(<PlanForm plan={plan} accounts={ACCOUNTS} onSaved={jest.fn()} onCancel={jest.fn()} />);
    await screen.findByRole('option', { name: 'Didi' });
    expect(screen.getByLabelText('Idea source')).toHaveValue('NN Scanner');

    fireEvent.click(screen.getByRole('button', { name: 'Save plan' }));

    await waitFor(() => expect(plansApi.update).toHaveBeenCalled());
    expect(plansApi.update.mock.calls[0][1]).toMatchObject({ idea_source: 'NN Scanner', status: 'planned' });
  });

  test('an attached plan shows its status and does not send one', async () => {
    const plan = { id: 3, account_id: 1, symbol: 'NU', direction: 'LONG', status: 'active', trade_group: 'g', closed: true };
    plansApi.update.mockResolvedValue({ data: plan });
    render(<PlanForm plan={plan} accounts={ACCOUNTS} onSaved={jest.fn()} onCancel={jest.fn()} />);
    await screen.findByRole('option', { name: 'Didi' });
    expect(screen.getByLabelText('Status')).toBeDisabled();
    expect(choices(screen.getByLabelText('Status'))).toEqual(['Closed']);

    fireEvent.click(screen.getByRole('button', { name: 'Save plan' }));

    await waitFor(() => expect(plansApi.update).toHaveBeenCalled());
    expect(plansApi.update.mock.calls[0][1]).not.toHaveProperty('status');
  });

  test('Settings saves one choice per line', async () => {
    plansApi.saveOptions.mockResolvedValue({ data: { ...OPTIONS, idea_sources: ['NN Scanner', 'Didi'] } });
    render(<PlanOptionSettings />);
    const box = await screen.findByLabelText('Idea source');
    expect(box).toHaveValue('Scanner\nDidi');

    fireEvent.change(box, { target: { value: 'NN Scanner\nDidi' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save lists' }));

    expect(await screen.findByRole('status')).toHaveTextContent('Saved');
    expect(plansApi.saveOptions).toHaveBeenCalledWith({ ...OPTIONS, idea_sources: ['NN Scanner', 'Didi'] });
  });
});

describe('on a trade', () => {
  const TRADE = { id: 7, ticker: 'NU', side: 'LONG' };
  const PLAN = { id: 3, symbol: 'NU', direction: 'LONG', plan_date: '2026-10-30', setup_type: 'Trend Continuation', entry_trigger: 'Break of SMA5', entry_price: 14, stop_price: 13.5, target_price: 15.5, shares: 50 };

  test('attaching the one candidate shows the setup and tells the trade view', async () => {
    tradesApi.getPlan.mockResolvedValueOnce({ data: { plan: null, candidates: [PLAN] } });
    tradesApi.getPlan.mockResolvedValueOnce({ data: { plan: PLAN, candidates: [] } });
    tradesApi.attachPlan.mockResolvedValue({ data: PLAN });
    const onChanged = jest.fn();
    render(<TradePlanCard trade={TRADE} actualEntry={14.2} actualShares={50} onChanged={onChanged} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Attach plan' }));

    expect(await screen.findByText('Break of SMA5')).toBeInTheDocument();
    expect(tradesApi.attachPlan).toHaveBeenCalledWith(7, 3);
    expect(onChanged).toHaveBeenCalled();
    expect(screen.getByText('$14.20')).toBeInTheDocument();
  });

  test('with several candidates the trader picks one', async () => {
    tradesApi.getPlan.mockResolvedValue({ data: { plan: null, candidates: [PLAN, { ...PLAN, id: 4, setup_type: 'Reversal' }] } });
    tradesApi.attachPlan.mockResolvedValue({ data: PLAN });
    render(<TradePlanCard trade={TRADE} />);

    fireEvent.change(await screen.findByRole('combobox', { name: 'Plan to attach' }), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Attach plan' }));

    await waitFor(() => expect(tradesApi.attachPlan).toHaveBeenCalledWith(7, 4));
  });

  test('nothing shows when the symbol has no open plan', async () => {
    tradesApi.getPlan.mockResolvedValue({ data: { plan: null, candidates: [] } });
    const { container } = render(<TradePlanCard trade={TRADE} />);
    await waitFor(() => expect(tradesApi.getPlan).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
