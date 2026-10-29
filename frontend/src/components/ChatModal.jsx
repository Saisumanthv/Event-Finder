import { useEffect, useRef, useState } from "react";
import { askCard, ingestCard } from "../utils/cardChat.js";

const SUGGESTIONS = [
  "When is it and what does it cost?",
  "Who can join?",
  "What should I prepare?",
  "Is there a certificate or prize?",
];

export default function ChatModal({ card, onClose }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [knowledge, setKnowledge] = useState(null); // null while the card is being read
  const endRef = useRef(null);
  const inputRef = useRef(null);

  // Read the card's details and page as soon as the chat opens, so the first answer is quick.
  useEffect(() => {
    let cancelled = false;
    ingestCard(card)
      .then((info) => !cancelled && setKnowledge(info))
      .catch(() => !cancelled && setKnowledge({ ready: false, passages: 0, page: false }));
    return () => {
      cancelled = true;
    };
  }, [card]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    inputRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, busy]);

  async function send(text) {
    const question = text.trim();
    if (!question || busy) return;
    const history = messages;
    setMessages([...history, { role: "user", text: question }]);
    setInput("");
    setBusy(true);
    try {
      const reply = await askCard(card, question, history);
      setMessages((m) => [...m, { role: "assistant", text: reply.answer, sources: reply.sources || [], fallback: reply.fallback }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", text: "Something went wrong. Please try again.", sources: [], fallback: true }]);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  let status = "Reading the listing and its page...";
  if (knowledge) {
    if (!knowledge.ready) status = "Answers will use the card details.";
    else if (knowledge.page) status = `Ready: ${knowledge.passages} passages from the listing and its page.`;
    else status = "Ready: using the listing details (the page text could not be read).";
  }

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal chat-modal" role="dialog" aria-modal="true" aria-labelledby="chat-title">
        <header className="modal-header">
          <div>
            <p className="modal-kicker">Ask about this {card.type || "listing"}</p>
            <h2 id="chat-title">{card.title}</h2>
          </div>
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="modal-body chat-body">
          <p className="chat-status">{status}</p>

          {messages.length === 0 && (
            <div className="chat-suggestions">
              <p>Try asking:</p>
              {SUGGESTIONS.map((s) => (
                <button type="button" className="pill" key={s} onClick={() => send(s)} disabled={busy}>
                  {s}
                </button>
              ))}
            </div>
          )}

          {messages.map((m, i) => (
            <div className={`chat-message ${m.role}`} key={i}>
              <div className="chat-bubble">{m.text}</div>
              {m.role === "assistant" && m.sources?.length > 0 && (
                <details className="chat-sources">
                  <summary>Passages used ({m.sources.length})</summary>
                  {m.sources.map((s, j) => (
                    <p key={j}>
                      <strong>{s.origin === "page" ? "Page" : "Listing"}:</strong> {s.text}
                    </p>
                  ))}
                </details>
              )}
            </div>
          ))}

          {busy && (
            <div className="chat-message assistant">
              <div className="chat-bubble typing">Thinking...</div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        <footer className="modal-footer chat-footer">
          <form
            className="chat-form"
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
          >
            <input
              ref={inputRef}
              type="text"
              value={input}
              maxLength={500}
              placeholder="Ask anything about this listing..."
              onChange={(e) => setInput(e.target.value)}
              disabled={busy}
            />
            <button type="submit" className="modal-button primary" disabled={busy || !input.trim()}>
              Send
            </button>
          </form>
          <p>AI answers use only this listing and its page. Check the official page for anything important.</p>
        </footer>
      </div>
    </div>
  );
}
