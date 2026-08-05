// AI captions — the expandable panel above the results grid. The user types a
// short "context" for the series of clips (tone, channel, CTA …); it is sent
// with each clip's own transcript to a user-configured OpenAI-compatible API
// which returns an optimized publish caption per clip. Generated captions fill
// the per-clip caption fields (the clip-state `captionTouched` flag marks
// hand-written ones so they are never overwritten), and stay fully editable.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Btn } from './primitives';
import { getConfig, optimizeCaptions } from './realApi';

export function AiCaptionPanel({ jobId, clips, clipStates = {}, onUpdateClipState, pushToast }) {
  const [context, setContext] = useState('');
  const [configured, setConfigured] = useState(null); // null = unknown yet
  const [model, setModel] = useState('');
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    let alive = true;
    getConfig()
      .then((c) => {
        if (!alive || !c) return;
        setConfigured(!!(c.OPENAI_CAPTIONS_API_KEY && c.OPENAI_CAPTIONS_BASE_URL));
        setModel(c.OPENAI_CAPTIONS_MODEL || '');
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  // Clips the user hasn't hand-written a caption for. `captionTouched` is set
  // true the moment anyone edits a caption (grid editor or publish modal), so
  // a regenerated batch never clobbers their words.
  const eligible = useMemo(
    () => clips
      .map((c, i) => ({ c, i }))
      .filter(({ i }) => !clipStates[i]?.deleted && !clipStates[i]?.captionTouched),
    [clips, clipStates],
  );

  const generate = useCallback(async () => {
    if (!eligible.length || generating) return;
    setGenerating(true);
    try {
      const res = await optimizeCaptions(jobId, {
        context: context.trim(),
        indices: eligible.map(({ c, i }) => c.original_index ?? i),
      });
      let applied = 0;
      // Response indices are ABSOLUTE shorts positions; map back to the grid's
      // array position (they diverge after delete-after-publish gaps).
      (res.captions || []).forEach(({ index, caption }) => {
        if (!caption) return;
        const entry = eligible.find(({ c, i }) => (c.original_index ?? i) === index);
        if (!entry) return;
        onUpdateClipState(entry.i, { caption, captionTouched: false });
        applied += 1;
      });
      pushToast?.(
        applied === eligible.length ? 'success' : 'warn',
        applied === eligible.length
          ? `Generated captions for ${applied} clip${applied === 1 ? '' : 's'}`
          : `Generated ${applied}/${eligible.length} captions`,
      );
    } catch (e) {
      pushToast?.('error', String(e?.message || e).slice(0, 80));
    } finally {
      setGenerating(false);
    }
  }, [eligible, generating, context, jobId, onUpdateClipState, pushToast]);

  return (
    <div className="ai-captions">
      <div className="ct-head">
        <span className="ct-title">AI captions</span>
        <span className="cc-hint">
          {configured === null
            ? 'checking config…'
            : configured
              ? model
                ? `model: ${model}`
                : 'configured'
              : 'configure a model in Settings → AI captions'}
        </span>
      </div>
      <textarea className="ta" rows="3" maxLength={2000} value={context}
        aria-label="AI caption context"
        placeholder="Context for this series of clips — e.g. “Fitness channel, host is Coach Mike, hype tone, end with a call to action.”"
        onChange={(e) => setContext(e.target.value)} />
      <div className="ai-captions-foot">
        <span className="cc-hint">
          {eligible.length} eligible clip{eligible.length === 1 ? '' : 's'} · hand-written captions are never overwritten
        </span>
        <Btn variant="grad" size="sm" icon="sparkles" loading={generating}
          disabled={configured !== true || !eligible.length} onClick={generate}>
          {generating ? 'Generating…' : 'Generate captions'}
        </Btn>
      </div>
    </div>
  );
}
