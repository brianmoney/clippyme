// PublishModal — per-clip captions. A batch renders one caption box per clip
// and every publish body carries that clip's own caption (never one shared
// string); the single-clip path seeds from the persisted per-clip state.
import { test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PublishModal } from './publish.jsx';
import { localDatePlus } from '../lib/scheduleDates';
import * as realApi from './realApi';

const zernioShape = ({ tz = 'Europe/Rome' } = {}) => ({
  configured: true,
  default_profile_id: 'p1',
  profiles: [{
    id: 'p1', name: 'Default', api_key_masked: 'sk_ab...cd', is_default: true,
    accounts: { tiktok: 'tt-1', instagram: 'ig-1', youtube: '' },
    timezone: tz,
  }],
  api_key_masked: 'sk_ab...cd',
  accounts: { tiktok: 'tt-1', instagram: 'ig-1', youtube: '' },
  timezone: tz,
});

vi.mock('./realApi', () => ({
  getZernio: vi.fn(async () => zernioShape()),
  publishClip: vi.fn(async () => ({ success: true })),
  clipVideoSrc: (clip) => clip.video_url || '/videos/x.mp4',
  clipPreviewSrc: (clip) => clip.video_url || '/videos/x.mp4',
  fmtDuration: () => '0:10',
}));

const clip = (i, over = {}) => ({
  start: 10,
  end: 20,
  viral_score: 90,
  video_title_for_youtube_short: `Clip ${i + 1}`,
  original_index: i,
  _idx: i,
  video_url: `/videos/${i}.mp4`,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  realApi.getZernio.mockResolvedValue(zernioShape());
});

test('batch publish sends a distinct caption per clip', async () => {
  const clips = [clip(0), clip(1), clip(2)];
  const onCaptionChange = vi.fn();
  render(
    <PublishModal clips={clips} jobId="job-1" clipStates={{}} onClose={vi.fn()} onPublished={vi.fn()} pushToast={vi.fn()} onCaptionChange={onCaptionChange} />,
  );

  const c1 = await screen.findByLabelText('Caption for Clip 1');
  fireEvent.change(c1, { target: { value: 'first caption' } });
  fireEvent.change(screen.getByLabelText('Caption for Clip 2'), { target: { value: 'second caption' } });
  fireEvent.change(screen.getByLabelText('Caption for Clip 3'), { target: { value: 'third caption' } });

  fireEvent.click(screen.getByRole('button', { name: /Publish now/ }));
  await waitFor(() => expect(realApi.publishClip).toHaveBeenCalledTimes(3));

  const bodies = realApi.publishClip.mock.calls.map((c) => c[2]);
  expect(bodies.map((b) => b.caption)).toEqual(['first caption', 'second caption', 'third caption']);
  expect(onCaptionChange).toHaveBeenCalledWith(0, 'first caption');
});

test('batch caption boxes seed from persisted per-clip state', async () => {
  const clips = [clip(0), clip(1)];
  const clipStates = { 0: { caption: 'saved in the grid' }, 1: {} };
  render(
    <PublishModal clips={clips} jobId="job-1" clipStates={clipStates} onClose={vi.fn()} onPublished={vi.fn()} pushToast={vi.fn()} />,
  );
  expect(await screen.findByLabelText('Caption for Clip 1')).toHaveValue('saved in the grid');
  expect(screen.getByLabelText('Caption for Clip 2')).toHaveValue('Clip 2');
});

test('single clip falls back to the clip title when nothing was authored', async () => {
  render(
    <PublishModal clips={[clip(0)]} jobId="job-1" clipStates={{}} onClose={vi.fn()} onPublished={vi.fn()} pushToast={vi.fn()} />,
  );
  expect(await screen.findByLabelText('Caption')).toHaveValue('Clip 1');
});

test('schedule batch spreads perDay clips per day and sends the chosen timezone', async () => {
  realApi.getZernio.mockResolvedValue(zernioShape({ tz: 'America/New_York' }));
  const clips = [clip(0), clip(1), clip(2), clip(3)];
  render(
    <PublishModal clips={clips} jobId="job-1" clipStates={{}} onClose={vi.fn()} onPublished={vi.fn()} pushToast={vi.fn()} />,
  );

  await screen.findByLabelText('Caption for Clip 1');
  // Timezone seeds from the Zernio config.
  await waitFor(() => expect(screen.getByLabelText('Schedule timezone')).toHaveValue('America/New_York'));
  // 1 → 2 posts per day.
  fireEvent.click(screen.getByRole('button', { name: 'Increase Posts per day' }));

  fireEvent.click(screen.getByRole('button', { name: 'Schedule' }));
  await waitFor(() => expect(realApi.publishClip).toHaveBeenCalledTimes(4));

  const bodies = realApi.publishClip.mock.calls.map((c) => c[2]);
  const startDates = bodies.map((b) => b.start_date);
  // 4 clips at 2/day → two clips on day 0, two on day 1.
  expect(startDates[0]).toBe(startDates[1]);
  expect(startDates[1]).not.toBe(startDates[2]);
  expect(startDates[2]).toBe(startDates[3]);
  expect(new Set(startDates).size).toBe(2);
  bodies.forEach((b) => {
    expect(b.schedule_mode).toBe('auto');
    expect(b.timezone).toBe('America/New_York');
  });
});

test('schedule batch offsets the start by days from now', async () => {
  const clips = [clip(0), clip(1)];
  render(
    <PublishModal clips={clips} jobId="job-1" clipStates={{}} onClose={vi.fn()} onPublished={vi.fn()} pushToast={vi.fn()} />,
  );
  await screen.findByLabelText('Caption for Clip 1');
  fireEvent.click(screen.getByRole('button', { name: 'Increase Days from now' })); // 0 → 1
  fireEvent.click(screen.getByRole('button', { name: 'Schedule' }));
  await waitFor(() => expect(realApi.publishClip).toHaveBeenCalledTimes(2));
  const startDates = realApi.publishClip.mock.calls.map((c) => c[2].start_date);
  // perDay=1: clip0 → today+1, clip1 → today+2.
  expect(startDates[0]).toBe(localDatePlus(1));
  expect(startDates[1]).toBe(localDatePlus(2));
});

test('batch schedule knobs are hidden for a single clip publish', async () => {
  render(
    <PublishModal clips={[clip(0)]} jobId="job-1" clipStates={{}} onClose={vi.fn()} onPublished={vi.fn()} pushToast={vi.fn()} />,
  );
  await screen.findByLabelText('Caption');
  // Posts-per-day and days-from-now are batch (Publish all) concepts.
  expect(screen.queryByRole('button', { name: 'Increase Posts per day' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Increase Days from now' })).toBeNull();
  expect(screen.getByLabelText('Schedule timezone')).toBeTruthy();
});

test('publish now sends schedule_mode now and no start_date', async () => {  const clips = [clip(0), clip(1)];
  render(
    <PublishModal clips={clips} jobId="job-1" clipStates={{}} onClose={vi.fn()} onPublished={vi.fn()} pushToast={vi.fn()} />,
  );
  await screen.findByLabelText('Caption for Clip 1');
  fireEvent.click(screen.getByRole('button', { name: 'Publish now' }));
  await waitFor(() => expect(realApi.publishClip).toHaveBeenCalledTimes(2));
  const bodies = realApi.publishClip.mock.calls.map((c) => c[2]);
  bodies.forEach((b) => {
    expect(b.schedule_mode).toBe('now');
    expect(b.start_date).toBeUndefined();
  });
});
