import { useEffect, useState } from 'react';
import { getAiStatus } from '../api';

// Which AI features are on (api/ai_settings.py /status), fetched once per page
// load and shared: the chatbot, the patient-page explanations and Model Studio
// all ask. A failed request reads as "on", so a network hiccup never hides a
// feature that works; the feature's own request then says what is wrong.
// Saving Settings -> AI refreshes it, and every component showing it updates.
let shared = null;
const listeners = new Set();

export function refreshAiStatus() {
  shared = getAiStatus().catch(() => null);
  shared.then((s) => listeners.forEach((set) => set(s)));
  return shared;
}

/** {chatbot, insights, studio, data}: each {on}; null while loading. */
export function useAiStatus() {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    let live = true;
    const set = (s) => { if (live) setStatus(s); };
    listeners.add(set);
    (shared || refreshAiStatus()).then(set);
    return () => { live = false; listeners.delete(set); };
  }, []);
  return status;
}

/** True unless the status says the feature is off. */
export const aiOn = (status, feature) => (status ? status[feature]?.on !== false : true);
