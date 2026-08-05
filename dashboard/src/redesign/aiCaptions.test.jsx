// AiCaptionPanel — pins: only clips without a hand-written caption are sent,
// responses are mapped back from absolute shorts positions to grid positions,
// and the panel surfaces a disabled state when no endpoint is configured.
import { test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AiCaptionPanel } from './aiCaptions.jsx';
import * as realApi from './realApi';

vi.mock('./realApi', () => ({
  getConfig: vi.fn(async () => ({
    OPENAI_CAPTIONS_API_KEY: 'sk-...abcd',
    OPENAI_CAPTIONS_BASE_URL: 'https://api.openai.com/v1',
    OPENAI_CAPTIONS_MODEL: 'gpt-4o-mini',
  })),
  optimizeCaptions: vi.fn(async () => ({ captions: [], model: 'gpt-4o-mini', base_url: 'x' })),
}));

const clip = (i) => ({
  start: 0,
  end: 5,
  video_title_for_youtube_short: `Clip ${i + 1}`,
  original_index: i,
});

beforeEach(() => {
  vi.clearAllMocks();
  realApi.getConfig.mockResolvedValue({
    OPENAI_CAPTIONS_API_KEY: 'sk-...abcd',
    OPENAI_CAPTIONS_BASE_URL: 'https://api.openai.com/v1',
    OPENAI_CAPTIONS_MODEL: 'gpt-4o-mini',
  });
  realApi.optimizeCaptions.mockResolvedValue({ captions: [], model: 'gpt-4o-mini', base_url: 'x' });
});

function renderPanel(clipStates = {}) {
  const clips = [clip(0), clip(1), clip(2)];
  const onUpdateClipState = vi.fn();
  const pushToast = vi.fn();
  render(
    <AiCaptionPanel jobId="job-1" clips={clips} clipStates={clipStates}
      onUpdateClipState={onUpdateClipState} pushToast={pushToast} />,
  );
  return { onUpdateClipState, pushToast };
}

test('sends only clips without a hand-written caption, mapped by original_index', async () => {
  // Clip 2 (array idx 2, original_index 2) was hand-written → excluded.
  const { onUpdateClipState } = renderPanel({ 2: { caption: 'mine', captionTouched: true } });
  realApi.optimizeCaptions.mockResolvedValue({
    captions: [
      { index: 0, caption: 'caption zero' },
      { index: 1, caption: 'caption one' },
    ],
  });

  await screen.findByText('model: gpt-4o-mini');
  fireEvent.change(screen.getByLabelText('AI caption context'), { target: { value: 'fitness channel' } });
  fireEvent.click(screen.getByRole('button', { name: 'Generate captions' }));

  await waitFor(() => expect(realApi.optimizeCaptions).toHaveBeenCalledTimes(1));
  expect(realApi.optimizeCaptions).toHaveBeenCalledWith('job-1', { context: 'fitness channel', indices: [0, 1] });

  await waitFor(() => expect(onUpdateClipState).toHaveBeenCalledWith(0, { caption: 'caption zero', captionTouched: false }));
  expect(onUpdateClipState).toHaveBeenCalledWith(1, { caption: 'caption one', captionTouched: false });
  expect(onUpdateClipState).not.toHaveBeenCalledWith(2, expect.anything());
});

test('excludes clips removed from the grid', async () => {
  const { onUpdateClipState } = renderPanel({ 0: { deleted: true } });
  realApi.optimizeCaptions.mockResolvedValue({ captions: [{ index: 1, caption: 'c1' }, { index: 2, caption: 'c2' }] });
  await screen.findByText(/2 eligible clips/);
  fireEvent.click(screen.getByRole('button', { name: 'Generate captions' }));
  await waitFor(() => expect(realApi.optimizeCaptions).toHaveBeenCalledWith('job-1', { context: '', indices: [1, 2] }));
  expect(onUpdateClipState).toHaveBeenCalledTimes(2);
});

test('disabled with a hint when no endpoint is configured', async () => {
  realApi.getConfig.mockResolvedValue({ OPENAI_CAPTIONS_API_KEY: '', OPENAI_CAPTIONS_BASE_URL: '' });
  renderPanel();
  expect(await screen.findByText(/configure a model in Settings/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Generate captions' })).toBeDisabled();
});

test('maps response indices back to grid positions when they diverge', async () => {
  // original_index differs from array position (a deleted-after-publish gap).
  const clips = [
    { start: 0, end: 5, original_index: 0 },
    { start: 6, end: 9, original_index: 2 },
  ];
  const onUpdateClipState = vi.fn();
  realApi.optimizeCaptions.mockResolvedValue({
    captions: [{ index: 0, caption: 'zero' }, { index: 2, caption: 'two' }],
  });
  render(
    <AiCaptionPanel jobId="job-1" clips={clips} clipStates={{}}
      onUpdateClipState={onUpdateClipState} pushToast={vi.fn()} />,
  );
  await screen.findByText('model: gpt-4o-mini');
  fireEvent.click(await screen.findByRole('button', { name: 'Generate captions' }));
  await waitFor(() => expect(realApi.optimizeCaptions).toHaveBeenCalledWith('job-1', { context: '', indices: [0, 2] }));
  expect(onUpdateClipState).toHaveBeenCalledWith(0, { caption: 'zero', captionTouched: false });
  expect(onUpdateClipState).toHaveBeenCalledWith(1, { caption: 'two', captionTouched: false });
});
