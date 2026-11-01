import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import TradeFeedback from './TradeFeedback';
import { tradesApi } from '../api';

jest.mock('../api', () => ({
  diaryApi: {},
  tradesApi: { getReview: jest.fn(), runReview: jest.fn(), getSummary: jest.fn(), writeSummary: jest.fn() },
}));

const TRADE = { id: 7, executions: '[]' };
const SAVED = {
  review: { grade: 'B', summary: 'Followed the plan.', plan_check: 'Entry on trigger.', went_well: ['Sized to plan'], improve: ['Note the exit reason'], next_step: 'Watch the stop at 13.56.' },
  generated_at: '2026-11-01 10:00:00',
  out_of_date: false,
};

test('nothing runs until asked, then the feedback shows', async () => {
  tradesApi.getReview.mockResolvedValue({ data: { review: null } });
  tradesApi.runReview.mockResolvedValue({ data: SAVED });
  render(<TradeFeedback trade={TRADE} />);

  const button = await screen.findByRole('button', { name: 'Analyse trade' });
  expect(tradesApi.runReview).not.toHaveBeenCalled();
  fireEvent.click(button);

  expect(await screen.findByText('Followed the plan.')).toBeInTheDocument();
  expect(screen.getByText('Process grade B')).toBeInTheDocument();
  expect(screen.getByText('Sized to plan')).toBeInTheDocument();
  expect(screen.getByText('What to watch')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Analyse again' })).toBeEnabled();
});

test('a closed trade calls the last section a lesson', async () => {
  tradesApi.getReview.mockResolvedValue({ data: SAVED });
  tradesApi.getSummary.mockResolvedValue({ data: { summary: null } });
  render(<TradeFeedback trade={TRADE} closed />);
  expect(await screen.findByText('Lesson for next time')).toBeInTheDocument();
});

test('feedback on a changed trade says so', async () => {
  tradesApi.getReview.mockResolvedValue({ data: { ...SAVED, out_of_date: true } });
  render(<TradeFeedback trade={TRADE} />);
  expect(await screen.findByRole('status')).toHaveTextContent('changed since');
});

test('it checks again when the trade\'s notes change', async () => {
  tradesApi.getReview.mockResolvedValue({ data: SAVED });
  const { rerender } = render(<TradeFeedback trade={TRADE} changed={0} />);
  await waitFor(() => expect(tradesApi.getReview).toHaveBeenCalledTimes(1));
  rerender(<TradeFeedback trade={TRADE} changed={1} />);
  await waitFor(() => expect(tradesApi.getReview).toHaveBeenCalledTimes(2));
});

test('a failed run keeps the earlier feedback and shows why', async () => {
  tradesApi.getReview.mockResolvedValue({ data: SAVED });
  tradesApi.runReview.mockRejectedValue({ response: { data: { detail: 'Add ANTHROPIC_API_KEY to backend/.env' } } });
  render(<TradeFeedback trade={TRADE} />);

  fireEvent.click(await screen.findByRole('button', { name: 'Analyse again' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('ANTHROPIC_API_KEY');
  expect(screen.getByText('Followed the plan.')).toBeInTheDocument();
});
