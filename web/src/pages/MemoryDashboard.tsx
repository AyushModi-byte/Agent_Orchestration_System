import { useEffect, useRef, useState } from "react";

import type { MemoryItem } from "../api/client";
import { useDeleteMemory, useMemory } from "../api/hooks";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, Mono, PageHeader } from "../components/ui";
import { ago } from "../lib/format";
import { getUserId, setUserId } from "../lib/user";

const TITLES: Record<string, string> = {
  plans: "Plans that were used",
  worked: "What worked",
  failed: "What failed",
  preferences: "Preferences",
  facts: "Repository facts",
  other: "Other",
};

function ConfirmDelete({ userId, count, onClose }: { userId: string; count: number; onClose: () => void }) {
  const del = useDeleteMemory();
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancel.current?.focus();
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-40 grid place-items-center p-4">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} aria-hidden />
      <div role="alertdialog" aria-modal="true" aria-labelledby="del-title" aria-describedby="del-desc" className="relative w-full max-w-md rounded-xl border border-line bg-surface p-5 shadow-card">
        <h2 id="del-title" className="text-lg font-semibold text-ink">
          Delete all memory for “{userId}”?
        </h2>
        <p id="del-desc" className="mt-2 text-sm text-ink-2">
          This permanently removes {count} remembered {count === 1 ? "item" : "items"}: plans, lessons, preferences and repository facts. Future runs will start without them. This cannot be undone.
        </p>
        {del.isError && <p role="alert" className="mt-3 text-sm text-failure">{del.error.message}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button ref={cancel} type="button" onClick={onClose} className="min-h-9 rounded-lg border border-line px-3.5 text-sm font-medium text-ink hover:bg-surface-2">
            Cancel
          </button>
          <Button variant="danger" disabled={del.isPending} onClick={() => del.mutate(userId, { onSuccess: onClose })}>
            {del.isPending ? "Deleting…" : "Delete everything"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Item({ it }: { it: MemoryItem }) {
  return (
    <li className="rounded-lg border border-line bg-surface p-3">
      <p className="text-sm text-ink">{it.text}</p>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.75rem] text-ink-3">
        {it.repo && <Mono>{it.repo}</Mono>}
        <span title="Importance blends how often it is used, how recent it is and human feedback">importance {it.importance.toFixed(2)}</span>
        <span>used {it.access_count}×</span>
        <span>saved {ago(it.created_at)}</span>
        {it.feedback !== 0 && <Badge tone={it.feedback > 0 ? "success" : "warning"}>{it.feedback > 0 ? "endorsed" : "disputed"}</Badge>}
      </div>
    </li>
  );
}

export default function MemoryDashboard() {
  const [user, setUser] = useState(getUserId());
  const [draft, setDraft] = useState(user);
  const [confirm, setConfirm] = useState(false);
  const [repo, setRepo] = useState("all");
  const q = useMemory(user);
  const total = q.data ? Object.values(q.data.counts).reduce((a, b) => a + b, 0) : 0;
  const repos = q.data ? [...new Set(Object.values(q.data.groups).flat().map((i) => i.repo).filter(Boolean))] : [];

  return (
    <>
      <PageHeader
        title="Memory"
        subtitle="What Conductor remembers about you and your repositories, and how it uses it. You can erase it all at any time."
        actions={
          <form
            className="flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setUser(draft.trim() || "demo");
              setUserId(draft.trim() || "demo");
            }}
          >
            <label className="text-sm text-ink-2">
              User
              <input value={draft} onChange={(e) => setDraft(e.target.value)} className="ml-2 w-32 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink" />
            </label>
            <Button type="submit">Show</Button>
          </form>
        }
      />
      {q.isLoading && <LoadingState rows={4} label="Loading memory" />}
      {q.isError && <ErrorState error={q.error} retry={() => q.refetch()} />}
      {q.data && total === 0 && <EmptyState title={`Nothing remembered for “${user}”`} hint="Finished reviews teach the system what worked and what did not; those lessons appear here." />}
      {q.data && total > 0 && (
        <div className="space-y-5">
          <Card>
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-sm text-ink-2">
                <span className="tabular text-lg font-semibold text-ink">{total}</span> remembered items for <Mono>{user}</Mono>
              </p>
              {repos.length > 1 && (
                <label className="text-sm text-ink-2">
                  Repository
                  <select value={repo} onChange={(e) => setRepo(e.target.value)} className="ml-2 rounded-lg border border-line bg-surface px-2 py-1.5 text-sm text-ink">
                    <option value="all">All</option>
                    {repos.map((r) => (
                      <option key={r}>{r}</option>
                    ))}
                  </select>
                </label>
              )}
              <Button variant="danger" className="ml-auto" onClick={() => setConfirm(true)}>
                Delete all memory…
              </Button>
            </div>
          </Card>
          <div className="grid gap-5 lg:grid-cols-2">
            {Object.entries(q.data.groups)
              .filter(([, items]) => items.length > 0)
              .map(([g, items]) => {
                const shown = items.filter((i) => repo === "all" || i.repo === repo || !i.repo);
                return (
                  <Card key={g} title={TITLES[g] ?? g} aside={<span className="tabular text-[0.75rem] text-ink-3">{shown.length}</span>}>
                    <ul className="space-y-2">{shown.map((i) => <Item key={i.id} it={i} />)}</ul>
                  </Card>
                );
              })}
          </div>
        </div>
      )}
      {confirm && <ConfirmDelete userId={user} count={total} onClose={() => setConfirm(false)} />}
    </>
  );
}
