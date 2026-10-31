import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import TradeTimeline, { buildTimeline, fillKey } from './TradeTimeline';
import { tradesApi } from '../api';

jest.mock('../api', () => ({
  tradesApi: { listComments: jest.fn(), addComment: jest.fn(), updateComment: jest.fn(), deleteComment: jest.fn() },
}));

const fill = (action, date, time, qty, price) => ({ action, date, time, qty, price, commission: 0 });
const BUY_1 = fill('BOT', '2026-10-19', '10:00:00', 2, 100);
const BUY_2 = fill('BOT', '2026-10-21', '12:35:00', 2, 110);
const SELL_1 = fill('SOLD', '2026-10-22', '11:00:00', 1, 120);
const SELL_2 = fill('SOLD', '2026-10-23', '15:20:00', 3, 90);

test('fills run in date order with the shares held and average cost after each', () => {
  const items = buildTimeline([SELL_2, BUY_2, SELL_1, BUY_1], [], 'LONG');
  expect(items.map(i => [i.fill.date, i.held, i.avgCost])).toEqual([
    ['2026-10-19', 2, 100],
    ['2026-10-21', 4, 105], // (2 x 100 + 2 x 110) / 4
    ['2026-10-22', 3, 105], // selling does not change the cost of what is left
    ['2026-10-23', 0, 105],
  ]);
});

test('a short opens on the sell', () => {
  const items = buildTimeline([
    fill('SOLD', '2026-10-19', '10:00:00', 4, 50),
    fill('BOT', '2026-10-20', '10:00:00', 4, 40),
  ], [], 'SHORT');
  expect(items.map(i => [i.held, i.avgCost])).toEqual([[4, 50], [0, 50]]);
});

test('comments sit on their fill, and notes on their date after that day\'s fills', () => {
  const onFill = { id: 1, fill_key: fillKey(BUY_2), note_date: '2026-10-21', body: 'Added at support' };
  const note = { id: 2, fill_key: null, note_date: '2026-10-21', body: 'Moved stop' };
  const earlyNote = { id: 3, fill_key: null, note_date: '2026-10-20', body: 'Earnings next week' };
  const items = buildTimeline([BUY_1, BUY_2], [onFill, note, earlyNote], 'LONG');
  expect(items.map(i => i.type === 'fill' ? i.fill.date : i.comment.body))
    .toEqual(['2026-10-19', 'Earnings next week', '2026-10-21', 'Moved stop']);
  expect(items[2].comments).toEqual([onFill]);
});

test('a comment whose fill is gone stays visible as a note', () => {
  const orphan = { id: 1, fill_key: 'gone', note_date: '2026-10-20', body: 'Why I added' };
  const items = buildTimeline([BUY_1], [orphan], 'LONG');
  expect(items[1]).toMatchObject({ type: 'note', comment: orphan });
});

test('the fill key matches the backend format', () => {
  expect(fillKey({ date: '2026-10-19', time: '10:00:00', action: 'BOT', qty: 2, price: 361.8 }))
    .toBe('2026-10-19|10:00:00|BOT|2.000000|361.800000');
});

describe('on screen', () => {
  const TRADE = { id: 7, side: 'LONG', executions: '[]' };
  let stored;

  beforeEach(() => {
    stored = [];
    tradesApi.listComments.mockImplementation(() => Promise.resolve({ data: stored }));
    tradesApi.addComment.mockImplementation((id, data) => {
      const comment = { id: stored.length + 1, fill_key: null, note_date: '2026-10-19', ...data };
      stored = [...stored, comment];
      return Promise.resolve({ data: comment });
    });
  });

  test('a comment is saved against its fill and replaces the add button', async () => {
    render(<TradeTimeline trade={TRADE} executions={[BUY_1]} />);
    expect(await screen.findByText('Holding 2, avg $100.00')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add comment' }));
    fireEvent.change(screen.getByRole('textbox', { name: /Comment on buy of 2/ }), { target: { value: 'Starter at support' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Starter at support', { ignore: 'textarea' })).toBeInTheDocument();
    expect(tradesApi.addComment).toHaveBeenCalledWith(7, { body: 'Starter at support', fill_key: fillKey(BUY_1) });
    expect(screen.queryByRole('button', { name: 'Add comment' })).not.toBeInTheDocument();
  });

  test('a note with no fill is saved with the date picked', async () => {
    render(<TradeTimeline trade={TRADE} executions={[BUY_1]} />);
    await waitFor(() => expect(tradesApi.listComments).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: 'Add note' }));
    fireEvent.change(screen.getByLabelText('Note date'), { target: { value: '2026-10-20' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'New note' }), { target: { value: 'Moved stop' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    const timeline = screen.getByRole('region', { name: 'Trade timeline' });
    expect(await within(timeline).findByText('Moved stop', { ignore: 'textarea' })).toBeInTheDocument();
    expect(tradesApi.addComment).toHaveBeenCalledWith(7, { body: 'Moved stop', note_date: '2026-10-20' });
  });

  test('a failed save keeps the text and shows why', async () => {
    tradesApi.addComment.mockImplementation(() => Promise.reject({ response: { data: { detail: 'Trade not found' } } }));
    render(<TradeTimeline trade={TRADE} executions={[BUY_1]} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add comment' }));
    const box = screen.getByRole('textbox', { name: /Comment on buy/ });
    fireEvent.change(box, { target: { value: 'Keep me' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Trade not found');
    expect(box).toHaveValue('Keep me');
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
  });
});
