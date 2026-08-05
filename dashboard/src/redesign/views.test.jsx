// SettingsView key-status badges — pins the bug fix where the "set"/"empty"
// badge must reflect backend-confirmed `present` state, never the raw input
// text, and must refresh after save/clear instead of going stale or being
// wiped by a transient getConfig failure.
import { test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { SettingsView, HistoryView } from './views.jsx';

const getConfig = vi.fn();
const saveConfig = vi.fn();
const getZernio = vi.fn();
const saveZernio = vi.fn();

vi.mock('./realApi', () => ({
  getConfig: (...a) => getConfig(...a),
  saveConfig: (...a) => saveConfig(...a),
  getModels: vi.fn(async () => ({ models: [] })),
  cookiesStatus: vi.fn(async () => ({ configured: false })),
  uploadCookies: vi.fn(),
  deleteCookies: vi.fn(),
  getZernio: (...a) => getZernio(...a),
  saveZernio: (...a) => saveZernio(...a),
  discoverZernioAccounts: vi.fn(),
  listFonts: vi.fn(async () => ({ fonts: [] })),
  uploadFont: vi.fn(),
  deleteFont: vi.fn(),
  logoStatus: vi.fn(async () => ({ configured: false })),
  uploadLogo: vi.fn(),
  deleteLogo: vi.fn(),
}));

const EMPTY_CONFIG = { GEMINI_API_KEY: '', HF_TOKEN: '', DEEPGRAM_API_KEY: '', ELEVENLABS_API_KEY: '' };
const SET_CONFIG = { ...EMPTY_CONFIG, GEMINI_API_KEY: 'AIza...xyz1' };

beforeEach(() => {
  vi.clearAllMocks();
  saveConfig.mockResolvedValue({ success: true });
  getZernio.mockResolvedValue({ configured: false, profiles: [] });
  saveZernio.mockResolvedValue({ configured: true, profiles: [] });
});

function mount(pushToast = vi.fn()) {
  render(<SettingsView pushToast={pushToast} />);
  return pushToast;
}

const geminiRow = () => screen.getByLabelText('Gemini').closest('.keyrow');

test('badge reflects backend state, not input text typed before any save', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  mount();
  await waitFor(() => expect(within(geminiRow()).getByText('empty')).toBeInTheDocument());

  // Typing into the field (no blur/save yet) must not flip the badge.
  fireEvent.change(screen.getByLabelText('Gemini'), { target: { value: 'AIzaSomeKey' } });
  expect(within(geminiRow()).getByText('empty')).toBeInTheDocument();
  expect(within(geminiRow()).queryByText('set')).toBeNull();
});

test('save triggers a refetch and the badge updates from the backend response', async () => {
  getConfig.mockResolvedValueOnce(EMPTY_CONFIG).mockResolvedValueOnce(SET_CONFIG);
  mount();
  await waitFor(() => expect(within(geminiRow()).getByText('empty')).toBeInTheDocument());

  const input = screen.getByLabelText('Gemini');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'AIzaSomeKey' } });
  fireEvent.blur(input);

  expect(saveConfig).toHaveBeenCalledWith({ GEMINI_API_KEY: 'AIzaSomeKey' });
  await waitFor(() => expect(getConfig).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(within(geminiRow()).getByText('set')).toBeInTheDocument());
});

test('a failed post-save refetch does not wipe previously-known present state', async () => {
  getConfig.mockResolvedValueOnce(SET_CONFIG).mockResolvedValueOnce(null);
  const pushToast = mount();
  await waitFor(() => expect(within(geminiRow()).getByText('set')).toBeInTheDocument());

  const input = screen.getByLabelText('Deepgram');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'dg_key' } });
  fireEvent.blur(input);

  await waitFor(() => expect(getConfig).toHaveBeenCalledTimes(2));
  // Gemini's badge (unrelated to the Deepgram save) must still read "set".
  expect(within(geminiRow()).getByText('set')).toBeInTheDocument();
  expect(pushToast).toHaveBeenCalledWith('warn', expect.any(String));
});

test('clearing a present key saves an empty value and the badge flips to empty', async () => {
  getConfig.mockResolvedValueOnce(SET_CONFIG).mockResolvedValueOnce(EMPTY_CONFIG);
  mount();
  await waitFor(() => expect(within(geminiRow()).getByText('set')).toBeInTheDocument());

  fireEvent.click(within(geminiRow()).getByRole('button', { name: 'Clear Gemini key' }));

  expect(saveConfig).toHaveBeenCalledWith({ GEMINI_API_KEY: '' });
  await waitFor(() => expect(within(geminiRow()).getByText('empty')).toBeInTheDocument());
});

