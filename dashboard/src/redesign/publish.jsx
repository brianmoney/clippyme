// ClippyMe redesign — PublishModal: real concurrent publish to Zernio.
// Every selected clip is published in parallel (Promise.allSettled) — the fix
// for the old sequential stall — each row showing live queued→uploading→
// live/error status. Per-clip compose_first honours the clip's toggles.
import { useState, useEffect, useRef } from 'react';
import { Icon, Social, Btn, Switch, Stepper, PlatPill, PLATFORMS } from './primitives';
import { clipVideoSrc } from './realApi';
import { publishClip, getZernio } from './realApi';
import { seedToggles, seedHookParams, seedSubtitleParams, seedLogoParams, seedBannerParams } from '../lib/seedClipParams';
import { localDatePlus } from '../lib/scheduleDates';
import { useModalA11y } from './useModalA11y';

// redesign plat id → backend platform + account key. Exported so other
// surfaces publishing to Zernio (live.jsx) don't re-derive this mapping.
export const PLAT = {
  tiktok: { platform: 'tiktok', acct: 'tiktok', icon: 'tiktok', label: 'TikTok' },
  ig: { platform: 'instagram', acct: 'instagram', icon: 'instagram', label: 'Reels' },
  yt: { platform: 'youtube', acct: 'youtube', icon: 'youtube', label: 'Shorts' },
};

// Curated IANA timezones for the schedule picker (backend validates ZoneInfo,
// so only valid names). Current stored value is always offered even if absent.
export const TIMEZONES = [
  'Europe/Rome', 'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Madrid',
  'Europe/Lisbon', 'Europe/Amsterdam', 'Europe/Stockholm', 'Europe/Athens', 'Europe/Istanbul',
  'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
  'America/Toronto', 'America/Mexico_City', 'America/Sao_Paulo',
  'Asia/Tokyo', 'Asia/Seoul', 'Asia/Shanghai', 'Asia/Kolkata', 'Asia/Dubai', 'Asia/Singapore',
  'Australia/Sydney', 'Africa/Johannesburg', 'Pacific/Auckland',
];

function PubRow({ clip, idx, st, plats }) {
  // `st` is either a status string or { state, error } so we can surface the
  // real failure reason instead of a bare "failed".
  const status = typeof st === 'object' && st ? st.state : st;
  const errMsg = typeof st === 'object' && st ? st.error : null;
  const tasks = Object.keys(plats).filter((k) => plats[k]);
  const done = status === 'done';
  const error = status === 'error';
  return (
    <div className={'pubrow' + (done ? ' done' : '')}>
      <div className="pthumb" style={{ background: '#000', overflow: 'hidden' }}>
        <video src={clipVideoSrc(clip)} muted playsInline preload="metadata"
          style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
      </div>
      <div className="pinfo">
        <div className="pttl">{clip.video_title_for_youtube_short || `Clip ${idx + 1}`}</div>
        <div className="pplats">
          {tasks.map((p) => (
            <div className="pp" key={p}>
              <Social n={PLAT[p].icon} color={done ? '02C5BF' : '7E7E8F'} size={13} />
              <div className="ptrack"><i className={p} style={{ width: done ? '100%' : status === 'uploading' ? '70%' : '0%', transition: 'width .4s' }}></i></div>
            </div>
          ))}
          <span className={'pstat' + (done ? ' done' : status === 'uploading' ? '' : ' wait')}
            style={error ? { color: 'var(--danger)' } : undefined}
            title={error && errMsg ? errMsg : undefined}>
            {error ? (errMsg ? `failed: ${errMsg.slice(0, 60)}` : 'failed') : done ? 'live' : status === 'uploading' ? 'uploading' : 'queued'}
          </span>
        </div>
      </div>
      <div className="pcheck"><Icon n={done ? 'check' : error ? 'x' : 'loader'} /></div>
    </div>
  );
}

