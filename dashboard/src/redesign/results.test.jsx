import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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

test('transcript copy writes the joined segment text to the clipboard', async () => {
  vi.spyOn(realApi, 'getClipTranscript').mockResolvedValue({
    segments: [
      { index: 0, text: 'Hello world', start: 0.0, end: 2.5 },
      { index: 1, text: 'This is the second line', start: 2.5, end: 4.0 },
    ],
    duration: 10,
    language: 'en',
  });
  const writeText = vi.fn().mockResolvedValue(undefined);
  const clipboardOrig = navigator.clipboard;
  const secureOrig = window.isSecureContext;
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });

  renderView();
  fireEvent.click(await screen.findByRole('button', { name: 'Toggle transcript' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Copy transcript' }));

  await waitFor(() => expect(writeText).toHaveBeenCalledWith('Hello world This is the second line'));
  expect(await screen.findByText('Copied')).toBeTruthy();

  Object.defineProperty(navigator, 'clipboard', { value: clipboardOrig, configurable: true });
  Object.defineProperty(window, 'isSecureContext', { value: secureOrig, configurable: true });
});

test('caption editor saves per-clip caption to state', async () => {
  const onUpdateClipState = vi.fn();
  render(
    <ResultsView
      clips={[{ ...clip }]}
      jobId="job-1"
      preselections={{}}
      clipStates={{}}
      onUpdateClipState={onUpdateClipState}
      onBack={vi.fn()}
      onPublish={vi.fn()}
      onPublishAll={vi.fn()}
      onEdit={vi.fn()}
      onApplyToAll={vi.fn()}
      onEditSelected={vi.fn()}
    />,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Toggle caption' }));
  fireEvent.change(screen.getByLabelText('Caption for Test Clip'), { target: { value: 'my viral caption' } });
  expect(onUpdateClipState).toHaveBeenCalledWith(0, { caption: 'my viral caption', captionTouched: true });
});

test('caption editor seeds from persisted per-clip state', async () => {
  render(
    <ResultsView
      clips={[{ ...clip }]}
      jobId="job-1"
      preselections={{}}
      clipStates={{ 0: { caption: 'pre-saved' } }}
      onUpdateClipState={vi.fn()}
      onBack={vi.fn()}
      onPublish={vi.fn()}
      onPublishAll={vi.fn()}
      onEdit={vi.fn()}
      onApplyToAll={vi.fn()}
      onEditSelected={vi.fn()}
    />,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Toggle caption' }));
  expect(screen.getByLabelText('Caption for Test Clip')).toHaveValue('pre-saved');
});

test('AI captions button expands the context panel above the grid', async () => {
  vi.spyOn(realApi, 'getConfig').mockResolvedValue({
    OPENAI_CAPTIONS_API_KEY: '',
    OPENAI_CAPTIONS_BASE_URL: '',
    OPENAI_CAPTIONS_MODEL: '',
  });
  renderView();
  fireEvent.click(await screen.findByRole('button', { name: 'AI captions' }));
  expect(await screen.findByLabelText('AI caption context')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'AI captions' }));
  expect(screen.queryByLabelText('AI caption context')).toBeNull();
});
