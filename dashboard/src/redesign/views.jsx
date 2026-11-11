// ClippyMe redesign — HistoryView + SettingsView + ApiKeyModal, wired to the
// real backend (history list/restore/delete; config keys, cookies, Zernio).
import { useState, useEffect, useRef } from 'react';
import { useModalA11y } from './useModalA11y';
import { Icon, Btn, Badge, Switch, Segmented, Panel } from './primitives';
import { Hero } from './chrome';
import {
  getConfig, saveConfig, getModels, getViralModels, cookiesStatus, uploadCookies, deleteCookies,
  getZernio, saveZernio, discoverZernioAccounts,
  listFonts, uploadFont, deleteFont, logoStatus, uploadLogo, deleteLogo,
} from './realApi';
import { SUB_FONTS } from './data';
import { getApiToken, setApiToken } from '../lib/apiToken';
import { relTime } from '../lib/relTime';

// Curated fallback when live discovery is unavailable (no key yet / offline).
// Mirrors the allow-list prefixes (gemini-2.5- / gemini-3) the backend accepts.
const FALLBACK_MODELS = [
  { name: 'gemini-3.5-flash', display_name: 'Gemini 3.5 Flash — recommended' },
  { name: 'gemini-2.5-flash', display_name: 'Gemini 2.5 Flash — budget' },
  { name: 'gemini-3.1-pro-preview', display_name: 'Gemini 3.1 Pro — max quality' },
  { name: 'gemini-2.5-pro', display_name: 'Gemini 2.5 Pro — max quality' },
];

const DEFAULT_VIRAL_BASE_URL = 'https://api.commandcode.ai/provider/v1';

// Curated fallback when the provider catalog can't be fetched yet.
const FALLBACK_VIRAL_MODELS = [
  { name: 'claude-sonnet-5', display_name: 'Claude Sonnet 5 — recommended' },
  { name: 'gpt-6-sol', display_name: 'GPT-6 Sol' },
  { name: 'deepseek/deepseek-v4-flash', display_name: 'DeepSeek V4 Flash' },
];

// YouTube video category ids (what Zernio/YouTube expect). Mirrors the backend
// allow-list in storage.config_store.YOUTUBE_CATEGORY_IDS.
const YT_CATEGORIES = {
  '22': 'People & Blogs',
  '1': 'Film & Animation',
  '2': 'Autos & Vehicles',
  '10': 'Music',
  '15': 'Pets & Animals',
  '17': 'Sports',
  '20': 'Gaming',
  '23': 'Comedy',
  '24': 'Entertainment',
  '25': 'News & Politics',
  '26': 'Howto & Style',
  '27': 'Education',
  '28': 'Science & Technology',
  '29': 'Nonprofits & Activism',
};

export function HistoryView({ history, availableIds, onOpen, onDelete, onClear }) {
  if (!history.length) {
    return (
      <div className="container narrow fade-in">
        <Hero eyebrow="History" line1="Nothing here yet." sub="Every job you run lands here, ready to reopen, re-export, or publish again." />
        <div className="empty">
          <div className="ei"><Icon n="clock" /></div>
          <h3>No jobs yet</h3>
          <p>Head to Create, paste a link, and your finished clips will land here.</p>
        </div>
      </div>
    );
  }
  return (
    <div className="container narrow fade-in">
      <div className="results-head" style={{ marginBottom: 18 }}>
        <h2>History</h2>
        <Badge tone="out">{history.length} jobs</Badge>
        <div className="rh-right">
          <Btn variant="ghost" size="sm" icon="trash-2" onClick={onClear}>Clear all</Btn>
        </div>
      </div>
      <Panel pad={false} className="hlist">
        {history.map((h) => {
          // `availableIds` is the set of jobs whose files still exist on disk
          // (null = backend not reached yet → assume available, don't disable).
          // An entry whose files were wiped by a rebuild is shown muted + flagged
          // "files removed" instead of looking clickable and dead-ending.
          const onDisk = !availableIds || availableIds.has(h.jobId);
          const ok = h.status === 'complete' && onDisk;
          const removed = !!availableIds && !availableIds.has(h.jobId);
          return (
            <div className="hrow" key={h.jobId}
              role={ok ? 'button' : undefined} tabIndex={ok ? 0 : undefined}
              aria-label={ok ? `Open job ${h.title || h.source || h.jobId}` : undefined}
              onClick={() => ok && onOpen(h)}
              onKeyDown={(e) => { if (ok && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onOpen(h); } }}
              style={{ cursor: ok ? 'pointer' : 'default', opacity: removed ? 0.55 : 1 }}>
              <div className="hthumb" style={{ background: removed ? 'var(--bg-4)' : 'var(--grad-viral)' }}>{h.clipCount ?? 0}</div>
              <div style={{ minWidth: 0 }}>
                <div className="ht" title={h.title || h.source || h.jobId}
                  style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.title || h.source || h.jobId}</div>
                <div className="hm">
                  <Icon n={h.sourceType === 'url' ? 'globe' : 'file-video'} style={{ width: 11, height: 11, verticalAlign: '-1px', marginRight: 5 }} />
                  {removed ? 'Files removed (rebuild/cleanup) · delete to dismiss'
                    : `${h.clipCount || 0} clips${h.cost != null ? ` · $${Number(h.cost).toFixed(2)}` : ''} · ${relTime(h.timestamp)}`}
                </div>
              </div>
              <div className="hr">
                {!removed && h.publishedCount > 0 && (
                  <Badge tone="teal" icon="send">{h.publishedCount} published</Badge>
                )}
                {removed ? <Badge tone="out" icon="triangle-alert">unavailable</Badge>
                  : h.status === 'complete' ? <Badge tone="teal" icon="check">complete</Badge>
                    : h.status === 'error' ? <Badge tone="danger" icon="triangle-alert">error</Badge>
                      : <Badge tone="amber" icon="clock">{h.status || 'pending'}</Badge>}
                <button type="button" className="mini" title="Delete" aria-label="Delete job" onClick={(e) => { e.stopPropagation(); onDelete(h.jobId); }}><Icon n="trash-2" /></button>
                {ok && <Icon n="chevron-right" style={{ width: 18, height: 18, color: 'var(--fg-4)' }} />}
              </div>
            </div>
          );
        })}
      </Panel>
    </div>
  );
}

