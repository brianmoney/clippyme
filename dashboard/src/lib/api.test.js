import { afterEach, describe, expect, it, vi } from 'vitest';
import { submitBatchJob, submitProcessJob } from './api';

const RECIPE = {
  subtitles: { mode: 'karaoke', preset: 'hormozi_bold', position: 'bottom' },
  hook: { position: 'top', size: 'L', animate: true },
};

function mockFetch() {
  const spy = vi.fn(async () => ({
    ok: true,
    json: async () => ({ job_id: 'job-1', jobs: [{ job_id: 'job-1' }], total: 1 }),
    text: async () => '',
  }));
  globalThis.fetch = spy;
  return spy;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Create recipe transport', () => {
  it('sends the recipe with a JSON URL job', async () => {
    const fetchSpy = mockFetch();
    await submitProcessJob(
      { type: 'url', payload: 'https://example.com/v', preselections: RECIPE },
      'key',
    );
    const body = JSON.parse(fetchSpy.mock.calls[0][1].body);
    expect(body.preselections).toEqual(RECIPE);
  });

  it('sends the recipe with a multipart upload', async () => {
    const fetchSpy = mockFetch();
    const file = new File(['x'], 'v.mp4', { type: 'video/mp4' });
    await submitProcessJob(
      { type: 'file', payload: file, preselections: RECIPE },
      'key',
    );
    const body = fetchSpy.mock.calls[0][1].body;
    expect(JSON.parse(body.get('preselections'))).toEqual(RECIPE);
  });

  it('sends the recipe with a batch job', async () => {
    const fetchSpy = mockFetch();
    await submitBatchJob(
      { urls: ['https://example.com/v'], preselections: RECIPE },
      'key',
    );
    const body = JSON.parse(fetchSpy.mock.calls[0][1].body);
    expect(body.preselections).toEqual(RECIPE);
  });
});
