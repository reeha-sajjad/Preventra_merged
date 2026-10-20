import React, { useCallback, useEffect, useState } from 'react';
import { Bot, Loader2, Save, PlugZap, CheckCircle2, XCircle, RefreshCw, ShieldAlert } from 'lucide-react';
import { getAiSettings, saveAiSettings, listAiModels, testAiModel } from '../../api';
import { refreshAiStatus } from '../../hooks/useAiStatus';

// Settings -> AI, for the superadmin (api/ai_settings.py): which provider and
// model serves each AI feature, the keys (written, never read back), whether
// a provider may receive patient details, and one switch for all of it.

const KEYED = ['gemini', 'anthropic', 'openai'];
const card = 'rounded-xl border border-gray-200 bg-white p-6';
const input = 'w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-ns-navy focus:outline-none';
const small = 'rounded-md border border-gray-300 px-2 py-1.5 text-sm focus:border-ns-navy focus:outline-none';
const button = 'inline-flex items-center gap-1.5 rounded-md bg-ns-navy px-3 py-2 text-sm font-semibold text-white hover:bg-ns-navy/90 disabled:opacity-50';
const ghost = 'inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50';

export default function AiSettings() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let live = true;
    getAiSettings().then((d) => { if (live) { setData(d); setError(null); } })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [reloadKey]);

  if (error) return <section className={card}><p className="text-sm text-red-700">{error}</p></section>;
  if (!data) return null;
  // Remounted after each save, so the form starts from what is stored.
  return <AiForm key={`${reloadKey}:${data.updated_at || ''}`} data={data} onSaved={() => { refreshAiStatus(); setReloadKey((k) => k + 1); }} />;
}

