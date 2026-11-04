import { useEffect, useRef } from "react";

import type { TraceNode } from "../api/client";
import { useSpanDetail } from "../api/hooks";
import { ms, pretty, usd } from "../lib/format";
import { TONE_CLASSES, traceStatus } from "../lib/status";
import { summarize } from "../lib/trace";
import { Badge, Code, ErrorState, LoadingState, Mono } from "./ui";

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="rounded-lg bg-surface-2 px-3 py-2">
      <dt className="text-[0.7rem] tracking-wide text-ink-3 uppercase">{k}</dt>
      <dd className="tabular text-sm font-medium text-ink">{v}</dd>
    </div>
  );
}

export default function TraceDrawer({ taskId, node, onClose }: { taskId: string; node: TraceNode; onClose: () => void }) {
  const detail = useSpanDetail(taskId, node.span_id);
  const close = useRef<HTMLButtonElement>(null);
  const status = traceStatus(node);
  const tone = status;

  // Focus moves into the dialog and Escape closes it; focus returns to the node on unmount.
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, [onClose]);

  const d = detail.data;
  return (
    <div className="fixed inset-0 z-40" role="presentation">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`Span details: ${summarize(node).title}`}
        className="absolute inset-y-0 right-0 flex w-[min(40rem,100vw)] flex-col overflow-hidden border-l border-line bg-surface shadow-card"
      >
        <header className="flex items-start justify-between gap-3 border-b border-line p-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-semibold text-ink">{summarize(node).title}</h2>
              <Badge tone={tone}>{status}</Badge>
            </div>
            <Mono className="text-ink-3">{node.name}</Mono>
          </div>
          <button
            ref={close}
            type="button"
            onClick={onClose}
            aria-label="Close details"
            className="grid size-9 place-items-center rounded-lg text-ink-2 hover:bg-surface-2 hover:text-ink"
          >
            ✕
          </button>
        </header>

        <div className="space-y-5 overflow-y-auto p-4">
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat k="Agent" v={node.agent ?? "—"} />
            <Stat k="Latency" v={ms(node.duration_ms)} />
            <Stat k="Cost" v={usd(node.cost_usd)} />
            <Stat k="Subtree cost" v={usd(node.subtree_cost_usd)} />
            {node.model && <Stat k="Model" v={node.model} />}
            {node.tokens_in + node.tokens_out > 0 && <Stat k="Tokens in / out" v={`${node.tokens_in} / ${node.tokens_out}`} />}
          </dl>

          {node.error && (
            <div role="alert" className={`rounded-lg border p-3 text-sm ${TONE_CLASSES.failure.border} ${TONE_CLASSES.failure.bg} ${TONE_CLASSES.failure.text}`}>
              {node.error}
            </div>
          )}

          {detail.isLoading && <LoadingState rows={2} label="Loading details" />}
          {detail.isError && <ErrorState error={detail.error} retry={() => detail.refetch()} />}

          {d?.llm && (
            <section aria-label="Prompt and response" className="space-y-3">
              <h3 className="text-sm font-semibold text-ink">
                Prompt <span className="font-normal text-ink-3">· {d.llm.role} {d.llm.cache_hit && "· cache hit"}</span>
              </h3>
              {d.llm.messages.map((m, i) => (
                <div key={i}>
                  <div className="mb-1 text-[0.75rem] font-medium text-ink-3 uppercase">{m.role}</div>
                  <Code>{pretty(m.content)}</Code>
                </div>
              ))}
              <h3 className="text-sm font-semibold text-ink">Response</h3>
              <Code>{pretty(d.llm.response ?? "")}</Code>
            </section>
          )}

          {d?.tool && (
            <section aria-label="Tool input and output" className="space-y-3">
              <h3 className="text-sm font-semibold text-ink">
                Input <Mono className="text-ink-3">· {d.tool.tool}</Mono>
              </h3>
              <Code>{pretty(d.tool.inputs)}</Code>
              <h3 className="text-sm font-semibold text-ink">Output</h3>
              <Code>{d.tool.error ? d.tool.error : pretty(d.tool.outputs)}</Code>
            </section>
          )}

          {Object.keys(node.attributes).length > 0 && (
            <section aria-label="Attributes">
              <h3 className="mb-1.5 text-sm font-semibold text-ink">Attributes</h3>
              <Code>{pretty(node.attributes)}</Code>
            </section>
          )}
        </div>
      </aside>
    </div>
  );
}
