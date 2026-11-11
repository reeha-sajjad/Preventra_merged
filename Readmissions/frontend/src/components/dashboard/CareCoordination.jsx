import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getCareActions, assignCoordinator, addCareNote } from '../../api';
import { UserCog, MessageSquarePlus, Loader2, AlertCircle, PencilLine } from 'lucide-react';
import { can, ASSIGN_ROLES, NOTE_ROLES, EDIT_PATIENT_ROLES } from '../../roles';

/**
 * The care coordinator and the care notes. Everyone who reaches this panel
 * reads them; only the roles the server lets change them (api/access.py
 * ACTIONS assign, add_notes, edit_patients) get the boxes and buttons, so an
 * insurer or pharmacy is never offered a save that will be refused.
 */
export default function CareCoordination({ patientId }) {
  const navigate = useNavigate();
  const canAssign = can(ASSIGN_ROLES);
  const canNote = can(NOTE_ROLES);
  const canEdit = can(EDIT_PATIENT_ROLES);
  const [careActions, setCareActions] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [coordinatorInput, setCoordinatorInput] = useState('');
  const [assigning, setAssigning] = useState(false);

  const [noteInput, setNoteInput] = useState('');
  const [addingNote, setAddingNote] = useState(false);

  const load = () => {
    setLoading(true);
    setError(null);
    getCareActions(patientId)
      .then((data) => {
        setCareActions(data);
        setCoordinatorInput(data.coordinator_name || '');
        setLoading(false);
      })
      .catch((err) => { setError(err.message); setLoading(false); });
  };

  useEffect(() => { load(); }, [patientId]);

  const handleAssign = async (e) => {
    e.preventDefault();
    if (!coordinatorInput.trim()) return;
    setAssigning(true);
    try {
      await assignCoordinator(patientId, coordinatorInput.trim());
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setAssigning(false);
    }
  };

  const handleAddNote = async (e) => {
    e.preventDefault();
    if (!noteInput.trim()) return;
    setAddingNote(true);
    try {
      await addCareNote(patientId, noteInput.trim());
      setNoteInput('');
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setAddingNote(false);
    }
  };

  if (loading) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 animate-pulse space-y-3">
        <div className="h-4 bg-gray-200 rounded w-40" />
        <div className="h-9 bg-gray-100 rounded w-full" />
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 space-y-5">
      <div className="flex items-center justify-between border-b pb-3">
        <h2 className="text-lg font-semibold text-ns-navy flex items-center space-x-2">
          <UserCog className="text-gray-400" size={20} />
          <span>Care Coordination</span>
        </h2>
        {canEdit && (
        <button
          type="button"
          onClick={() => navigate(`/patients/${patientId}/update`)}
          className="flex items-center space-x-1.5 px-3 py-1.5 text-sm font-medium text-ns-navy border border-ns-navy/30 rounded-lg hover:bg-ns-navy/5 transition-colors"
        >
          <PencilLine size={15} />
          <span>Update Patient</span>
        </button>
        )}
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 flex items-start space-x-2 text-red-800 text-sm">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Assign coordinator - read-only for the roles that do not assign */}
      {canAssign ? (
      <form onSubmit={handleAssign} className="flex items-end gap-3">
        <div className="flex-1">
          <label className="block text-sm font-medium text-gray-700 mb-1.5">Assigned Coordinator</label>
          <input
            type="text"
            value={coordinatorInput}
            onChange={(e) => setCoordinatorInput(e.target.value)}
            placeholder="e.g. Jane Doe, RN"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-ns-navy/30 focus:border-ns-navy transition-colors"
          />
        </div>
        <button
          type="submit"
          disabled={assigning || !coordinatorInput.trim()}
          className="px-4 py-2 bg-ns-navy text-white rounded-lg text-sm font-semibold hover:bg-blue-900 transition-colors disabled:opacity-60 disabled:cursor-not-allowed flex items-center space-x-2 shrink-0"
        >
          {assigning ? <Loader2 size={16} className="animate-spin" /> : <span>Assign</span>}
        </button>
      </form>
      ) : (
        <div>
          <div className="text-sm font-medium text-gray-700 mb-1.5">Assigned Coordinator</div>
          <div className="text-sm text-gray-800">
            {careActions?.coordinator_name || <span className="text-gray-400">None assigned yet</span>}
          </div>
        </div>
      )}
      {careActions?.assigned_at && (
        <p className="text-xs text-gray-400 -mt-3">Last assigned: {careActions.assigned_at}</p>
      )}

      {/* Notes timeline */}
      <div>
        <h3 className="text-sm font-semibold text-gray-700 mb-2">Notes Timeline</h3>
        <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
          {(careActions?.notes || []).length === 0 && (
            <p className="text-sm text-gray-400 italic">No notes yet.</p>
          )}
          {[...(careActions?.notes || [])].reverse().map((note, idx) => (
            <div key={idx} className="bg-gray-50 border border-gray-100 rounded-lg p-3">
              <p className="text-sm text-gray-800">{note.text}</p>
              <p className="text-xs text-gray-400 mt-1">{note.author} — {note.created_at}</p>
            </div>
          ))}
        </div>

        {canNote && (
        <form onSubmit={handleAddNote} className="mt-3 flex items-end gap-3">
          <div className="flex-1">
            <textarea
              rows={2}
              value={noteInput}
              onChange={(e) => setNoteInput(e.target.value)}
              placeholder="Add a care note..."
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-ns-navy/30 focus:border-ns-navy transition-colors resize-none"
            />
          </div>
          <button
            type="submit"
            disabled={addingNote || !noteInput.trim()}
            className="px-4 py-2 bg-ns-navy text-white rounded-lg text-sm font-semibold hover:bg-blue-900 transition-colors disabled:opacity-60 disabled:cursor-not-allowed flex items-center space-x-2 shrink-0"
          >
            {addingNote ? <Loader2 size={16} className="animate-spin" /> : <MessageSquarePlus size={16} />}
          </button>
        </form>
        )}
      </div>
    </div>
  );
}