test('Twitch client id/secret rows reflect backend present state', async () => {
  getConfig.mockResolvedValue({ ...EMPTY_CONFIG, TWITCH_CLIENT_ID: 'abcd1234', TWITCH_CLIENT_SECRET: 'shhh12345678' });
  mount();
  const idRow = () => screen.getByLabelText('Twitch client ID').closest('.keyrow');
  const secretRow = () => screen.getByLabelText('Twitch client secret').closest('.keyrow');
  await waitFor(() => expect(within(idRow()).getByText('set')).toBeInTheDocument());
  expect(within(secretRow()).getByText('set')).toBeInTheDocument();
});

test('Twitch client id/secret rows show empty when unset', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  mount();
  const idRow = () => screen.getByLabelText('Twitch client ID').closest('.keyrow');
  await waitFor(() => expect(within(idRow()).getByText('empty')).toBeInTheDocument());
});

// --- Zernio profile editor -------------------------------------------------

// Scope queries to the Publishing panel — the AI captions panel also has a
// Save button, so a bare `getByRole('button', { name: 'Save' })` would be ambiguous.
const publishingPanel = () => screen.getByRole('heading', { name: 'Publishing' }).closest('.panel');
const zernioSave = () => within(publishingPanel()).getByRole('button', { name: 'Save' });

test('Zernio profiles load and save posts the whole editable list', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  getZernio.mockResolvedValue({
    configured: true,
    default_profile_id: 'p1',
    profiles: [
      { id: 'p1', name: 'Client A', api_key_masked: 'sk_ab...cd', is_default: true, accounts: { tiktok: 'tt1' }, timezone: 'Europe/Rome' },
      { id: 'p2', name: 'Client B', api_key_masked: 'sk_ef...gh', is_default: false, accounts: {}, timezone: 'Europe/Rome' },
    ],
  });
  saveZernio.mockResolvedValue({ configured: true, profiles: [] });
  mount();
  await waitFor(() => expect(screen.getByDisplayValue('Client A')).toBeInTheDocument());
  expect(screen.getByDisplayValue('Client B')).toBeInTheDocument();

  fireEvent.click(zernioSave());
  await waitFor(() => expect(saveZernio).toHaveBeenCalledTimes(1));
  const payload = saveZernio.mock.calls[0][0].profiles;
  expect(payload).toHaveLength(2);
  expect(payload[0]).toMatchObject({ id: 'p1', name: 'Client A', is_default: true });
  // Keys are blanked in the editor so an unchanged save keeps the stored key.
  expect(payload[0].api_key).toBe('');
  expect(payload[1]).toMatchObject({ id: 'p2', name: 'Client B', is_default: false });
});

test('adding a Zernio profile marks it default when none exist', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  getZernio.mockResolvedValue({ configured: false, profiles: [] });
  saveZernio.mockResolvedValue({ configured: true, profiles: [] });
  mount();
  await waitFor(() => expect(screen.getByText(/Add a named profile/)).toBeInTheDocument());

  fireEvent.click(screen.getByRole('button', { name: 'Add profile' }));
  expect(screen.getByLabelText('Profile 1 name')).toBeInTheDocument();

  fireEvent.click(zernioSave());
  await waitFor(() => expect(saveZernio).toHaveBeenCalledTimes(1));
  const payload = saveZernio.mock.calls[0][0].profiles;
  expect(payload).toHaveLength(1);
  expect(payload[0].is_default).toBe(true);
});

// HistoryView — title + per-job "published" badge (derived from
// history_service.scan_history's additive `title`/`publishedCount` fields).
test('history row shows the video title and a published-count badge when clips were published', () => {
  const history = [
    { jobId: 'job-1', status: 'complete', clipCount: 2, source: 'my video', title: 'my video', publishedCount: 1, timestamp: Date.now() },
    { jobId: 'job-2', status: 'complete', clipCount: 3, source: 'other video', title: 'other video', publishedCount: 0, timestamp: Date.now() },
  ];
  render(<HistoryView history={history} availableIds={null} onOpen={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} />);

  expect(screen.getByText('my video')).toBeInTheDocument();
  expect(screen.getByText('1 published')).toBeInTheDocument();
  expect(screen.getByText('other video')).toBeInTheDocument();
  expect(screen.queryByText('0 published')).toBeNull();
});
