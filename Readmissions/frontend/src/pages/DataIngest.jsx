import React, { useCallback, useEffect, useState } from 'react';
import { Database, Upload, Columns3, PlugZap, Activity } from 'lucide-react';
import { getIngestProfile } from '../api';
import RescoreCard from '../components/data/RescoreCard';
import UploadPanel from '../components/data/UploadPanel';
import MappingPanel from '../components/data/MappingPanel';
import ConnectionsPanel from '../components/data/ConnectionsPanel';
import ActivityPanel from '../components/data/ActivityPanel';
import { Notice } from '../components/data/dataParts';

const TABS = [
  { key: 'upload', label: 'Upload', icon: <Upload size={16} /> },
  { key: 'mapping', label: 'Mapping', icon: <Columns3 size={16} /> },
  { key: 'connections', label: 'Connections', icon: <PlugZap size={16} /> },
  { key: 'activity', label: 'Activity', icon: <Activity size={16} /> },
];

/**
 * Getting a hospital's patients in. Every way in - an upload here, the
 * hospital's own systems through the ingest API, or a nightly read of its
 * database - goes through the same mapping and checks, and lands on the
 * worklist scored by the hospital's deployed model (api/ingest.py,
 * api/ingest_connections.py, docs/pilot_ingestion_build.md).
 */
export default function DataIngest() {
  const [tab, setTab] = useState('upload');
  const [prefix, setPrefix] = useState(null);
  const [error, setError] = useState(null);
  // Columns of the last file seen per table, so the Mapping tab can offer them.
  const [seen, setSeen] = useState({});
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let live = true;
    getIngestProfile().then((p) => { if (live) { setPrefix(p.profile.patient_prefix); setError(null); } })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [tab, reloadKey]);

  const onSeen = useCallback((table, headers, mapping) => {
    setSeen((s) => ({ ...s, [table]: { headers, mapping: mapping || s[table]?.mapping || {} } }));
  }, []);
  const goToMapping = () => setTab('mapping');
  const onUploaded = useCallback(() => setReloadKey((k) => k + 1), []);

  return (
    <div className="mx-auto max-w-7xl p-4 sm:p-6">
      <div className="mb-5">
        <h1 className="flex items-center gap-2 text-2xl font-bold text-ns-navy"><Database size={26} /> Data</h1>
        <p className="mt-1 text-sm text-gray-500">
          Bring your hospital's discharges and follow-up check-ins in: upload files, let your systems send them, or
          have Preventra read your database every night. They are scored by your deployed model and appear on the
          worklist.
        </p>
      </div>

      {error ? (
        <Notice tone={error.includes('hospital') ? 'amber' : 'red'}>
          {error.includes('Choose a hospital') ? 'Pick a hospital with the hospital picker at the top first.' : error}
        </Notice>
      ) : (
        <>
          <div className="mb-5"><RescoreCard reloadKey={reloadKey} /></div>

          <div className="mb-5 flex gap-1 overflow-x-auto border-b border-gray-200">
            {TABS.map((t) => (
              <button key={t.key} type="button" onClick={() => setTab(t.key)}
                className={`-mb-px inline-flex items-center gap-2 whitespace-nowrap border-b-2 px-4 py-2 text-sm font-medium transition-colors ${tab === t.key
                  ? 'border-ns-navy text-ns-navy' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
                {t.icon} {t.label}
              </button>
            ))}
          </div>

          {tab === 'upload' && (
            <UploadPanel prefix={prefix} onHeaders={onSeen} onUploaded={onUploaded} />
          )}
          {tab === 'mapping' && <MappingPanel seen={seen} onSeen={onSeen} />}
          {tab === 'connections' && <ConnectionsPanel prefix={prefix} onGoToMapping={goToMapping} />}
          {tab === 'activity' && <ActivityPanel reloadKey={reloadKey} />}
        </>
      )}
    </div>
  );
}
