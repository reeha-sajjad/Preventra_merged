import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { MessageCircle, X, Send, Loader2, Maximize2, Minimize2, Lightbulb, RotateCcw } from 'lucide-react';
import { askChatbot, getPatientGroups } from '../api';
import { can, myRole, CONSOLE_ROLES, REASON_ROLES, STAFF_ROLES } from '../roles';
import { buildMatchers, linkify } from './chatLinks';

const FRIENDLY_ERROR = "Sorry, I couldn't process that question. Please try again.";

// How much of the conversation to send back with each question. The backend
// trims to the same order of magnitude; keeping it short here means a long
// session does not grow the request without bound.
const HISTORY_TURNS = 8;

// Questions people actually ask, worded the way the query layer understands
// them. Per role, because the backend answers each role from its own layer
// (api/chatbot_service.py guard): a hospital admin or insurer offered a
// diagnosis question would only be told to open the patient first.
function commonQuestions() {
  const role = myRole();
  if (role === 'patient') {
    return [
      'What does my risk score mean?',
      'What is a hospital readmission?',
      'What can I do to lower my risk of readmission?',
      'What warning signs should I watch for after discharge?',
    ];
  }
  const clinical = !can(REASON_ROLES);
  return [
    'How many patients are in each risk band?',
    'Who are the 5 highest-risk patients right now?',
    'Which patients have risen in risk since discharge?',
    clinical && 'What are the most common conditions among high-risk patients?',
    clinical && 'What are the most common risk drivers?',
    clinical && 'What are the most common diagnoses among high-risk patients?',
    'How has risk trended over the last few weeks?',
    (can(STAFF_ROLES) || can(CONSOLE_ROLES)) && 'Which doctors have the most open alerts?',
    can(STAFF_ROLES) && 'Which doctors are registered and what do they cover?',
    clinical && 'What drives readmission after heart failure?',
  ].filter(Boolean);
}

function SuggestionList({ questions, onPick, disabled, grid }) {
  return (
    <div className={grid ? 'grid gap-2 sm:grid-cols-2' : 'flex flex-col gap-1.5'}>
      {questions.map((q) => (
        <button
          key={q}
          type="button"
          onClick={() => onPick(q)}
          disabled={disabled}
          className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-left text-sm text-gray-700 transition-colors hover:border-ns-navy/40 hover:bg-ns-navy/5 hover:text-ns-navy disabled:opacity-50"
        >
          {q}
        </button>
      ))}
    </div>
  );
}

function BotText({ text, entities, groups, onFollow }) {
  const segments = useMemo(() => linkify(text, buildMatchers({
    entities, groups, patientOnly: myRole() === 'patient',
  })), [text, entities, groups]);

  return segments.map((s, i) => (s.link ? (
    <Link
      key={i}
      to={s.link.to}
      title={s.link.title}
      onClick={(e) => {
        // A modified click opens a new tab; the chat here stays as it is.
        if (!(e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0)) onFollow();
      }}
      className="font-medium text-ns-navy underline decoration-ns-navy/30 underline-offset-2 hover:decoration-ns-navy"
    >
      {s.text}
    </Link>
  ) : <React.Fragment key={i}>{s.text}</React.Fragment>));
}

