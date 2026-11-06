import { useEffect, useRef, useState, type FormEvent } from "react";

import { useChat } from "../api/hooks";
import { Button, cx } from "./ui";

interface Msg {
  role: "human" | "assistant";
  content: string;
}

const SUGGESTIONS = ["Why this fix?", "What are the risks?", "What did you remember from before?"];

/** Questions go to the agent with the approval's context; nothing here can change the run. */
export default function ChatPanel({ approvalId }: { approvalId: string }) {
  const chat = useChat(approvalId);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState("");
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMsgs([]);
    setQ("");
  }, [approvalId]);
  useEffect(() => {
    // Braces matter: an effect must return a cleanup function or nothing, never the call's value.
    end.current?.scrollIntoView?.({ block: "nearest" });
  }, [msgs, chat.isPending]);

  const ask = (text: string) => {
    const question = text.trim();
    if (!question || chat.isPending) return;
    setMsgs((m) => [...m, { role: "human", content: question }]);
    setQ("");
    chat.mutate(question, {
      onSuccess: (r) => setMsgs((m) => [...m, { role: "assistant", content: r.answer }]),
    });
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    ask(q);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="max-h-72 min-h-24 space-y-2 overflow-y-auto rounded-lg border border-line bg-surface-2 p-3" role="log" aria-live="polite" aria-label="Conversation with the agent">
        {msgs.length === 0 && <p className="text-sm text-ink-3">Ask the agent about this request before you decide. It answers from the context above and cannot act.</p>}
        {msgs.map((m, i) => (
          <div key={i} className={cx("max-w-[90%] rounded-lg px-3 py-2 text-sm", m.role === "human" ? "ml-auto bg-accent text-accent-ink" : "bg-surface text-ink")}>
            {m.content}
          </div>
        ))}
        {chat.isPending && <div className="text-sm text-ink-3">Thinking…</div>}
        {chat.isError && (
          <div role="alert" className="text-sm text-failure">
            {chat.error.message}
          </div>
        )}
        <div ref={end} />
      </div>
      {msgs.length === 0 && (
        <div className="flex flex-wrap gap-1.5">
          {SUGGESTIONS.map((s) => (
            <button key={s} type="button" onClick={() => ask(s)} className="rounded-full border border-line px-3 py-1 text-[0.75rem] text-ink-2 hover:bg-surface-2">
              {s}
            </button>
          ))}
        </div>
      )}
      <form onSubmit={submit} className="flex gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Question for the agent"
          placeholder="Ask a question…"
          className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-3"
        />
        <Button type="submit" disabled={!q.trim() || chat.isPending}>
          Ask
        </Button>
      </form>
    </div>
  );
}