function AiForm({ data, onSaved }) {
  const [enabled, setEnabled] = useState(data.enabled);
  const [providers, setProviders] = useState(() => Object.fromEntries(Object.entries(data.providers).map(([k, p]) => [
    k, { api_key: '', base_url: p.base_url || '', patient_data: p.patient_data, clear_key: false }])));
  const [features, setFeatures] = useState(() => Object.fromEntries(Object.entries(data.features).map(([k, f]) => [
    k, { provider: f.chosen ? f.provider : '', model: f.chosen ? f.model : '' }])));
  const [models, setModels] = useState({});          // provider -> {list, loading, error}
  const [tests, setTests] = useState({});            // feature -> result
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const setP = (name, change) => setProviders((all) => ({ ...all, [name]: { ...all[name], ...change } }));
  const setF = (name, change) => setFeatures((all) => ({ ...all, [name]: { ...all[name], ...change } }));

  const loadModels = useCallback(async (provider) => {
    if (!provider) return;
    setModels((m) => ({ ...m, [provider]: { ...(m[provider] || {}), loading: true, error: null } }));
    try {
      const p = providers[provider];
      const r = await listAiModels(provider, p.api_key, p.base_url);
      setModels((m) => ({ ...m, [provider]: { list: r.models, loading: false } }));
    } catch (e) {
      setModels((m) => ({ ...m, [provider]: { list: [], loading: false, error: e.message } }));
    }
  }, [providers]);

  const test = async (feature) => {
    const f = features[feature];
    const p = providers[f.provider] || {};
    setTests((t) => ({ ...t, [feature]: { loading: true } }));
    try {
      const r = await testAiModel(f.provider, f.model, p.api_key, p.base_url);
      setTests((t) => ({ ...t, [feature]: r }));
    } catch (e) {
      setTests((t) => ({ ...t, [feature]: { ok: false, error: e.message } }));
    }
  };

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      await saveAiSettings({
        enabled,
        providers: Object.fromEntries(Object.entries(providers).map(([k, p]) => [k, {
          ...(p.api_key ? { api_key: p.api_key } : {}), clear_key: p.clear_key,
          base_url: p.base_url, patient_data: p.patient_data }])),
        features: Object.fromEntries(Object.entries(features).map(([k, f]) => [
          k, f.provider && f.model ? { provider: f.provider, model: f.model } : null])),
      });
      onSaved();
    } catch (e) { setMsg(e.message); }
    finally { setBusy(false); }
  };

  return (
    <section className={card}>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-ns-navy/10 text-ns-navy"><Bot size={17} /></div>
          <div>
            <h2 className="font-semibold text-gray-800">AI models</h2>
            <p className="text-xs text-gray-500">Which model serves each AI feature. Gemini, Claude, OpenAI, or a local model on your own server.</p>
          </div>
        </div>
        <label className="flex items-center gap-2 text-sm font-medium text-gray-700">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          AI features on
        </label>
      </div>

      {!data.key_set && (
        <p className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          The server has no INGEST_SECRET_KEY, so keys cannot be saved here. Keys given to the server as environment
          variables (GEMINI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, OLLAMA_BASE_URL) still work.
        </p>
      )}

      <h3 className="mb-2 text-sm font-semibold text-gray-700">Providers</h3>
      <div className="grid gap-3 md:grid-cols-2">
        {Object.entries(data.providers).map(([name, p]) => (
          <div key={name} className="rounded-lg border border-gray-200 p-3">
            <div className="flex items-center justify-between">
              <span className="font-medium text-gray-800">{p.label}</span>
              <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${p.configured ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                {p.configured ? (p.source === 'environment' ? 'set on the server' : 'saved here') : 'not set'}
              </span>
            </div>
            {KEYED.includes(name) && (
              <div className="mt-2 flex gap-2">
                <input type="password" className={input} autoComplete="off" value={providers[name].api_key}
                       placeholder={p.configured ? 'Key set; paste a new one to replace it' : 'Paste an API key'}
                       onChange={(e) => setP(name, { api_key: e.target.value, clear_key: false })} />
                {p.source === 'settings' && (
                  <button type="button" className={ghost} title="Remove the saved key"
                          onClick={() => setP(name, { clear_key: true, api_key: '' })}>
                    {providers[name].clear_key ? 'Will remove' : 'Remove'}
                  </button>
                )}
              </div>
            )}
            {(name === 'ollama' || name === 'openai') && (
              <input className={`${input} mt-2`} value={providers[name].base_url}
                     placeholder={name === 'ollama' ? data.default_ollama_url : 'Optional: another OpenAI-compatible address'}
                     onChange={(e) => setP(name, { base_url: e.target.value })} />
            )}
            <label className="mt-2 flex items-start gap-2 text-xs text-gray-600">
              <input type="checkbox" className="mt-0.5" checked={providers[name].patient_data}
                     onChange={(e) => setP(name, { patient_data: e.target.checked })} />
              <span>
                <strong>May receive patient details</strong> (chatbot, patient-page explanations).{' '}
                {p.local ? 'The model runs on your own server.' : 'Only with a data-protection agreement with this provider.'}
              </span>
            </label>
          </div>
        ))}
      </div>

      <h3 className="mb-2 mt-6 text-sm font-semibold text-gray-700">Features</h3>
      <div className="space-y-3">
        {Object.entries(data.features).map(([name, f]) => {
          const pick = features[name];
          const list = models[pick.provider];
          const result = tests[name];
          return (
            <div key={name} className="rounded-lg border border-gray-200 p-3">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="font-medium text-gray-800">{f.label}</span>
                {f.sends_patient_data
                  ? <span className="inline-flex items-center gap-1 text-[11px] text-amber-700"><ShieldAlert size={12} /> sends patient details</span>
                  : <span className="text-[11px] text-gray-500">column summaries only</span>}
                <span className={`ml-auto rounded-full px-2 py-0.5 text-[11px] font-semibold ${f.on ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                  {f.on ? `on: ${f.model}` : `off: ${f.reason}`}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <select className={small} value={pick.provider} onChange={(e) => {
                  setF(name, { provider: e.target.value, model: '' });
                  if (e.target.value && !models[e.target.value]) loadModels(e.target.value);
                }}>
                  <option value="">Default (Gemini key on the server)</option>
                  {Object.entries(data.providers).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
                </select>
                {pick.provider && (
                  <>
                    <input className={`${small} min-w-[16rem]`} list={`models-${name}`} value={pick.model}
                           placeholder={list?.loading ? 'Loading models…' : 'Model'}
                           onChange={(e) => setF(name, { model: e.target.value })} />
                    <datalist id={`models-${name}`}>
                      {(list?.list || []).map((m) => <option key={m} value={m} />)}
                    </datalist>
                    <button type="button" className={ghost} onClick={() => loadModels(pick.provider)} title="Fetch the provider's models">
                      {list?.loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                    </button>
                    <button type="button" className={ghost} disabled={!pick.model || result?.loading} onClick={() => test(name)}>
                      {result?.loading ? <Loader2 size={13} className="animate-spin" /> : <PlugZap size={13} />} Test
                    </button>
                  </>
                )}
              </div>
              {list?.error && <p className="mt-1.5 text-xs text-red-700">{list.error}</p>}
              {result && !result.loading && (
                <p className={`mt-1.5 flex items-center gap-1.5 text-xs ${result.ok ? 'text-green-700' : 'text-red-700'}`}>
                  {result.ok ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
                  {result.ok
                    ? `Answered in ${result.seconds}s. ${result.tools_ok ? 'Can use tools.' : 'Did not use the tool'}${!result.tools_ok && f.needs_tools ? ' - the chatbot needs this; choose another model.' : !result.tools_ok ? '.' : ''}`
                    : result.error}
                </p>
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <button type="button" className={button} disabled={busy} onClick={save}>
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} Save
        </button>
        {data.updated_by && <span className="text-xs text-gray-500">Last changed by {data.updated_by}</span>}
        {msg && <span className="text-sm text-red-700">{msg}</span>}
      </div>
    </section>
  );
}
