// AI hook-text rewrite state for EditClipModal (Hook tab). The pipeline writes
// hooks in the transcript's language, so a mixed-language video can produce
// hooks in the wrong language; this lets the user regenerate the per-clip hook
// in a target language without typing it. State stays LIFTED in the modal via
// this hook — HookTab is a dumb component and tabs are conditionally rendered.
import { useState } from 'react';
import { rewriteHookText } from '../redesign/realApi';
import { LANGUAGES } from '../redesign/data';

const LANGUAGE_NAME = new Map(LANGUAGES);

export function useHookRewrite({ jobId, idx, apiKey }) {
  // 'multi' is meaningless as a hook target — default English.
  const [language, setLanguage] = useState('en');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const rewrite = async (onText) => {
    if (busy || !jobId) return;
    setBusy(true); setMsg('');
    try {
      const { hook_text = '', error = '' } = await rewriteHookText(jobId, idx, { language }, apiKey);
      if (hook_text) {
        onText(hook_text);
        setMsg(`Hook rewritten in ${LANGUAGE_NAME.get(language) || language}.`);
      } else {
        setMsg(error || 'Rewrite failed.');
      }
    } catch (e) {
      setMsg(e.message || 'Rewrite failed.');
    } finally {
      setBusy(false);
    }
  };

  return { language, setLanguage, busy, msg, rewrite };
}