export default function Chatbot() {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [question, setQuestion] = useState('');
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [groups, setGroups] = useState([]);
  const historyRef = useRef(null);
  const inputRef = useRef(null);
  const questions = useMemo(commonQuestions, []);

  useEffect(() => {
    if (historyRef.current) {
      historyRef.current.scrollTop = historyRef.current.scrollHeight;
    }
  }, [messages, loading]);

  // The conditions in this batch, so a condition named in an answer links to
  // its worklist filter. Fetched once, the first time the chat opens.
  useEffect(() => {
    if (!open || groups.length || myRole() === 'patient') return;
    getPatientGroups().then((res) => setGroups(res?.groups || [])).catch(() => {});
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!expanded) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') setExpanded(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [expanded]);

  // Following a link should show the page it opens. Full screen steps back to
  // the side panel; on a phone the panel covers the page, so it closes.
  const afterFollow = () => {
    setExpanded(false);
    if (window.innerWidth < 640) setOpen(false);
  };

  const ask = async (text) => {
    const trimmed = (text || '').trim();
    if (!trimmed || loading) return;

    setQuestion('');
    setShowSuggestions(false);
    setLoading(true);
    // Snapshot the transcript before this question is appended: the backend
    // receives the question separately, and sending it twice reads as the user
    // having asked it already.
    const history = messages
      .filter((m) => !m.isError)
      .slice(-HISTORY_TURNS)
      .map(({ role, text: t }) => ({ role, text: t }));
    setMessages((prev) => [...prev, { role: 'user', text: trimmed }]);

    try {
      const { answer, entities } = await askChatbot(trimmed, history);
      setMessages((prev) => [...prev, { role: 'bot', text: answer || FRIENDLY_ERROR,
                                        entities: entities || [] }]);
    } catch {
      setMessages((prev) => [...prev, { role: 'bot', text: FRIENDLY_ERROR, isError: true }]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const handleSend = (e) => {
    e.preventDefault();
    ask(question);
  };

  const panel = expanded
    ? 'fixed inset-0 z-[70] flex flex-col overflow-hidden bg-white'
    : 'fixed inset-x-3 bottom-24 z-[60] flex h-[70dvh] flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl sm:inset-x-auto sm:right-6 sm:h-[520px] sm:max-h-[calc(100dvh-8rem)] sm:w-[380px]';
  // Full screen keeps the conversation to a readable measure.
  const column = expanded ? 'mx-auto w-full max-w-4xl' : '';

  return (
    <>
      {open && (
        <div className={panel} role="dialog" aria-label="Preventra Chatbot">
          {/* Header */}
          <div className="bg-ns-navy px-5 py-4 flex items-center justify-between shrink-0">
            <div>
              <h2 className="text-white font-semibold text-base leading-tight">Preventra Chatbot</h2>
              <p className="text-white/60 text-xs mt-0.5">Ask about patient risk data</p>
            </div>
            <div className="flex items-center gap-1">
              {messages.length > 0 && (
                <button
                  onClick={() => { setMessages([]); setShowSuggestions(false); }}
                  className="text-white/70 hover:text-white hover:bg-white/10 rounded-full p-1.5 transition-colors"
                  aria-label="New conversation" title="New conversation"
                >
                  <RotateCcw size={17} />
                </button>
              )}
              <button
                onClick={() => setExpanded((v) => !v)}
                className="text-white/70 hover:text-white hover:bg-white/10 rounded-full p-1.5 transition-colors"
                aria-label={expanded ? 'Exit full screen' : 'Expand to full screen'}
                title={expanded ? 'Exit full screen (Esc)' : 'Expand to full screen'}
              >
                {expanded ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
              </button>
              <button
                onClick={() => { setOpen(false); setExpanded(false); }}
                className="text-white/70 hover:text-white hover:bg-white/10 rounded-full p-1.5 transition-colors"
                aria-label="Close chat" title="Close"
              >
                <X size={18} />
              </button>
            </div>
          </div>

          {/* Message history */}
          <div ref={historyRef} className="flex-1 overflow-y-auto bg-gray-50 px-4 py-4">
            <div className={`space-y-3 ${column}`}>
              {messages.length === 0 && (
                <div className="mt-4">
                  <p className="mb-3 text-center text-sm text-gray-500">
                    Ask anything about your patients' risk, or start with a common question.
                  </p>
                  <SuggestionList questions={questions} onPick={ask} disabled={loading} grid={expanded} />
                </div>
              )}
              {messages.map((msg, idx) => (
                <div key={idx} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div
                    className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap leading-relaxed ${
                      msg.role === 'user'
                        ? 'bg-ns-navy text-white rounded-br-sm'
                        : msg.isError
                        ? 'bg-red-50 text-red-700 border border-red-200 rounded-bl-sm'
                        : 'bg-white text-gray-800 border border-gray-200 rounded-bl-sm'
                    }`}
                  >
                    {msg.role === 'bot' && !msg.isError
                      ? <BotText text={msg.text} entities={msg.entities} groups={groups} onFollow={afterFollow} />
                      : msg.text}
                  </div>
                </div>
              ))}
              {loading && (
                <div className="flex justify-start">
                  <div className="bg-white border border-gray-200 rounded-2xl rounded-bl-sm px-4 py-2.5 flex items-center gap-2 text-gray-400">
                    <Loader2 size={16} className="animate-spin" />
                    <span className="text-sm">Thinking…</span>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Common questions, any time - not only before the first one. */}
          {showSuggestions && messages.length > 0 && (
            <div className="max-h-[40%] shrink-0 overflow-y-auto border-t border-gray-200 bg-gray-50 px-3 py-3">
              <div className={column}>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
                  Common questions
                </div>
                <SuggestionList questions={questions} onPick={ask} disabled={loading} grid={expanded} />
              </div>
            </div>
          )}

          {/* Input */}
          <form onSubmit={handleSend} className="border-t border-gray-200 p-3 shrink-0 bg-white">
            <div className={`flex items-center gap-2 ${column}`}>
              {messages.length > 0 && (
                <button
                  type="button"
                  onClick={() => setShowSuggestions((v) => !v)}
                  className={`rounded-full p-2.5 shrink-0 transition-colors ${
                    showSuggestions ? 'bg-ns-navy/10 text-ns-navy' : 'text-gray-500 hover:bg-gray-100'}`}
                  aria-label="Common questions" aria-pressed={showSuggestions} title="Common questions"
                >
                  <Lightbulb size={16} />
                </button>
              )}
              <input
                ref={inputRef}
                type="text"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Ask a question…"
                disabled={loading}
                className="flex-1 text-sm border border-gray-300 rounded-full px-4 py-2 focus:outline-none focus:ring-2 focus:ring-ns-navy/40 disabled:bg-gray-100"
              />
              <button
                type="submit"
                disabled={loading || !question.trim()}
                className="bg-ns-navy text-white rounded-full p-2.5 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-ns-navy/90 transition-colors shrink-0"
                aria-label="Send question"
              >
                <Send size={16} />
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Floating toggle button. Hidden at full screen, where it would sit on
          top of the send button and the header already has a close. */}
      {!expanded && (
        <button
          onClick={() => setOpen((v) => !v)}
          className="fixed bottom-6 right-6 z-[60] bg-ns-navy hover:bg-ns-navy/90 text-white rounded-full p-4 shadow-xl transition-colors"
          aria-label={open ? 'Close chat' : 'Open chat'}
        >
          {open ? <X size={24} /> : <MessageCircle size={24} />}
        </button>
      )}
    </>
  );
}
