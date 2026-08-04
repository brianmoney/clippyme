import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { ResultsView } from './results';
import * as realApi from './realApi';

const clip = {
  start: 10,
  end: 20,
  viral_score: 92,
  video_title_for_youtube_short: 'Test Clip',
  original_index: 0,
};

function renderView() {
  return render(
    <ResultsView
      clips={[clip]}
      jobId="job-1"
      preselections={{}}
      clipStates={{}}
      onUpdateClipState={vi.fn()}
      onBack={vi.fn()}
      onPublish={vi.fn()}
      onPublishAll={vi.fn()}
      onEdit={vi.fn()}
      onApplyToAll={vi.fn()}
      onEditSelected={vi.fn()}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test('transcript toggle fetches and shows the clip-relative segments', async () => {
  const get = vi.spyOn(realApi, 'getClipTranscript').mockResolvedValue({
    segments: [
      { index: 0, text: 'Hello world', start: 0.0, end: 2.5 },
      { index: 1, text: 'This is the second line', start: 2.5, end: 4.0 },
    ],
    duration: 10,
    language: 'en',
  });
  renderView();
  fireEvent.click(await screen.findByRole('button', { name: 'Toggle transcript' }));

  expect(await screen.findByText('Hello world')).toBeTruthy();
  expect(screen.getByText('This is the second line')).toBeTruthy();
  expect(screen.getByText('en')).toBeTruthy();
  expect(get).toHaveBeenCalledWith('job-1', 0);
});

test('transcript failure surfaces an unavailable hint without crashing', async () => {
  vi.spyOn(realApi, 'getClipTranscript').mockRejectedValue(new Error('boom'));
  renderView();
  fireEvent.click(await screen.findByRole('button', { name: 'Toggle transcript' }));

  expect(await screen.findByText('Transcript unavailable for this clip.')).toBeTruthy();
});