function KeyRow({ icon, name, desc, value, onChange, onSave, onClear, placeholder, present }) {
  const [reveal, setReveal] = useState(false);
  // Only persist (and toast) when the field actually changed during this focus
  // session — tabbing past an already-set key shouldn't spam saves/toasts.
  const focusVal = useRef(value);
  return (
    <div className="keyrow">
      <div className="ki"><Icon n={icon} /></div>
      <div style={{ minWidth: 0 }}>
        <div className="kt">{name}</div>
        <div className="kd">{desc}</div>
      </div>
      <div className="kr">
        <input className="key-input" type={reveal ? 'text' : 'password'} value={value}
          aria-label={name}
          placeholder={placeholder} onChange={(e) => onChange(e.target.value)}
          onFocus={() => { focusVal.current = value; }}
          onBlur={() => { if (value !== focusVal.current) onSave(); }} />
        <button type="button" className="mini" title={reveal ? 'Hide' : 'Show'} aria-label={reveal ? 'Hide key' : 'Show key'} onClick={() => setReveal(!reveal)}><Icon n={reveal ? 'eye-off' : 'eye'} /></button>
        {/* Backend-confirmed state only — raw input text (typed but not yet
            saved) must never flip this badge. */}
        {present ? <Badge tone="teal" icon="check">set</Badge> : <Badge tone="out">empty</Badge>}
        {present && onClear && (
          <button type="button" className="mini" title="Clear saved key" aria-label={`Clear ${name} key`} onClick={onClear}><Icon n="x" /></button>
        )}
      </div>
    </div>
  );
}

