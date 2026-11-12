import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { ResultsView } from './results';
import * as realApi from './realApi';

const clip = {
  start: 10,
  end: 20,
  viral_score: 92,
  viral_hook_text: 'She got voted out mid-sentence',
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


test('auto-composes once with the Create recipe so previews reflect subtitles/hook', async () => {
  const compose = vi.spyOn(realApi, 'composeClip').mockResolvedValue({
    composed_url: '/videos/job-1/composed_clip.mp4',
  });
  const update = vi.fn();
  render(
    <ResultsView
      clips={[clip]}
      jobId="job-1"
      preselections={{
        subtitles: {
          mode: 'karaoke', preset: 'hormozi_bold', align: 'center', offset_y: 12,
          font_color: '#FDE700', outline_color: '#111111',
        },
        hook: { position: 'top', size: 'M', animate: true, text_color: '#FF0000' },
        smartcut: false,
      }}
      clipStates={{}}
      onUpdateClipState={update}
      onBack={vi.fn()}
      onPublish={vi.fn()}
      onPublishAll={vi.fn()}
      onEdit={vi.fn()}
      onApplyToAll={vi.fn()}
      onEditSelected={vi.fn()}
    />,
  );
  await waitFor(() => expect(compose).toHaveBeenCalled());
  const [jobId, index, params] = compose.mock.calls[0];
  expect(jobId).toBe('job-1');
  expect(index).toBe(0);
  expect(params.toggles.subtitles).toBe(true);
  expect(params.toggles.hook).toBe(true);
  expect(params.toggles.smartcut).toBe(false);
  // The whole recipe must reach compose — not just the toggle. These were the
  // options that used to be silently dropped on every generated clip.
  expect(params.subtitle_params).toMatchObject({
    mode: 'karaoke', preset: 'hormozi_bold', align: 'center', offset_y: 12,
    font_color: '#FDE700', outline_color: '#111111',
  });
  expect(params.hook_params).toMatchObject({
    position: 'top', size: 'M', animate: true, text_color: '#FF0000',
    text: 'She got voted out mid-sentence',
  });
  await waitFor(() => expect(update).toHaveBeenCalledWith(
    0,
    expect.objectContaining({ previewUrl: '/videos/job-1/composed_clip.mp4', processing: false }),
  ));
  const finalState = update.mock.calls.at(-1)[1];
  expect(finalState.hookParams).toBeTruthy();
  expect(finalState.subtitleParams).toBeTruthy();
  expect(finalState.hook_params).toBeUndefined();
});

test('regenerates a preview whose composed file is gone', async () => {
  const compose = vi.spyOn(realApi, 'composeClip').mockResolvedValue({
    composed_url: '/videos/job-1/Test Clip_1.mp4',
  });
  render(
    <ResultsView
      clips={[{ ...clip, video_url: '/videos/job-1/Test Clip_clip_1.mp4' }]} // no composed_url: file is gone
      jobId="job-1"
      preselections={{ subtitles: { mode: 'karaoke', preset: 'hormozi_bold' } }}
      clipStates={{ 0: { previewUrl: '/videos/job-1/Test Clip_1.mp4', toggles: { subtitles: true } } }}
      onUpdateClipState={vi.fn()}
      onBack={vi.fn()}
      onPublish={vi.fn()}
      onPublishAll={vi.fn()}
      onEdit={vi.fn()}
      onApplyToAll={vi.fn()}
      onEditSelected={vi.fn()}
    />,
  );
  await waitFor(() => expect(compose).toHaveBeenCalled());
});

test('keeps a per-clip edit when regenerating a missing preview', async () => {
  const compose = vi.spyOn(realApi, 'composeClip').mockResolvedValue({
    composed_url: '/videos/job-1/Test Clip_1.mp4',
  });
  render(
    <ResultsView
      clips={[{ ...clip, video_url: '/videos/job-1/Test Clip_clip_1.mp4' }]}
      jobId="job-1"
      preselections={{ subtitles: { mode: 'karaoke', preset: 'hormozi_bold' } }}
      clipStates={{
        0: {
          previewUrl: '/videos/job-1/Test Clip_1.mp4',
          toggles: { subtitles: true },
          subtitleParams: { mode: 'classic', preset: 'classic_white', font: 'Anton-Regular' },
        },
      }}
      onUpdateClipState={vi.fn()}
      onBack={vi.fn()}
      onPublish={vi.fn()}
      onPublishAll={vi.fn()}
      onEdit={vi.fn()}
      onApplyToAll={vi.fn()}
      onEditSelected={vi.fn()}
    />,
  );
  await waitFor(() => expect(compose).toHaveBeenCalled());
  const [, , params] = compose.mock.calls[0];
  expect(params.subtitle_params).toMatchObject({ mode: 'classic', preset: 'classic_white', font: 'Anton-Regular' });
});

test('does not recompose when the composed file is present', async () => {
  const compose = vi.spyOn(realApi, 'composeClip').mockResolvedValue({ composed_url: '/x.mp4' });
  render(
    <ResultsView
      clips={[{ ...clip, composed_url: '/videos/job-1/Test Clip_1.mp4' }]}
      jobId="job-1"
      preselections={{ subtitles: { mode: 'karaoke', preset: 'hormozi_bold' } }}
      clipStates={{ 0: { previewUrl: '/videos/job-1/Test Clip_1.mp4', toggles: { subtitles: true } } }}
      onUpdateClipState={vi.fn()}
      onBack={vi.fn()}
      onPublish={vi.fn()}
      onPublishAll={vi.fn()}
      onEdit={vi.fn()}
      onApplyToAll={vi.fn()}
      onEditSelected={vi.fn()}
    />,
  );
  await new Promise((resolve) => setTimeout(resolve, 30));
  expect(compose).not.toHaveBeenCalled();
});

test('a fresh browser uses the server composed file without re-composing', async () => {
  const compose = vi.spyOn(realApi, 'composeClip').mockResolvedValue({ composed_url: '/x.mp4' });
  render(
    <ResultsView
      clips={[{ ...clip, composed_url: '/videos/job-1/Test Clip_1.mp4' }]}
      jobId="job-1"
      preselections={{ subtitles: { mode: 'karaoke', preset: 'hormozi_bold' } }}
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
  await new Promise((resolve) => setTimeout(resolve, 30));
  expect(compose).not.toHaveBeenCalled();
});

test('a failed recipe compose stays retryable (does not mark the recipe applied)', async () => {
  // Persisting toggles on failure made every later mount skip the recipe, so
  // one transient compose error left all clips raw forever. Keep the state
  // recipe-free and surface the failure instead.
  vi.spyOn(realApi, 'composeClip').mockRejectedValue(new Error('ffmpeg busy'));
  const update = vi.fn();
  const pushToast = vi.fn();
  render(
    <ResultsView
      clips={[clip]}
      jobId="job-1"
      preselections={{ subtitles: { mode: 'karaoke', preset: 'hormozi_bold' } }}
      clipStates={{}}
      onUpdateClipState={update}
      onBack={vi.fn()}
      onPublish={vi.fn()}
      onPublishAll={vi.fn()}
      onEdit={vi.fn()}
      onApplyToAll={vi.fn()}
      onEditSelected={vi.fn()}
      pushToast={pushToast}
    />,
  );
  await waitFor(() => expect(pushToast).toHaveBeenCalledWith('warn', expect.stringContaining('Clip 1')));
  const failed = update.mock.calls.at(-1)[1];
  expect(failed).toMatchObject({ processing: false, composeError: true });
  expect(failed.toggles).toBeUndefined();
  expect(failed.subtitleParams).toBeUndefined();
});

test('real Create opts → preselections → clip compose keeps every subtitle/hook option', async () => {
  // End-to-end seam: the home-page drawer state is mapped by
  // optsToPreselections, then seeded per clip by seedClipParams. Pin the
  // fields the user expects to see on the generated clips so a new control
  // cannot be added to Create and silently dropped on the way to compose.
  const compose = vi.spyOn(realApi, 'composeClip').mockResolvedValue({
    composed_url: '/videos/job-1/composed.mp4',
  });
  const preselections = realApi.optsToPreselections({
    reframeMode: 'auto', subtitles: true, subMode: 'classic', subPreset: 'classic_white',
    subFont: 'Anton-Regular', subColor: '#FDE700', subOutlineW: 4, subBg: true,
    subPosition: 'top', subAlign: 'center', subOffsetY: 8,
    hooks: true, hookPos: 'bottom', hookSize: 'L',
    hookStyle: { animate: true, text_color: '#00FF66', outline_width: 8 },
    smartcut: true,
  });
  render(
    <ResultsView
      clips={[clip]}
      jobId="job-1"
      preselections={preselections}
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
  await waitFor(() => expect(compose).toHaveBeenCalled());
  const [, , params] = compose.mock.calls[0];
  expect(params.toggles).toMatchObject({ subtitles: true, hook: true, smartcut: true });
  expect(params.subtitle_params).toMatchObject({
    mode: 'classic', preset: 'classic_white', font: 'Anton-Regular',
    font_color: '#FDE700', border_width: 4, bg_opacity: 0.6,
    position: 'top', align: 'center', offset_y: 8,
  });
  expect(params.hook_params).toMatchObject({
    position: 'bottom', size: 'L', animate: true,
    text_color: '#00FF66', outline_width: 8,
    text: 'She got voted out mid-sentence',
  });
});