export function PublishModal({ clips, jobId, clipStates = {}, preselections, onClose, onPublished, pushToast, onCaptionChange }) {
  const all = clips.length > 1;
  const [zernio, setZernio] = useState(null);
  const [plats, setPlats] = useState({ tiktok: true, ig: true, yt: false });
  const [schedule, setSchedule] = useState(true);
  // Per-clip captions keyed by the clip's array position `_idx`. Seeded from
  // the persisted per-clip state (grid caption editor), then the clip's own
  // metadata, so a batch never shares one caption. Edits here write back via
  // `onCaptionChange` so they survive a close/reopen.
  const [captions, setCaptions] = useState(() => {
    const init = {};
    clips.forEach((clip) => {
      const idx = clip._idx;
      const cs = clipStates[idx] || {};
      init[idx] = cs.caption !== undefined ? cs.caption : (clip.tiktok_caption || clip.video_title_for_youtube_short || '');
    });
    return init;
  });
  const setCaption = (idx, value) => {
    setCaptions((m) => ({ ...m, [idx]: value }));
    onCaptionChange?.(idx, value);
  };
  const [stage, setStage] = useState('setup'); // setup | uploading | done
  const [progress, setProgress] = useState({});
  // Scheduled batch knobs: how many clips publish per day (default 1 = the
  // original one-clip-per-day spacing), how many days from now the schedule
  // starts, and the prime-time timezone (defaults to the Zernio-config
  // timezone, override per batch here).
  const [perDay, setPerDay] = useState(1);
  const [daysFromNow, setDaysFromNow] = useState(0);
  const [tz, setTz] = useState('Europe/Rome');
  const tzTouched = useRef(false);

  useEffect(() => { getZernio().then(setZernio).catch(() => setZernio({ configured: false })); }, []);

  // Seed the timezone from the saved Zernio config once it loads, unless the
  // user already picked one for this batch.
  useEffect(() => {
    if (zernio?.timezone && !tzTouched.current) setTz(zernio.timezone);
  }, [zernio]);

  // Accessibility: focus trap + Escape-to-close + focus restore.
  const panelRef = useModalA11y(onClose);

  // Guard the post-publish setTimeout so it never calls setState after the
  // modal has been unmounted (e.g. parent closes it while the delay is in flight).
  const mountedRef = useRef(true);
  useEffect(() => () => { mountedRef.current = false; }, []);

  const accounts = zernio?.accounts || {};
  const toggle = (k) => setPlats((p) => ({ ...p, [k]: !p[k] }));
  const platTargets = () => Object.keys(plats)
    .filter((k) => plats[k] && accounts[PLAT[k].acct])
    .map((k) => ({ platform: PLAT[k].platform, accountId: accounts[PLAT[k].acct] }));
  const targets = platTargets();
  const ready = zernio?.configured && targets.length > 0;

  // `batchPos` is the clip's position within this batch (0-based). When
  // scheduling, the schedule starts `daysFromNow` days out and `perDay` clips
  // share each day: start_date = today + daysFromNow + floor(batchPos / perDay).
  // The backend SmartScheduler anti-collides clips on the same day (>= min_gap
  // apart), so X-per-day "just works".
  const buildBody = (clip, idx, batchPos = 0, scheduleOn = schedule) => {
    const cs = clipStates[idx] || {};
    const toggles = cs.toggles ?? seedToggles(preselections);
    const any = Object.values(toggles).some(Boolean);
    const hookParams = cs.hookParams ?? seedHookParams(clip, preselections);
    const subtitleParams = cs.subtitleParams ?? seedSubtitleParams(preselections);
    const logoParams = cs.logoParams ?? seedLogoParams(preselections);
    const gradeParams = cs.gradeParams ?? { preset: preselections?.grade?.preset || 'none' };
    const bannerParams = cs.bannerParams ?? seedBannerParams(preselections);
    const title = (clip.video_title_for_youtube_short || `Clip ${idx + 1}`).slice(0, 100);
    return {
      title,
      caption: (captions[idx] && captions[idx].trim()) || title,
      platforms: targets,
      schedule_mode: scheduleOn ? 'auto' : 'now',
      ...(scheduleOn ? { start_date: localDatePlus(daysFromNow + Math.floor(batchPos / Math.max(1, perDay))) } : {}),
      timezone: tz.trim() || 'Europe/Rome',
      tiktok_settings: plats.tiktok && accounts.tiktok ? {
        privacy_level: 'PUBLIC_TO_EVERYONE', allow_comment: true, allow_duet: true,
        allow_stitch: true, content_preview_confirmed: true, express_consent_given: true,
      } : undefined,
      ...(any ? { compose_first: true, toggles, hook_params: toggles.hook ? hookParams : {}, subtitle_params: toggles.subtitles ? subtitleParams : {}, logo_params: toggles.logo ? logoParams : {}, grade_params: toggles.grade ? gradeParams : {}, banner_params: toggles.banner ? bannerParams : {}, drop_ranges: toggles.smartcut ? (cs.dropRanges || []) : [] } : {}),
    };
  };

  const run = async (forceNow = false) => {
    setStage('uploading');
    const init = {};
    clips.forEach((c) => { init[c._idx] = { state: 'uploading' }; });
    setProgress(init);
    // `forceNow` overrides the schedule toggle (the "Publish now" button) —
    // the toggle state must not leak into the body via a stale closure.
    const scheduleOn = schedule && !forceNow;
    const results = await Promise.allSettled(clips.map(async (clip, batchPos) => {
      const idx = clip._idx;
      // Resolve to the backend's ABSOLUTE `shorts` position for the actual
      // publish call — `idx` (array position) stays the key into local
      // clipStates/progress, which are unaffected by a manual-publish gap.
      const apiIdx = clip._apiIdx ?? idx;
      try {
        await publishClip(jobId, apiIdx, buildBody(clip, idx, batchPos, scheduleOn));
        setProgress((p) => ({ ...p, [idx]: { state: 'done' } }));
        onPublished?.(idx);
        return true;
      } catch (e) {
        // Surface the real reason (e.g. a Zernio daily-limit 429) instead of a
        // bare "failed", so the user knows to retry that platform tomorrow.
        setProgress((p) => ({ ...p, [idx]: { state: 'error', error: e?.message || 'Publish failed' } }));
        return false;
      }
    }));
    const ok = results.filter((r) => r.status === 'fulfilled' && r.value).length;
    const fail = clips.length - ok;
    setTimeout(() => {
      if (!mountedRef.current) return;
      setStage('done');
      pushToast?.(fail === 0 ? 'success' : 'warn', `Published ${ok}/${clips.length}${fail ? `, ${fail} failed` : ''}`);
    }, 500);
  };

  const title = stage === 'done' ? (schedule ? 'Scheduled' : 'Published')
    : all ? `Publish ${clips.length} clips` : `Publish · ${clips[0]?.video_title_for_youtube_short || ''}`;

  return (
    // Backdrop click is a mouse-only convenience; keyboard users close via
    // Esc (useModalA11y). currentTarget guard replaces stopPropagation.
    <div className="overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={'modal' + (all ? ' wide' : '')} ref={panelRef}
        role="dialog" aria-modal="true" aria-labelledby="publish-modal-title">
        <div className="modal-head">
          <div>
            <h3 id="publish-modal-title">{title}</h3>
            {stage === 'uploading' && <div className="mh-sub">uploading concurrently · daily-limit checks server-side</div>}
          </div>
          <button className="x" onClick={onClose} aria-label="Close"><Icon n="x" /></button>
        </div>

        {stage === 'setup' && (
          <>
            <div className="modal-body">
              {!zernio ? <div className="cm-small">Loading Zernio…</div> : !zernio.configured ? (
                <div className="empty" style={{ padding: '24px 12px' }}>
                  <div className="ei"><Icon n="rss" /></div>
                  <h3>Zernio not connected</h3>
                  <p>Add your Zernio API key + account IDs in Settings to publish.</p>
                </div>
              ) : (
                <>
                  <div className="field">
                    <span className="field-label">Platforms</span>
                    <div className="plats">
                      {PLATFORMS.map((p) => {
                        const has = !!accounts[PLAT[p.id].acct];
                        return <PlatPill key={p.id} {...p} on={plats[p.id] && has}
                          onClick={() => has ? toggle(p.id) : pushToast?.('warn', `No ${PLAT[p.id].label} account saved`)} />;
                      })}
                    </div>
                  </div>
                  {all ? (
                    <div className="field">
                      <span className="field-label">Captions</span>
                      <div className="capgrid">
                        {clips.map((clip) => {
                          const idx = clip._idx;
                          const clipTitle = clip.video_title_for_youtube_short || `Clip ${idx + 1}`;
                          return (
                            <div className="caprow" key={idx}>
                              <div className="pthumb">
                                <video src={clipVideoSrc(clip)} muted playsInline preload="metadata"
                                  style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                              </div>
                              <div className="capwrap">
                                <div className="pttl">{clipTitle}</div>
                                <textarea className="ta" rows="2" maxLength={2200}
                                  value={captions[idx] || ''}
                                  placeholder="Caption for this clip"
                                  aria-label={`Caption for ${clipTitle}`}
                                  onChange={(e) => setCaption(idx, e.target.value)} />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : (
                    <div className="field">
                      <span className="field-label">Caption</span>
                      <textarea className="ta" rows="3" maxLength={2200} value={captions[clips[0]._idx] || ''}
                        aria-label="Caption"
                        onChange={(e) => setCaption(clips[0]._idx, e.target.value)}></textarea>
                    </div>
                  )}
                  <div className="opt" style={{ borderBottom: 0 }}>
                    <div className="oico"><Icon n="calendar-clock" /></div>
                    <div className="otxt"><div className="ot">Schedule for prime time</div><div className="od">SmartScheduler picks the slot · off = publish now</div></div>
                    <div className="r"><Switch on={schedule} onChange={setSchedule} /></div>
                  </div>
                  {schedule && (
                    <>
                      {all && (
                        <div className="opt">
                          <div className="oico"><Icon n="layers" /></div>
                          <div className="otxt">
                            <div className="ot">Posts per day</div>
                            <div className="od">{clips.length} clips → {Math.ceil(clips.length / Math.max(1, perDay))} day{Math.ceil(clips.length / Math.max(1, perDay)) === 1 ? '' : 's'} · starts in {daysFromNow} day{daysFromNow === 1 ? '' : 's'}</div>
                          </div>
                          <div className="r"><Stepper value={perDay} set={setPerDay} min={1} max={Math.max(1, clips.length)} label="Posts per day" /></div>
                        </div>
                      )}
                      {all && (
                        <div className="opt">
                          <div className="oico"><Icon n="clock" /></div>
                          <div className="otxt"><div className="ot">Days from now</div><div className="od">Delay the whole schedule by this many days</div></div>
                          <div className="r"><Stepper value={daysFromNow} set={setDaysFromNow} min={0} max={30} label="Days from now" /></div>
                        </div>
                      )}
                      <div className="opt" style={{ borderBottom: 0 }}>
                        <div className="oico"><Icon n="globe" /></div>
                        <div className="otxt"><div className="ot">Timezone</div><div className="od">Prime-time slots are computed in this zone</div></div>
                        <div className="r">
                          <select className="key-input" style={{ width: 'auto', maxWidth: 180, fontFamily: 'var(--font-sans)' }}
                            aria-label="Schedule timezone"
                            value={tz}
                            onChange={(e) => { tzTouched.current = true; setTz(e.target.value); }}>
                            {tz && !TIMEZONES.includes(tz) && <option value={tz}>{tz}</option>}
                            {TIMEZONES.map((zone) => <option key={zone} value={zone}>{zone}</option>)}
                          </select>
                        </div>
                      </div>
                    </>
                  )}
                </>
              )}
            </div>
            <div className="modal-foot">
              <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
              <div className="mf-right">
                <Btn variant="secondary" icon="send" disabled={!ready} onClick={() => run(true)}>Publish now</Btn>
                <Btn variant="grad" icon="calendar-clock" disabled={!ready} onClick={() => run()}>{schedule ? 'Schedule' : 'Queue'}</Btn>
              </div>
            </div>
          </>
        )}

        {stage === 'uploading' && (
          <div className="modal-body">
            <div className="pubgrid">
              {clips.map((c) => <PubRow key={c._idx} clip={c} idx={c._idx} st={progress[c._idx]} plats={plats} />)}
            </div>
          </div>
        )}

        {stage === 'done' && (
          <div className="modal-body" style={{ textAlign: 'center', padding: '36px 24px' }}>
            <div style={{ width: 60, height: 60, borderRadius: '50%', background: 'var(--success-bg)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 18px' }}>
              <Icon n={schedule ? 'calendar-check' : 'party-popper'} style={{ width: 28, height: 28, color: 'var(--brand-teal)' }} />
            </div>
            <div style={{ fontWeight: 700, fontSize: 18 }}>{all ? `${clips.length} clips ` : 'Clip '}{schedule ? 'scheduled' : 'published'}</div>
            <p style={{ color: 'var(--fg-3)', fontSize: 13.5, marginTop: 8, lineHeight: 1.5 }}>
              {schedule ? 'Queued via Zernio for the next prime-time slot.' : 'Sent to Zernio for immediate publish.'}
            </p>
            <div style={{ marginTop: 22 }}><Btn variant="secondary" onClick={onClose}>Done</Btn></div>
          </div>
        )}
      </div>
    </div>
  );
}