export function SettingsView({ apiKey, onApiKey, cookiesConfigured, onCookiesChange, pushToast }) {
  const [gemini, setGemini] = useState(apiKey || '');
  const [deepgram, setDeepgram] = useState('');
  const [elevenlabs, setElevenlabs] = useState('');
  const [hf, setHf] = useState('');
  const [twitchId, setTwitchId] = useState('');
  const [twitchSecret, setTwitchSecret] = useState('');
  const [openaiKey, setOpenaiKey] = useState('');
  const [openaiUrl, setOpenaiUrl] = useState('');
  const [openaiModel, setOpenaiModel] = useState('');
  const [apiToken, setApiTokenState] = useState(() => getApiToken());
  const [present, setPresent] = useState({});
  const [zernio, setZernioState] = useState(null);
  // Editable Zernio profiles: {id, name, api_key (raw, blank = keep), is_default, accounts, timezone}
  const [profiles, setProfiles] = useState([]);
  const [discovering, setDiscovering] = useState(null);
  const [cookies, setCookies] = useState(!!cookiesConfigured);
  const [logoOn, setLogoOn] = useState(false);
  const [fonts, setFonts] = useState([]);
  const [provider, setProvider] = useState('deepgram');
  const [model, setModel] = useState('');
  const [models, setModels] = useState(FALLBACK_MODELS);
  const [loadingModels, setLoadingModels] = useState(false);
  const [viralProvider, setViralProvider] = useState('gemini');
  const [viralKey, setViralKey] = useState('');
  const [viralUrl, setViralUrl] = useState(DEFAULT_VIRAL_BASE_URL);
  const [viralModel, setViralModel] = useState('');
  const [viralModels, setViralModels] = useState(FALLBACK_VIRAL_MODELS);
  const [loadingViralModels, setLoadingViralModels] = useState(false);
  const [viralModelsError, setViralModelsError] = useState('');
  const [viralMode, setViralMode] = useState('auto');
  const [discoveryModel, setDiscoveryModel] = useState('z-ai/glm-5.3-flash');
  const [reviewModel, setReviewModel] = useState('claude-opus-5-5');
  const [copyModel, setCopyModel] = useState('');
  const [maxCandidates, setMaxCandidates] = useState('32');
  const [maxSelected, setMaxSelected] = useState('8');
  const [minQuality, setMinQuality] = useState('6');
  const [stageFallback, setStageFallback] = useState('single');

  // Pull the live model list from the backend (uses the saved key if the
  // header is empty). Merges discovery with the curated fallback + the
  // currently-selected model so the dropdown is never empty and never drops
  // the active choice.
  const loadModels = async (key) => {
    setLoadingModels(true);
    try {
      const { models: live } = await getModels(key || gemini || apiKey || '');
      const seen = new Set();
      const merged = [];
      [...(live || []), ...FALLBACK_MODELS].forEach((m) => {
        if (m?.name && !seen.has(m.name)) { seen.add(m.name); merged.push(m); }
      });
      if (merged.length) setModels(merged);
    } catch { /* keep fallback */ }
    finally { setLoadingModels(false); }
  };

  // Live model catalog for the OpenAI-compatible (Command Code) provider.
  const loadViralModels = async (prov, key, url) => {
    const active = prov || viralProvider;
    if (active !== 'openai' || typeof getViralModels !== 'function') return;
    setLoadingViralModels(true);
    try {
      const result = await getViralModels({
        provider: active,
        apiKey: key ?? viralKey,
        baseUrl: url ?? viralUrl,
      });
      setViralModelsError(result?.error ? String(result.error).slice(0, 160) : '');
      if (Array.isArray(result?.models) && result.models.length) setViralModels(result.models);
    } catch (err) {
      setViralModelsError(String(err?.message || err).slice(0, 160));
    }
    finally { setLoadingViralModels(false); }
  };

  // Source of truth for "is this key set" is always the backend's response,
  // never the (optimistic) input text — refetched after every save/clear so
  // the badge can't drift from what's actually persisted.
  const refreshConfig = async () => {
    const c = await getConfig();
    if (!c) { pushToast?.('warn', 'Could not refresh key status'); return null; }
    setPresent({
      gemini: !!c.GEMINI_API_KEY, hf: !!c.HF_TOKEN, deepgram: !!c.DEEPGRAM_API_KEY, elevenlabs: !!c.ELEVENLABS_API_KEY,
      twitchId: !!c.TWITCH_CLIENT_ID, twitchSecret: !!c.TWITCH_CLIENT_SECRET,
      openai: !!(c.OPENAI_CAPTIONS_API_KEY && c.OPENAI_CAPTIONS_BASE_URL),
      viral: !!c.VIRAL_API_KEY,
    });
    if (c.TRANSCRIPTION_PROVIDER) setProvider(c.TRANSCRIPTION_PROVIDER);
    if (c.GEMINI_MODEL) setModel(c.GEMINI_MODEL);
    if (c.OPENAI_CAPTIONS_BASE_URL) setOpenaiUrl(c.OPENAI_CAPTIONS_BASE_URL);
    if (c.OPENAI_CAPTIONS_MODEL) setOpenaiModel(c.OPENAI_CAPTIONS_MODEL);
    if (c.VIRAL_PROVIDER) setViralProvider(c.VIRAL_PROVIDER);
    if (c.VIRAL_BASE_URL) setViralUrl(c.VIRAL_BASE_URL);
    if (c.VIRAL_MODEL) setViralModel(c.VIRAL_MODEL);
    if (c.VIRAL_MODE) setViralMode(c.VIRAL_MODE);
    if (c.VIRAL_DISCOVERY_MODEL) setDiscoveryModel(c.VIRAL_DISCOVERY_MODEL);
    if (c.VIRAL_REVIEW_MODEL) setReviewModel(c.VIRAL_REVIEW_MODEL);
    if (c.VIRAL_COPY_MODEL) setCopyModel(c.VIRAL_COPY_MODEL);
    if (c.VIRAL_MAX_CANDIDATES) setMaxCandidates(String(c.VIRAL_MAX_CANDIDATES));
    if (c.VIRAL_MAX_SELECTED_CLIPS) setMaxSelected(String(c.VIRAL_MAX_SELECTED_CLIPS));
    if (c.VIRAL_MIN_QUALITY) setMinQuality(String(c.VIRAL_MIN_QUALITY));
    if (c.VIRAL_TWO_STAGE_FALLBACK) setStageFallback(c.VIRAL_TWO_STAGE_FALLBACK);
    return c;
  };

  useEffect(() => {
    refreshConfig().then((c) => {
      loadModels();
      if ((c?.VIRAL_PROVIDER || 'gemini') === 'openai') loadViralModels('openai', '', '');
    });
    getZernio().then((z) => {
      setZernioState(z);
      // Loaded profiles carry api_key_masked, never the raw key — reset the
      // editor's raw key field so a blank save keeps the stored key server-side.
      setProfiles(((z.profiles && z.profiles.length) ? z.profiles : [])
        .map((p) => ({ ...p, api_key: '' })));
    }).catch(() => {});
    cookiesStatus().then((s) => setCookies(!!s.configured)).catch(() => {});
    logoStatus().then((s) => setLogoOn(!!s.configured)).catch(() => {});
    listFonts().then(({ fonts: f }) => setFonts(Array.isArray(f) ? f : [])).catch(() => {});
    // Mount-once bootstrap; loadModels reads the latest key via closure on call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const saveKeys = async (patch) => {
    try { await saveConfig(patch); pushToast?.('success', 'Saved'); await refreshConfig(); }
    catch { pushToast?.('error', 'Save failed'); }
  };

  const saveZernioCfg = async () => {
    try {
      const z = await saveZernio({ profiles });
      setZernioState(z);
      setProfiles(((z.profiles || []).map((p) => ({ ...p, api_key: '' }))));
      pushToast?.('success', 'Zernio saved');
    } catch { pushToast?.('error', 'Zernio save failed'); }
  };

  const patchProfile = (i, patch) => setProfiles((ps) => ps.map((p, idx) => (idx === i ? { ...p, ...patch } : p)));

  const patchProfileYoutube = (i, key, value) => setProfiles((ps) => ps.map((p, idx) => (
    idx === i ? { ...p, youtube: { ...(p.youtube || {}), [key]: value } } : p
  )));

  const patchAccount = (i, key, value) => setProfiles((ps) => ps.map((p, idx) => (
    idx === i ? { ...p, accounts: { ...(p.accounts || {}), [key]: value } } : p
  )));

  const setDefault = (i) => setProfiles((ps) => ps.map((p, idx) => ({ ...p, is_default: idx === i })));

  const addProfile = () => {
    setProfiles((ps) => [...ps, {
      id: '', name: '', api_key: '', is_default: ps.length === 0,
      accounts: { tiktok: '', instagram: '', youtube: '' },
      timezone: zernio?.timezone || 'America/New_York',
      youtube: { category_id: '22', made_for_kids: false, contains_synthetic_media: false, visibility: 'public' },
    }]);
  };

  const removeProfile = (i) => {
    setProfiles((ps) => {
      const removedDefault = ps[i]?.is_default;
      const next = ps.filter((_, idx) => idx !== i);
      if (removedDefault && next.length && !next.some((p) => p.is_default)) {
        next[0] = { ...next[0], is_default: true };
      }
      return next;
    });
  };

  const discover = async (profile) => {
    if (!(profile.api_key || profile.api_key_masked)) {
      pushToast?.('warn', 'Add an API key to this profile first');
      return;
    }
    const key = profile.id || profile.name;
    setDiscovering(key);
    try {
      // Discovery runs against the *saved* key, so persist first — otherwise
      // the backend 400s with "API key not configured". Persisting also gives
      // brand-new profiles a stable id to query against.
      const z = await saveZernio({ profiles });
      const saved = (z.profiles || []).find((p) => p.name === profile.name);
      const { accounts } = await discoverZernioAccounts(saved?.id || profile.id);
      const next = { tiktok: '', instagram: '', youtube: '' };
      (accounts || []).forEach((a) => {
        const p = (a.platform || '').toLowerCase();
        const id = a._id || a.id;
        if (p.includes('tiktok')) next.tiktok = id;
        else if (p.includes('insta')) next.instagram = id;
        else if (p.includes('you')) next.youtube = id;
      });
      setZernioState(z);
      setProfiles((z.profiles || []).map((p) => ({
        ...p, api_key: '',
        accounts: p.name === profile.name ? { ...(p.accounts || {}), ...next } : p.accounts,
      })));
      pushToast?.('success', `Discovered ${(accounts || []).length} accounts`);
    } catch { pushToast?.('error', 'Discover failed. Check the API key.'); }
    finally { setDiscovering(null); }
  };

  const onCookieFile = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    try { await uploadCookies(f); setCookies(true); onCookiesChange?.(true); pushToast?.('success', 'Cookies uploaded'); }
    catch { pushToast?.('error', 'Cookie upload failed'); }
  };
  const removeCookies = async () => {
    try { await deleteCookies(); setCookies(false); onCookiesChange?.(false); pushToast?.('info', 'Cookies removed'); }
    catch { pushToast?.('error', 'Remove failed'); }
  };

  const onLogoFile = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    try { await uploadLogo(f); setLogoOn(true); pushToast?.('success', 'Logo uploaded'); }
    catch (err) { pushToast?.('error', String(err.message || 'Logo upload failed').slice(0, 80)); }
  };
  const removeLogo = async () => {
    try { await deleteLogo(); setLogoOn(false); pushToast?.('info', 'Logo removed'); }
    catch { pushToast?.('error', 'Remove failed'); }
  };

  // Only user-uploaded faces are deletable; bundled ones are part of the app.
  const bundled = new Set(SUB_FONTS.map(([v]) => v));
  const onFontFile = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    try { const { fonts: nf } = await uploadFont(f); setFonts(nf || fonts); pushToast?.('success', 'Font added'); }
    catch (err) { pushToast?.('error', String(err.message || 'Font upload failed').slice(0, 80)); }
  };
  const removeFont = async (name) => {
    try { const { fonts: nf } = await deleteFont(name); setFonts(nf || fonts.filter((n) => n !== name)); pushToast?.('info', 'Font removed'); }
    catch { pushToast?.('error', 'Remove failed'); }
  };

  return (
    <div className="container narrow fade-in">
      <Hero eyebrow="Settings" line1="Keys & connections." sub="Everything is stored locally. Your keys never leave your machine." />

      <Panel title="API keys" sub="Required for transcription & moment detection" icon="key-round" style={{ marginBottom: 18 }}>
        <div className="opt">
          <div className="oico"><Icon n="sparkles" /></div>
          <div className="otxt"><div className="ot">Viral moment provider</div><div className="od">Who finds and scores the clips · applied to new jobs</div></div>
          <div className="r"><Segmented value={viralProvider}
            onChange={(id) => { setViralProvider(id); saveKeys({ VIRAL_PROVIDER: id }); if (id === 'openai') loadViralModels('openai'); }}
            options={[{ id: 'gemini', label: 'Gemini' }, { id: 'openai', label: 'Command Code' }]} /></div>
        </div>
        {viralProvider === 'gemini' ? (
          <KeyRow icon="sparkles" name="Gemini" desc="Viral-moment detection" value={gemini} present={present.gemini}
            onChange={(v) => { setGemini(v); onApiKey?.(v); }} onSave={() => saveKeys({ GEMINI_API_KEY: gemini })}
            onClear={() => { setGemini(''); onApiKey?.(''); saveKeys({ GEMINI_API_KEY: '' }); }} placeholder="AIza…" />
        ) : (
          <>
            <div className="opt">
              <div className="oico"><Icon n="link" /></div>
              <div className="otxt"><div className="ot">Command Code endpoint</div><div className="od">OpenAI/Anthropic-compatible base URL · key never leaves your machine</div></div>
              <div className="r" style={{ flex: '0 0 340px' }}>
                <input className="key-input" style={{ width: '100%' }} aria-label="Command Code base URL"
                  value={viralUrl} placeholder={DEFAULT_VIRAL_BASE_URL}
                  onChange={(e) => setViralUrl(e.target.value)}
                  onBlur={() => saveKeys({ VIRAL_BASE_URL: viralUrl.trim() })} />
              </div>
            </div>
            <KeyRow icon="key-round" name="Command Code" desc="Viral-moment detection · OpenAI/Anthropic-compatible" value={viralKey} present={present.viral}
              onChange={setViralKey}
              onSave={() => saveKeys({
                VIRAL_API_KEY: viralKey,
                VIRAL_BASE_URL: viralUrl.trim(),
                VIRAL_PROVIDER: 'openai',
              }).then(() => loadViralModels('openai', viralKey, viralUrl))}
              onClear={() => { setViralKey(''); saveKeys({ VIRAL_API_KEY: '' }); }} placeholder="Command Code API key" />
          </>
        )}
        <KeyRow icon="audio-lines" name="Deepgram" desc="Nova-3 transcription" value={deepgram} present={present.deepgram}
          onChange={setDeepgram} onSave={() => saveKeys({ DEEPGRAM_API_KEY: deepgram })}
          onClear={() => { setDeepgram(''); saveKeys({ DEEPGRAM_API_KEY: '' }); }} placeholder="dg_…" />
        <KeyRow icon="audio-lines" name="ElevenLabs" desc="Scribe transcription · audio-event tags" value={elevenlabs} present={present.elevenlabs}
          onChange={setElevenlabs} onSave={() => saveKeys({ ELEVENLABS_API_KEY: elevenlabs })}
          onClear={() => { setElevenlabs(''); saveKeys({ ELEVENLABS_API_KEY: '' }); }} placeholder="sk_…" />
        <KeyRow icon="scan-face" name="Hugging Face token" desc="Speaker diarization models" value={hf} present={present.hf}
          onChange={setHf} onSave={() => saveKeys({ HF_TOKEN: hf })}
          onClear={() => { setHf(''); saveKeys({ HF_TOKEN: '' }); }} placeholder="hf_…" />
        <KeyRow icon="rss" name="Twitch client ID" desc="Live Monitor: Twitch channel detection" value={twitchId} present={present.twitchId}
          onChange={setTwitchId} onSave={() => saveKeys({ TWITCH_CLIENT_ID: twitchId })}
          onClear={() => { setTwitchId(''); saveKeys({ TWITCH_CLIENT_ID: '' }); }} placeholder="Helix app client id" />
        <KeyRow icon="rss" name="Twitch client secret" desc="Live Monitor: Twitch channel detection" value={twitchSecret} present={present.twitchSecret}
          onChange={setTwitchSecret} onSave={() => saveKeys({ TWITCH_CLIENT_SECRET: twitchSecret })}
          onClear={() => { setTwitchSecret(''); saveKeys({ TWITCH_CLIENT_SECRET: '' }); }} placeholder="Helix app client secret" />
        <KeyRow icon="key-round" name="API token" desc="Only for LAN deploys with CLIPPYME_API_TOKEN set — stored in this browser, sent as X-API-Token" value={apiToken} present={!!getApiToken()}
          onChange={setApiTokenState} onSave={() => { setApiToken(apiToken); pushToast?.('success', apiToken.trim() ? 'API token saved' : 'API token cleared'); }} placeholder="Shared secret (leave empty + Save to clear)" />
        <div className="opt" style={{ borderBottom: 0 }}>
          <div className="oico"><Icon n="audio-lines" /></div>
          <div className="otxt"><div className="ot">Transcription engine</div><div className="od">Cloud STT falls back to local Whisper if its key is missing</div></div>
          <div className="r"><Segmented value={provider}
            onChange={(id) => { setProvider(id); saveKeys({ TRANSCRIPTION_PROVIDER: id }); }}
            options={[{ id: 'deepgram', label: 'Deepgram' }, { id: 'elevenlabs', label: 'ElevenLabs' }, { id: 'whisper', label: 'Whisper' }]} /></div>
        </div>
        {provider === 'deepgram' && !present.deepgram && (
          <div className="od" style={{ color: 'var(--warn, #f5a623)', padding: '0 0 8px 44px' }}>⚠ No Deepgram key saved — pipeline will use local Whisper.</div>
        )}
        {provider === 'elevenlabs' && !present.elevenlabs && (
          <div className="od" style={{ color: 'var(--warn, #f5a623)', padding: '0 0 8px 44px' }}>⚠ No ElevenLabs key saved — pipeline will use local Whisper.</div>
        )}
        {viralProvider === 'gemini' ? (
          <div className="opt" style={{ borderBottom: 0 }}>
            <div className="oico"><Icon n="sparkles" /></div>
            <div className="otxt"><div className="ot">Gemini model</div><div className="od">Viral-moment detection model · applied to new jobs</div></div>
            <div className="r" style={{ gap: 8 }}>
              <select className="key-input" style={{ width: 'auto', minWidth: 200, fontFamily: 'var(--font-sans)' }}
                value={model}
                onChange={(e) => { setModel(e.target.value); saveKeys({ GEMINI_MODEL: e.target.value }); }}>
                {!model && <option value="">Default (gemini-3.5-flash)</option>}
                {model && !models.some((m) => m.name === model) && <option value={model}>{model}</option>}
                {models.map((m) => <option key={m.name} value={m.name}>{m.display_name || m.name}</option>)}
              </select>
              <Btn variant="ghost" size="sm" icon="refresh-cw" onClick={() => loadModels()} disabled={loadingModels}>
                {loadingModels ? '…' : 'Refresh'}
              </Btn>
            </div>
          </div>
        ) : (
          <div className="opt" style={{ borderBottom: 0 }}>
            <div className="oico"><Icon n="sparkles" /></div>
            <div className="otxt"><div className="ot">Command Code model</div><div className="od">Any model in your Command Code catalog · Claude, GPT and open models</div></div>
            <div className="r" style={{ gap: 8 }}>
              <select className="key-input" style={{ width: 'auto', minWidth: 240, maxWidth: 320, fontFamily: 'var(--font-sans)' }}
                value={viralModel}
                onChange={(e) => { setViralModel(e.target.value); saveKeys({ VIRAL_MODEL: e.target.value }); }}>
                {!viralModel && <option value="">Default (Claude Sonnet 5)</option>}
                {viralModel && !viralModels.some((m) => m.name === viralModel) && <option value={viralModel}>{viralModel}</option>}
                {viralModels.map((m) => <option key={m.name} value={m.name}>{m.display_name || m.name}</option>)}
              </select>
              <Btn variant="ghost" size="sm" icon="refresh-cw" onClick={() => loadViralModels('openai')} disabled={loadingViralModels}>
                {loadingViralModels ? '…' : 'Refresh'}
              </Btn>
            </div>
          </div>
        )}
        {viralProvider === 'openai' && viralModelsError && (
          <div className="od" style={{ color: 'var(--warn, #f5a623)', padding: '0 0 8px 44px' }}>
            ⚠ Could not load models: {viralModelsError}
          </div>
        )}
      </Panel>

      {viralProvider === 'openai' && (
        <Panel title="Two-stage selection" sub="GLM discovers broadly → Opus ranks independently → copy is written last" icon="layers" style={{ marginBottom: 18 }}>
          <div className="opt">
            <div className="oico"><Icon n="layers" /></div>
            <div className="otxt"><div className="ot">Selection mode</div><div className="od">Auto uses two-stage on Command Code · Single keeps the legacy one-model flow</div></div>
            <div className="r"><Segmented value={viralMode}
              onChange={(id) => { setViralMode(id); saveKeys({ VIRAL_MODE: id }); }}
              options={[{ id: 'auto', label: 'Auto' }, { id: 'two_stage', label: 'Two-stage' }, { id: 'single', label: 'Single' }]} /></div>
          </div>
          <div className="opt">
            <div className="oico"><Icon n="sparkles" /></div>
            <div className="otxt"><div className="ot">Discovery model</div><div className="od">High-recall candidate finder · default z-ai/glm-5.3-flash</div></div>
            <div className="r" style={{ flex: '0 0 260px' }}>
              <select className="key-input" style={{ width: '100%', fontFamily: 'var(--font-sans)' }}
                aria-label="Discovery model"
                value={discoveryModel}
                onChange={(e) => { setDiscoveryModel(e.target.value); saveKeys({ VIRAL_DISCOVERY_MODEL: e.target.value }); }}>
                {discoveryModel && !viralModels.some((m) => m.name === discoveryModel) && <option value={discoveryModel}>{discoveryModel}</option>}
                {viralModels.map((m) => <option key={m.name} value={m.name}>{m.display_name || m.name}</option>)}
              </select>
            </div>
          </div>
          <div className="opt">
            <div className="oico"><Icon n="trending-up" /></div>
            <div className="otxt"><div className="ot">Review model</div><div className="od">Independent editorial scoring, ranking and final selection · default claude-opus-5-5</div></div>
            <div className="r" style={{ flex: '0 0 260px' }}>
              <select className="key-input" style={{ width: '100%', fontFamily: 'var(--font-sans)' }}
                aria-label="Review model"
                value={reviewModel}
                onChange={(e) => { setReviewModel(e.target.value); saveKeys({ VIRAL_REVIEW_MODEL: e.target.value }); }}>
                {reviewModel && !viralModels.some((m) => m.name === reviewModel) && <option value={reviewModel}>{reviewModel}</option>}
                {viralModels.map((m) => <option key={m.name} value={m.name}>{m.display_name || m.name}</option>)}
              </select>
            </div>
          </div>
          <div className="opt">
            <div className="oico"><Icon n="type" /></div>
            <div className="otxt"><div className="ot">Copy model</div><div className="od">Writes titles/captions/hooks only for selected clips · blank reuses the discovery model</div></div>
            <div className="r" style={{ flex: '0 0 260px' }}>
              <select className="key-input" style={{ width: '100%', fontFamily: 'var(--font-sans)' }}
                aria-label="Copy model"
                value={copyModel}
                onChange={(e) => { setCopyModel(e.target.value); saveKeys({ VIRAL_COPY_MODEL: e.target.value }); }}>
                <option value="">Same as discovery</option>
                {copyModel && !viralModels.some((m) => m.name === copyModel) && <option value={copyModel}>{copyModel}</option>}
                {viralModels.map((m) => <option key={m.name} value={m.name}>{m.display_name || m.name}</option>)}
              </select>
            </div>
          </div>
          <div className="opt">
            <div className="oico"><Icon n="sliders-horizontal" /></div>
            <div className="otxt"><div className="ot">Discovery candidates</div><div className="od">Recall-oriented cap before review (4-60)</div></div>
            <div className="r"><input className="key-input" style={{ width: 90 }} type="number" min="4" max="60"
              aria-label="Maximum discovery candidates" value={maxCandidates}
              onChange={(e) => setMaxCandidates(e.target.value)}
              onBlur={() => saveKeys({ VIRAL_MAX_CANDIDATES: maxCandidates })} /></div>
          </div>
          <div className="opt">
            <div className="oico"><Icon n="scissors" /></div>
            <div className="otxt"><div className="ot">Final selected clips</div><div className="od">Hard ceiling after review (1-20)</div></div>
            <div className="r"><input className="key-input" style={{ width: 90 }} type="number" min="1" max="20"
              aria-label="Maximum final selected clips" value={maxSelected}
              onChange={(e) => setMaxSelected(e.target.value)}
              onBlur={() => saveKeys({ VIRAL_MAX_SELECTED_CLIPS: maxSelected })} /></div>
          </div>
          <div className="opt">
            <div className="oico"><Icon n="flame" /></div>
            <div className="otxt"><div className="ot">Editorial quality threshold</div><div className="od">0-10 mean of the Opus rubric · below this a clip is rejected</div></div>
            <div className="r"><input className="key-input" style={{ width: 90 }} type="number" min="0" max="10" step="0.5"
              aria-label="Minimum editorial quality" value={minQuality}
              onChange={(e) => setMinQuality(e.target.value)}
              onBlur={() => saveKeys({ VIRAL_MIN_QUALITY: minQuality })} /></div>
          </div>
          <div className="opt" style={{ borderBottom: 0 }}>
            <div className="oico"><Icon n="panel-top" /></div>
            <div className="otxt"><div className="ot">If a stage fails</div><div className="od">Fall back to the legacy single-stage flow, or return no clips</div></div>
            <div className="r"><Segmented value={stageFallback}
              onChange={(id) => { setStageFallback(id); saveKeys({ VIRAL_TWO_STAGE_FALLBACK: id }); }}
              options={[{ id: 'single', label: 'Fallback' }, { id: 'off', label: 'No fallback' }]} /></div>
          </div>
        </Panel>
      )}

      <Panel title="AI captions" sub="Optional OpenAI-compatible model for caption optimization" icon="sparkles" style={{ marginBottom: 18 }}>
        <div className="zernio-card" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <div className="zico"><Icon n="sparkles" /></div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="kt">OpenAI-compatible endpoint</div>
              <div className="kd">Powers “AI captions” on the results page: your series context + each clip&apos;s transcript → a caption per clip. Works with OpenAI, OpenRouter, Ollama, LM Studio…</div>
            </div>
            {present.openai && <span className="conn"><Icon n="circle-check" />Connected</span>}
          </div>
          <input className="key-input" style={{ width: '100%', fontFamily: 'var(--font-sans)' }}
            aria-label="OpenAI-compatible endpoint URL"
            value={openaiUrl} placeholder="https://api.openai.com/v1"
            onChange={(e) => setOpenaiUrl(e.target.value)} />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <input className="key-input" style={{ width: '100%', fontFamily: 'var(--font-sans)' }}
              aria-label="AI caption model"
              value={openaiModel} placeholder="gpt-4o-mini"
              onChange={(e) => setOpenaiModel(e.target.value)} />
            <input className="key-input" style={{ width: '100%' }} type="password"
              aria-label="OpenAI-compatible API key"
              value={openaiKey} placeholder={present.openai ? 'Replace API key (optional)' : 'API key (sk-…)'}
              onChange={(e) => setOpenaiKey(e.target.value)} />
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <Btn variant="secondary" size="sm" icon="key-round"
              disabled={!openaiUrl.trim()}
              onClick={() => saveKeys({
                OPENAI_CAPTIONS_BASE_URL: openaiUrl.trim(),
                OPENAI_CAPTIONS_API_KEY: openaiKey.trim(),
                OPENAI_CAPTIONS_MODEL: openaiModel.trim() || 'gpt-4o-mini',
              })}>Save</Btn>
            {present.openai && (
              <Btn variant="ghost" size="sm" icon="trash-2"
                onClick={() => saveKeys({
                  OPENAI_CAPTIONS_BASE_URL: '',
                  OPENAI_CAPTIONS_API_KEY: '',
                  OPENAI_CAPTIONS_MODEL: '',
                })}>Clear</Btn>
            )}
          </div>
        </div>
      </Panel>

      <Panel title="Publishing" sub="Push finished clips to socials via Zernio" icon="send" style={{ marginBottom: 18 }}>
        <div className="zernio-card" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <div className="zico"><Icon n="rss" /></div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="kt">Zernio</div>
              <div className="kd">{profiles.length
                ? `${profiles.length} profile${profiles.length > 1 ? 's' : ''} · the default one is what "Publish" uses`
                : 'Add a named profile (API key + account IDs) per account to schedule posts'}</div>
            </div>
            {zernio?.configured && <span className="conn"><Icon n="circle-check" />Connected</span>}
          </div>

          {profiles.map((p, i) => (
            <div key={p.id || p.name || `new-${i}`} className="zernio-profile"
              style={{ border: '1px solid var(--line-1)', borderRadius: 'var(--r-md)', padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <input className="key-input" style={{ flex: 1, fontFamily: 'var(--font-sans)' }}
                  aria-label={`Profile ${i + 1} name`} placeholder="Profile name (e.g. Client A)"
                  value={p.name || ''} onChange={(e) => patchProfile(i, { name: e.target.value })} />
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap', color: 'var(--fg-2)', fontSize: 13, cursor: 'pointer' }}>
                  <input type="radio" name="zernio-default" checked={!!p.is_default}
                    aria-label={`Make ${p.name || 'profile'} the default`}
                    onChange={() => setDefault(i)} />
                  Default
                </label>
                <button type="button" className="mini" aria-label={`Delete ${p.name || 'profile'}`} title="Delete profile"
                  onClick={() => removeProfile(i)}><Icon n="x" /></button>
              </div>
              <input className="key-input" style={{ width: '100%' }} type="password"
                aria-label={`${p.name || 'profile'} API key`}
                placeholder={p.api_key_masked ? `Replace key (${p.api_key_masked})` : 'Zernio API key (sk_…)'}
                value={p.api_key || ''} onChange={(e) => patchProfile(i, { api_key: e.target.value })} />
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 8 }}>
                {['tiktok', 'instagram', 'youtube'].map((pp) => (
                  <input key={pp} className="key-input" style={{ width: '100%', fontFamily: 'var(--font-sans)' }}
                    aria-label={`${p.name || 'profile'} ${pp} account id`}
                    value={p.accounts?.[pp] || ''} placeholder={`${pp} account id`}
                    onChange={(e) => patchAccount(i, pp, e.target.value)} />
                ))}
              </div>
              <div style={{ border: '1px solid var(--line-1)', borderRadius: 'var(--r-md)', padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-2)' }}>YouTube upload defaults</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--fg-2)' }}>
                    Category
                    <select className="key-input" style={{ width: '100%', fontFamily: 'var(--font-sans)' }}
                      aria-label={`${p.name || 'profile'} YouTube category`}
                      value={p.youtube?.category_id || '22'}
                      onChange={(e) => patchProfileYoutube(i, 'category_id', e.target.value)}>
                      {Object.entries(YT_CATEGORIES).map(([id, label]) => (
                        <option key={id} value={id}>{label}</option>
                      ))}
                    </select>
                  </label>
                  <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--fg-2)' }}>
                    Visibility
                    <select className="key-input" style={{ width: '100%', fontFamily: 'var(--font-sans)' }}
                      aria-label={`${p.name || 'profile'} YouTube visibility`}
                      value={p.youtube?.visibility || 'public'}
                      onChange={(e) => patchProfileYoutube(i, 'visibility', e.target.value)}>
                      <option value="public">Public</option>
                      <option value="unlisted">Unlisted</option>
                      <option value="private">Private</option>
                    </select>
                  </label>
                </div>
                <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: 'var(--fg-2)', cursor: 'pointer' }}>
                    <input type="checkbox"
                      aria-label={`${p.name || 'profile'} made for kids`}
                      checked={!!p.youtube?.made_for_kids}
                      onChange={(e) => patchProfileYoutube(i, 'made_for_kids', e.target.checked)} />
                    Made for kids (COPPA)
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, color: 'var(--fg-2)', cursor: 'pointer' }}>
                    <input type="checkbox"
                      aria-label={`${p.name || 'profile'} contains AI-generated content`}
                      checked={!!p.youtube?.contains_synthetic_media}
                      onChange={(e) => patchProfileYoutube(i, 'contains_synthetic_media', e.target.checked)} />
                    Contains AI-generated content
                  </label>
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <Btn variant="ghost" size="sm" icon="rss" disabled={discovering === (p.id || p.name)}
                  onClick={() => discover(p)}>
                  {discovering === (p.id || p.name) ? 'Discovering…' : 'Discover'}
                </Btn>
              </div>
            </div>
          ))}

          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <Btn variant="ghost" size="sm" icon="plus" onClick={addProfile}>Add profile</Btn>
            <div style={{ flex: 1 }} />
            <Btn variant="primary" size="sm" icon="check" onClick={saveZernioCfg}>Save</Btn>
          </div>
        </div>
      </Panel>

      <Panel title="Brand assets" sub="Logo overlay + custom subtitle fonts" icon="stamp" style={{ marginBottom: 18 }}>
        <div className="opt">
          <div className="oico"><Icon n="image" /></div>
          <div className="otxt"><div className="ot">Brand logo</div><div className="od">{logoOn ? 'Configured · burned on clips when the Logo layer is on' : 'Upload a transparent PNG to overlay on clips'}</div></div>
          <div className="r" style={{ gap: 8 }}>
            <label className="btn btn-secondary btn-sm" style={{ cursor: 'pointer' }}>
              <Icon n="upload" />Upload
              <input type="file" accept="image/png,.png" hidden onChange={onLogoFile} />
            </label>
            {logoOn && <Btn variant="ghost" size="sm" icon="trash-2" onClick={removeLogo}>Remove</Btn>}
          </div>
        </div>
        <div className="opt" style={{ borderBottom: 0, alignItems: 'flex-start' }}>
          <div className="oico"><Icon n="baseline" /></div>
          <div className="otxt" style={{ flex: 1 }}>
            <div className="ot">Subtitle fonts</div>
            <div className="od">Upload a .ttf/.otf (e.g. Stratos) to use in classic captions</div>
            {fonts.filter((n) => !bundled.has(n)).length > 0 && (
              <div className="s-sub" style={{ marginTop: 10 }}>
                {fonts.filter((n) => !bundled.has(n)).map((n) => (
                  <span key={n} className="chip" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    {n}
                    <button type="button" className="mini" aria-label={`Remove ${n}`} title="Remove" onClick={() => removeFont(n)}><Icon n="x" /></button>
                  </span>
                ))}
              </div>
            )}
          </div>
          <div className="r">
            <label className="btn btn-secondary btn-sm" style={{ cursor: 'pointer' }}>
              <Icon n="upload" />Upload
              <input type="file" accept=".ttf,.otf,.ttc,font/ttf,font/otf" hidden onChange={onFontFile} />
            </label>
          </div>
        </div>
      </Panel>

      <Panel title="Downloads" sub="For age- or region-restricted sources" icon="cookie">
        <div className="opt" style={{ borderBottom: 0 }}>
          <div className="oico"><Icon n="cookie" /></div>
          <div className="otxt"><div className="ot">YouTube cookies</div><div className="od">{cookies ? 'Configured · restricted videos OK' : 'Not set · public videos only'}</div></div>
          <div className="r" style={{ gap: 8 }}>
            <label className="btn btn-secondary btn-sm" style={{ cursor: 'pointer' }}>
              <Icon n="upload" />Upload
              <input type="file" accept=".txt" hidden onChange={onCookieFile} />
            </label>
            {cookies && <Btn variant="ghost" size="sm" icon="trash-2" onClick={removeCookies}>Remove</Btn>}
          </div>
        </div>
      </Panel>
    </div>
  );
}

export function ApiKeyModal({ onClose, onGoToSettings }) {
  const panelRef = useModalA11y(onClose);
  return (
    // Backdrop click is a mouse-only convenience; keyboard users close via
    // Esc (useModalA11y). currentTarget guard replaces stopPropagation.
    <div className="overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" ref={panelRef}
        role="dialog" aria-modal="true" aria-labelledby="apikey-modal-title">
        <div className="modal-head"><h3 id="apikey-modal-title">Add a viral-detection key</h3><button className="x" onClick={onClose} aria-label="Close"><Icon n="x" /></button></div>
        <div className="modal-body">
          <p style={{ color: 'var(--fg-2)', fontSize: 14, lineHeight: 1.55 }}>
            ClippyMe needs a Gemini key or a Command Code key to score the transcript and find viral moments. It&apos;s stored locally and never leaves your machine.
          </p>
        </div>
        <div className="modal-foot">
          <Btn variant="ghost" onClick={onClose}>Later</Btn>
          <div className="mf-right"><Btn variant="primary" icon="settings" onClick={onGoToSettings}>Open settings</Btn></div>
        </div>
      </div>
    </div>
  );
}
