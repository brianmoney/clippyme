
import { useCallback, useEffect, useRef, useState } from 'react';
import { getApiUrl } from '../config';
import { apiFetch } from '../lib/apiToken';

export function useBackendStatus() {
  const [hfTokenSet, setHfTokenSet] = useState(true);
  const [cookiesConfigured, setCookiesConfigured] = useState(false);
  // Backend-resolved LOCAL_INPUT_DIR ('' until known) — drives the Create tab's
  // Server-source placeholder so the hint matches the deployed allow-list root.
  const [localInputDir, setLocalInputDir] = useState('');
  const [state, setState] = useState({ loading: true, reachable: true, error: null, updatedAt: null });
  const controllerRef = useRef(null);

  const refresh = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setState((current) => ({ ...current, loading: true }));
    try {
      const [configRes, cookiesRes, localInputRes] = await Promise.all([
        apiFetch(getApiUrl('/api/config'), { signal: controller.signal }),
        apiFetch(getApiUrl('/api/config/cookies/status'), { signal: controller.signal }),
        apiFetch(getApiUrl('/api/config/local-input'), { signal: controller.signal }),
      ]);
      if (!configRes.ok || !cookiesRes.ok) throw new Error('Backend status request failed');
      const [config, cookies, localInput] = await Promise.all([
        configRes.json(),
        cookiesRes.json(),
        localInputRes.ok ? localInputRes.json() : Promise.resolve(null),
      ]);
      setHfTokenSet(!!config.HF_TOKEN);
      setCookiesConfigured(!!cookies.configured);
      if (typeof localInput?.dir === 'string' && localInput.dir) setLocalInputDir(localInput.dir);
      setState({ loading: false, reachable: true, error: null, updatedAt: Date.now() });
    } catch (error) {
      if (error?.name === 'AbortError') return;
      setState({ loading: false, reachable: false, error, updatedAt: Date.now() });
    }
  }, []);

  useEffect(() => {
    refresh();
    return () => controllerRef.current?.abort();
  }, [refresh]);

  return { hfTokenSet, setHfTokenSet, cookiesConfigured, setCookiesConfigured, localInputDir, ...state, refresh };
}
