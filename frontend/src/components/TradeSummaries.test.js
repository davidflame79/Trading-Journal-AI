import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { TradeSummaryCard, WriteSummaryButton } from './TradeSummaries';
import { diaryApi, tradesApi } from '../api';

jest.mock('../api', () => ({
  diaryApi: { listSummaries: jest.fn(), deleteSummary: jest.fn() },
  tradesApi: { getSummary: jest.fn(), writeSummary: jest.fn() },
}));

const TRADE = { id: 7, ticker: 'NU', side: 'LONG', net_pnl: 74.25, executions: '[]' };
const READY = {
  id: 1, status: 'ready', out_of_date: false, trade: TRADE,
  content: { headline: 'Clean breakout', story: 'You bought NU.', against_plan: 'On plan.', lessons: ['Keep sizing'] },
};

test('a finished summary shows its text, result and link, and offers no paid rewrite', () => {
  const open = jest.fn();
  render(<TradeSummaryCard summary={READY} onChanged={jest.fn()} onOpenDetail={open} />);
  expect(screen.getByText('Clean breakout')).toBeInTheDocument();
  expect(screen.getByText('+$74.25')).toBeInTheDocument();
  expect(screen.getByText('Keep sizing')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Write again|Generate/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Open trade' }));
  expect(open).toHaveBeenCalledWith(TRADE, [], 'Strategy');
});

test('a failed summary says why and can be tried again', async () => {
  tradesApi.writeSummary.mockResolvedValue({ data: { summary: READY } });
  const onChanged = jest.fn();
  render(<TradeSummaryCard summary={{ id: 1, status: 'failed', error: 'Add ANTHROPIC_API_KEY', trade: TRADE, content: null }} onChanged={onChanged} />);
  expect(screen.getByRole('alert')).toHaveTextContent('ANTHROPIC_API_KEY');

  fireEvent.click(screen.getByRole('button', { name: 'Generate summary' }));

  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  expect(tradesApi.writeSummary).toHaveBeenCalledWith(7);
});

test('a summary being written shows that and no button', () => {
  render(<TradeSummaryCard summary={{ id: 1, status: 'writing', trade: TRADE, content: null }} onChanged={jest.fn()} />);
  expect(screen.getByRole('status')).toHaveTextContent('Writing');
  expect(screen.queryByRole('button', { name: 'Generate summary' })).not.toBeInTheDocument();
});

test('a changed trade says so and offers to write again', () => {
  render(<TradeSummaryCard summary={{ ...READY, out_of_date: true }} onChanged={jest.fn()} />);
  expect(screen.getByRole('status')).toHaveTextContent('changed since');
  expect(screen.getByRole('button', { name: 'Write again' })).toBeInTheDocument();
});

test('deleting asks first and leaves the summary if declined', () => {
  jest.spyOn(window, 'confirm').mockReturnValue(false);
  render(<TradeSummaryCard summary={READY} onChanged={jest.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Delete NU summary' }));
  expect(diaryApi.deleteSummary).not.toHaveBeenCalled();
});

describe('on Trade View', () => {
  test('a closed trade without a summary can get one', async () => {
    tradesApi.getSummary.mockResolvedValue({ data: { summary: null } });
    tradesApi.writeSummary.mockResolvedValue({ data: { summary: READY } });
    render(<WriteSummaryButton trade={TRADE} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Write Diary summary' }));

    expect(await screen.findByText(/in your Diary/)).toBeInTheDocument();
  });

  test('a failed write shows why', async () => {
    tradesApi.getSummary.mockResolvedValue({ data: { summary: null } });
    tradesApi.writeSummary.mockResolvedValue({ data: { summary: { status: 'failed', error: 'Cut off', content: null } } });
    render(<WriteSummaryButton trade={TRADE} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Write Diary summary' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Cut off');
  });
});
