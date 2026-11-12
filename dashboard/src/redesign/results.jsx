
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Icon, Btn, Badge } from './primitives';
import { LazyVideo } from './LazyVideo';
import { AiCaptionPanel } from './aiCaptions';
import { clipPreviewSrc, fmtDuration, downloadClip, exportClip, getClipTranscript, composeClip } from './realApi';
import { seedToggles, seedHookParams, seedSubtitleParams, seedLogoParams, seedGradeParams, seedBannerParams, seedTopTextParams } from '../lib/seedClipParams';
import { processingLabel } from '../hooks/useClipStates';

const REFRAME_ICON = { auto: 'crop', subject: 'scan-face', object: 'scan-face', disabled: 'square' };
const REFRAME_LABEL = { auto: 'Auto', subject: 'Subject', object: 'Subject', disabled: 'Off' };
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const ClipCard = memo(function ClipCard({ clip, index, jobId, state, preselections, onUpdate, onEdit, onApplyToAll, selectMode, onPublish, pushToast }) {
  const [downloading, setDownloading] = useState(false);
  const [showTranscript, setShowTranscript] = useState(false);
  const [showCaption, setShowCaption] = useState(false);
  const [captionDraft, setCaptionDraft] = useState('');
  const [transcript, setTranscript] = useState(null); // null = not loaded yet
  const [txLoading, setTxLoading] = useState(false);
  const [txErr, setTxErr] = useState(false);
  const [copied, setCopied] = useState(false);
  const transcriptText = useMemo(() => (transcript?.segments || []).map((s) => s.text).filter(Boolean).join(' ').trim(), [transcript]);
  const hasCaption = !!(state?.caption || clip.tiktok_caption);
  const selected = state?.selected !== false;
  const score = Math.round(clip.viral_score || 0);
  const mode = state?.reframeMode || clip.reframe_mode || 'auto';
  const title = clip.video_title_for_youtube_short || `Clip ${index + 1}`;
  const processing = !!state?.processing;
  const busyLabel = processingLabel(state);
  const selectionProps = selectMode ? {
    role: 'checkbox',
    tabIndex: 0,
    'aria-checked': selected,
    onClick: () => onUpdate(index, { selected: !selected }),
    onKeyDown: (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        onUpdate(index, { selected: !selected });
      }
    },
  } : {};

  const doDownload = async (event) => {
    event.stopPropagation();
    if (downloading || processing) return;
    setDownloading(true);
    try {
      const kind = await exportClip(jobId, index, clip, state, preselections);
      pushToast?.('success', kind === 'composed' ? 'Composed clip downloaded' : 'Clip downloaded');
    } catch {
      pushToast?.('warn', 'Compose failed; downloading the raw clip instead');
      downloadClip(clip, index);
    } finally {
      setDownloading(false);
    }
  };

  // Toggle the in-gallery transcript panel. Transcript is fetched lazily from
  // the same /api/transcript endpoint the manual-trim tab uses (clip-relative
  // segments); it is cached once loaded so toggling is instant afterwards.
  const toggleTranscript = async (event) => {
    event.stopPropagation();
    if (showTranscript) { setShowTranscript(false); return; }
    setShowTranscript(true);
    if (transcript !== null) return;
    setTxLoading(true);
    setTxErr(false);
    try {
      const data = await getClipTranscript(jobId, clip.original_index ?? index);
      setTranscript(data);
    } catch {
      setTxErr(true);
    } finally {
      setTxLoading(false);
    }
  };

  // Copy the clip's full transcript text to the clipboard. Uses the async
  // Clipboard API when available (secure contexts incl. localhost), falling
  // back to execCommand for LAN/plain-http hosts.
  const copyTranscript = async (event) => {
    event.stopPropagation();
    if (!transcriptText) return;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(transcriptText);
      } else {
        const ta = document.createElement('textarea');
        ta.value = transcriptText;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      setCopied(true);
      pushToast?.('success', 'Transcript copied to clipboard');
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      pushToast?.('warn', 'Could not copy transcript');
    }
  };

  // Toggle the in-gallery caption editor. The draft is seeded once when the
  // panel opens: from the persisted per-clip state first (set here or in the
  // publish modal), then the clip's own metadata caption, else empty.
  const toggleCaption = (event) => {
    event.stopPropagation();
    if (showCaption) { setShowCaption(false); return; }
    setCaptionDraft(state?.caption ?? clip.tiktok_caption ?? '');
    setShowCaption(true);
  };

  // The AI-captions batch (aiCaptions.jsx) writes state.caption straight into
  // clipStates without going through this card's own onChange, so if the
  // panel is left open across a regenerate the draft needs to pick up the
  // new text instead of silently keeping the stale one on screen.
  useEffect(() => {
    if (showCaption) setCaptionDraft(state?.caption ?? clip.tiktok_caption ?? '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.caption]);

  return (
    <article {...selectionProps} className={`clip${score >= 90 ? ' top' : ''}${selectMode && selected ? ' sel' : ''}`}>
      <div className="clip-media" style={{ padding: 0, background: '#000' }}>
        <LazyVideo key={clipPreviewSrc(clip, state)} src={clipPreviewSrc(clip, state)} controls={!selectMode} playsInline muted={selectMode}
          aria-label={`Preview ${title}`} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', zIndex: 0 }} />
        <div className="clip-top" style={{ padding: 10 }}>
          <span className="score"><Icon n="flame" />{score}</span>
          {selectMode ? <span className="clip-check" aria-hidden="true"><Icon n="check" /></span>
            : <span className="rf-badge" title={`Reframe: ${REFRAME_LABEL[mode] || mode}`}><Icon n={REFRAME_ICON[mode] || 'crop'} />{REFRAME_LABEL[mode] || 'Auto'}</span>}
        </div>
        <div className="clip-bottom" style={{ padding: 10 }}>
          {state?.publishedAt && <span className="clip-pub"><Icon n="check" />published</span>}
          <span className="dur" style={{ marginLeft: state?.publishedAt ? 8 : 0 }}>{fmtDuration(clip.start, clip.end)}</span>
        </div>
        {processing && <div className="clip-busy" role="status"><Icon n="loader" /><span>{busyLabel}</span></div>}
      </div>
      {!selectMode && (
        <button type="button" className="clip-edit" disabled={processing} onClick={(event) => { event.stopPropagation(); onEdit(clip, index); }}
          aria-label={`Edit and reprocess ${title}`}><Icon n={processing ? 'loader' : 'sliders-horizontal'} />{processing ? busyLabel : 'Edit & reprocess'}</button>
      )}
      <div className="clip-foot">
        <span className="ttl" title={title}>{title}</span>
        {/* In selectMode the whole card is a role="checkbox" — nested buttons
            inside a checkbox role are invalid for assistive tech, so the
            action cluster only renders in the normal (non-select) mode. */}
        {!selectMode && <>
        <button type="button" className="mini" title="Show / hide this clip's transcript" aria-label="Toggle transcript"
          aria-expanded={showTranscript} onClick={toggleTranscript}><Icon n="captions" /></button>
        <button type="button" className={`mini${hasCaption ? ' has-caption' : ''}`}
          title={hasCaption ? "Edit this clip's publish caption (caption set)" : "Edit this clip's publish caption"}
          aria-label="Toggle caption" aria-expanded={showCaption} onClick={toggleCaption}><Icon n="type" /></button>
        <button type="button" className="mini" title="Apply these settings to all clips" aria-label="Apply settings to all clips" disabled={processing}
          onClick={(event) => { event.stopPropagation(); if (!processing && window.confirm("Apply this clip's settings to every other clip? Manual trim and per-clip hook text are not copied.")) onApplyToAll(index); }}><Icon n="copy" /></button>
        <button type="button" className="mini" title="Download with edits" aria-label={`Download ${title}`} disabled={downloading || processing} onClick={doDownload}><Icon n={downloading ? 'loader' : 'download'} /></button>
        <button type="button" className="mini" title="Publish" aria-label={`Publish ${title}`} disabled={processing} onClick={(event) => { event.stopPropagation(); onPublish({ ...clip, _idx: index, _apiIdx: clip.original_index ?? index }); }}><Icon n="send" /></button>
        <button type="button" className="mini" title="Remove from grid" aria-label={`Remove ${title}`} onClick={(event) => {
          event.stopPropagation();
          if (window.confirm('Remove this clip from the grid? The file stays on disk.')) { onUpdate(index, { deleted: true }); pushToast?.('info', 'Clip removed'); }
        }}><Icon n="trash-2" /></button>
        </>}
      </div>
      {showTranscript && (
        <div className="clip-transcript">
          <div className="ct-head">
            <span className="ct-title">Transcript</span>
            <span className="ct-right">
              {transcript?.language && <span className="ct-lang">{transcript.language}</span>}
              {transcriptText && (
                <button type="button" className="ct-copy" title="Copy transcript to clipboard"
                  aria-label="Copy transcript" onClick={copyTranscript}>
                  <Icon n={copied ? 'check' : 'copy'} />{copied ? 'Copied' : 'Copy'}
                </button>
              )}
            </span>
          </div>
          {txLoading && <div className="eo-d ct-hint">Loading transcript…</div>}
          {txErr && <div className="eo-d ct-hint">Transcript unavailable for this clip.</div>}
          {transcript && transcript.segments && transcript.segments.length === 0 &&
            <div className="eo-d ct-hint">No transcript for this clip range.</div>}
          {transcript && transcript.segments && transcript.segments.length > 0 && (
            <div className="trim-list">
              {transcript.segments.map((s) => (
                <div key={s.index} className="trim-seg" title={s.text} style={{ cursor: 'default' }}>
                  <span className="trim-txt">{s.text}</span>
                  <span className="trim-time">{s.start.toFixed(1)}s</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {showCaption && (
        <div className="clip-caption">
          <div className="ct-head">
            <span className="ct-title">Caption</span>
            <span className="cc-hint">used on publish</span>
          </div>
          <textarea className="cc-ta" rows="2" maxLength={2200}
            value={captionDraft}
            placeholder={`Caption for this clip${clip.tiktok_caption ? ' — Gemini suggested one' : ''}…`}
            aria-label={`Caption for ${title}`}
            onChange={(e) => { setCaptionDraft(e.target.value); onUpdate(index, { caption: e.target.value, captionTouched: true }); }} />
        </div>
      )}
    </article>
  );
});

function TwoStageAnalysis({ report }) {
  if (!report) return null;
  const models = report.models || {};
  const discovery = report.discovery || {};
  const consolidation = discovery.consolidation || {};
  const review = report.review || {};
  const totals = report.totals || {};
  const used = report.models_used || {};
  const selected = report.selections || [];
  const rejected = report.rejected || [];
  return (
    <details className="two-stage-analysis" style={{
      margin: '10px 0 18px', padding: '12px 14px', border: '1px solid var(--line-1)',
      borderRadius: 'var(--r-md)', background: 'var(--bg-2)', fontSize: 13, color: 'var(--fg-2)',
    }}>
      <summary style={{ cursor: 'pointer', color: 'var(--fg-1)' }}>
        {report.reviewed_empty
          ? 'Two-stage review: no clips qualified'
          : `Two-stage selection · ${selected.length} kept from ${consolidation.final ?? review.evaluated ?? 0} candidates`}
      </summary>
      <div style={{ marginTop: 10, lineHeight: 1.6 }}>
        <div><strong>Discovery:</strong> {models.discovery || '—'} · {consolidation.input ?? 0} proposed → {consolidation.final ?? 0} reviewed ({consolidation.duplicates_removed ?? 0} duplicates removed)</div>
        {used.discovery?.length && used.discovery[0] !== models.discovery && (
          <div style={{ color: 'var(--warn, #f5a623)' }}>
            Discovery used <strong>{used.discovery.join(', ')}</strong> instead of {models.discovery}.
          </div>
        )}
        <div><strong>Review:</strong> {models.review || '—'} · {review.evaluated ?? 0} evaluated · {review.selected ?? 0} kept · {review.rejected ?? 0} rejected</div>
        {used.review?.length && used.review[0] !== models.review && (
          <div style={{ color: 'var(--warn, #f5a623)' }}>
            Review fell back to <strong>{used.review.join(', ')}</strong> because {models.review} was unavailable on this plan.
          </div>
        )}
        {models.copy && <div><strong>Copy:</strong> {models.copy}{report.copy?.fallback ? ' (grounded fallback)' : ''}</div>}
        {totals.input_tokens != null && (
          <div><strong>Usage:</strong> {totals.input_tokens} in / {totals.output_tokens} out tokens · {totals.latency_s ?? 0}s · cost {totals.pricing_known ? `$${totals.cost_usd}` : 'unknown (pricing not published)'}</div>
        )}
        {!!selected.length && (
          <ol style={{ margin: '8px 0 0 18px' }}>
            {selected.map((item) => (
              <li key={item.candidate_id} style={{ marginBottom: 4 }}>
                <strong>#{item.rank} · {Number(item.quality_score).toFixed(1)}/10</strong>{' '}
                [{Number(item.start).toFixed(1)}–{Number(item.end).toFixed(1)}s] · {item.category}
                <div style={{ color: 'var(--fg-3)' }}>{item.reason}</div>
              </li>
            ))}
          </ol>
        )}
        {!selected.length && rejected.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <strong>Rejected:</strong>{' '}
            {Object.entries(rejected.reduce((acc, item) => {
              const reason = item.reason || 'rejected';
              acc[reason] = (acc[reason] || 0) + 1;
              return acc;
            }, {})).map(([reason, count]) => `${count}× ${reason}`).join(' · ')}
          </div>
        )}
      </div>
    </details>
  );
}

export function ResultsView({ clips, jobId, preselections, sourceBanner, clipStates = {}, onUpdateClipState,
  doneIn, onBack, onPublish, onPublishAll, onEdit, onApplyToAll, onEditSelected, embedded, pushToast,
  analysis }) {
  const [selectMode, setSelectMode] = useState(false);
  const [aiCaptionsOpen, setAiCaptionsOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const visible = useMemo(() => clips.map((clip, index) => ({ c: clip, i: index })).filter(({ i }) => !clipStates[i]?.deleted), [clips, clipStates]);
  const selected = useMemo(() => visible.filter(({ i }) => clipStates[i]?.selected !== false), [visible, clipStates]);
  const topScore = useMemo(() => visible.length ? Math.max(...visible.map(({ c }) => Math.round(c.viral_score || 0))) : 0, [visible]);
  const allSelected = visible.length > 0 && selected.length === visible.length;

  // The Create-tab recipe is a compose-time default. Pre-compose each clip once
  // so the results preview actually reflects the chosen subtitles/hook/logo etc.
  // The raw clip stays on disk; export/publish still compose from the same
  // params, and a per-clip state means this never runs twice for the same job.
  const composingRef = useRef(new Set());
  const attemptedRef = useRef(new Set());
  const [composeTick, setComposeTick] = useState(0);
  useEffect(() => {
    composingRef.current = new Set();
    attemptedRef.current = new Set();
  }, [jobId]);
  useEffect(() => {
    if (!jobId) return;
    const recipeToggles = seedToggles(preselections);
    for (const { c: clip, i } of visible) {
      const state = clipStates[i] || {};
      // The server already has a composed file for this clip. With no
      // per-clip edit state, that file IS the recipe output: show it instead
      // of re-composing every clip on every fresh page load. If a per-clip
      // edit exists but its preview URL is gone, regenerate that edit below.
      if (clip.composed_url && (!state.toggles || state.previewUrl)) continue;
      const toggles = state.toggles || recipeToggles;
      if (!Object.values(toggles).some(Boolean)) continue;
      const key = `${jobId}:${i}`;
      if (attemptedRef.current.has(key) || composingRef.current.has(key)) continue;
      // Two at a time: a 20-clip job must not launch 20 ffmpeg composes at
      // once. State updates below re-run this effect and pick up the rest.
      if (composingRef.current.size >= 2) break;
      attemptedRef.current.add(key);
      composingRef.current.add(key);
      const apiIndex = clip.original_index ?? i;
      // Reuse the clip's own saved params when it has them, so regenerating a
      // missing preview never replaces a per-clip edit with the Create recipe.
      const hookParams = toggles.hook ? (state.hookParams || seedHookParams(clip, preselections)) : {};
      const subtitleParams = toggles.subtitles ? (state.subtitleParams || seedSubtitleParams(preselections)) : {};
      const logoParams = toggles.logo ? (state.logoParams || seedLogoParams(preselections)) : {};
      const gradeParams = toggles.grade ? (state.gradeParams || seedGradeParams(preselections)) : {};
      const bannerParams = toggles.banner ? (state.bannerParams || seedBannerParams(preselections, sourceBanner)) : {};
      const topTextParams = toggles.toptext ? (state.topTextParams || seedTopTextParams(preselections)) : {};
      const dropRanges = toggles.smartcut ? (state.dropRanges || []) : [];
      const apiParams = {
        toggles,
        hook_params: hookParams,
        subtitle_params: subtitleParams,
        logo_params: logoParams,
        grade_params: gradeParams,
        banner_params: bannerParams,
        top_text_params: topTextParams,
        drop_ranges: dropRanges,
      };
      const clipState = {
        toggles, hookParams, subtitleParams, logoParams, gradeParams,
        bannerParams, topTextParams, dropRanges,
      };
      onUpdateClipState(i, { processing: true, processingSince: Date.now() });
      composeClip(jobId, apiIndex, apiParams)
        .then(({ composed_url }) => onUpdateClipState(i, {
          ...clipState, processing: false, processingSince: null, composeError: false,
          previewUrl: composed_url, previewBust: Date.now(),
        }))
        .catch((err) => {
          // Keep the attempted params so a later reload retries them; the
          // preview check above (not `toggles`) is what prevents a stuck loop.
          onUpdateClipState(i, { processing: false, processingSince: null, composeError: true });
          pushToast?.('warn', `Clip ${i + 1}: could not apply the recipe — ${
            String(err?.message || err).slice(0, 90)
          }`);
        })
        .finally(() => {
          composingRef.current.delete(key);
          setComposeTick((value) => value + 1);
        });
    }
  }, [jobId, preselections, sourceBanner, visible, clipStates, onUpdateClipState, pushToast, composeTick]);

  const setSelectedAll = useCallback((value) => visible.forEach(({ i }) => onUpdateClipState(i, { selected: value })), [visible, onUpdateClipState]);
  const publishMany = useCallback((list) => onPublishAll(list.map(({ c, i }) => ({ ...c, _idx: i, _apiIdx: c.original_index ?? i }))), [onPublishAll]);
  const exportMany = async (list) => {
    if (exporting || !list.length) return;
    setExporting(true);
    let composed = 0;
    let rawFallback = 0;
    try {
      for (const { c, i } of list) {
        try { await exportClip(jobId, i, c, clipStates[i], preselections); composed += 1; }
        catch { downloadClip(c, i); rawFallback += 1; }
        await delay(150);
      }
      pushToast?.(rawFallback ? 'warn' : 'success', rawFallback
        ? `Exported ${list.length} clips; ${rawFallback} used the raw fallback`
        : `Exported ${composed}/${list.length} clips`);
    } finally { setExporting(false); }
  };

  return (
    <main className="container fade-in">
      <div className="results-head">
        {!embedded && <Btn variant="icon" icon="arrow-left" onClick={onBack} title="Start over" aria-label="Start over" />}
        <h2>{visible.length} clips ready</h2>
        {doneIn && <Badge tone="teal" icon="check">done in {doneIn}</Badge>}
        <div className="rh-right">
          <Btn variant="secondary" size="sm" icon={selectMode ? 'x' : 'check-square'} onClick={() => setSelectMode((current) => {
            if (!current) visible.forEach(({ i }) => onUpdateClipState(i, { selected: false }));
            return !current;
          })}>{selectMode ? 'Cancel' : 'Select'}</Btn>
          {!selectMode && <Btn variant="secondary" size="sm" icon="sparkles"
            aria-expanded={aiCaptionsOpen} onClick={() => setAiCaptionsOpen((o) => !o)}>
            AI captions</Btn>}
          {!selectMode && <Btn variant="secondary" size="sm" icon="download" loading={exporting} disabled={!visible.length} onClick={() => exportMany(visible)}>{exporting ? 'Exporting…' : 'Export all'}</Btn>}
          {!selectMode && <Btn variant="grad" size="sm" icon="send" disabled={!visible.length} onClick={() => publishMany(visible)}>Publish all</Btn>}
        </div>
      </div>
      <div className="results-sub">Sorted by virality score · top moment {topScore}</div>
      <TwoStageAnalysis report={analysis} />

      {aiCaptionsOpen && !selectMode && (
        <AiCaptionPanel jobId={jobId} clips={clips} clipStates={clipStates}
          onUpdateClipState={onUpdateClipState} pushToast={pushToast} />
      )}

      {selectMode && <div className="actionbar" role="toolbar" aria-label="Selected clip actions">
        <span className="sel-n" aria-live="polite">{selected.length} selected</span>
        <Btn variant="ghost" size="sm" icon="check-check" onClick={() => setSelectedAll(!allSelected)}>{allSelected ? 'Deselect all' : 'Select all'}</Btn>
        <div className="ab-right">
          <Btn variant="secondary" size="sm" icon="sliders-horizontal" disabled={!selected.length} onClick={() => onEditSelected(selected)}>Edit {selected.length || ''}</Btn>
          <Btn variant="secondary" size="sm" icon="download" loading={exporting} disabled={!selected.length} onClick={() => exportMany(selected)}>{exporting ? 'Exporting…' : 'Export'}</Btn>
          <Btn variant="grad" size="sm" icon="send" disabled={!selected.length} onClick={() => publishMany(selected)}>Publish {selected.length || ''}</Btn>
        </div>
      </div>}

      {visible.length ? <div className="results-grid" role="list" aria-label="Generated clips">
        {visible.map(({ c, i }) => <ClipCard key={c.original_index ?? i} clip={c} index={i} jobId={jobId} state={clipStates[i]}
          preselections={preselections} onUpdate={onUpdateClipState} selectMode={selectMode} onPublish={onPublish}
          onEdit={onEdit} onApplyToAll={onApplyToAll} pushToast={pushToast} />)}
      </div> : <div className="empty" role="status"><div className="ei"><Icon n="film" /></div><h3>No visible clips</h3><p>All clips were removed from this view. Start over to generate a new set.</p></div>}
    </main>
  );
}
