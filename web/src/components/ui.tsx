import type { ButtonHTMLAttributes, ReactNode } from "react";

import { TONE_CLASSES, type Tone, label } from "../lib/status";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-[1.75rem] leading-tight font-semibold text-ink">{title}</h1>
        {subtitle && <p className="mt-1 max-w-2xl text-sm text-ink-2">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Card({
  children,
  className,
  title,
  aside,
  as: Tag = "section",
}: {
  children: ReactNode;
  className?: string;
  title?: string;
  aside?: ReactNode;
  as?: "section" | "div" | "article";
}) {
  return (
    <Tag className={cx("rounded-xl border border-line bg-surface shadow-card", className)}>
      {title && (
        <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          {aside}
        </div>
      )}
      <div className="p-4">{children}</div>
    </Tag>
  );
}

export function Badge({ tone = "neutral", children, dot = true }: { tone?: Tone; children: ReactNode; dot?: boolean }) {
  const t = TONE_CLASSES[tone];
  return (
    <span
      data-tone={tone}
      className={cx("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[0.75rem] font-medium whitespace-nowrap", t.bg, t.text)}
    >
      {dot && <span aria-hidden className={cx("size-1.5 rounded-full", t.dot)} />}
      {children}
    </span>
  );
}

export function StatusBadge({ status, tone }: { status: string; tone: Tone }) {
  return <Badge tone={tone}>{label(status)}</Badge>;
}

type Variant = "primary" | "secondary" | "danger" | "ghost";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-accent-ink hover:opacity-90",
  secondary: "border border-line bg-surface text-ink hover:bg-surface-2",
  danger: "bg-failure text-white hover:opacity-90 dark:text-black",
  ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
};

export function Button({
  variant = "secondary",
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      type="button"
      {...rest}
      className={cx(
        "inline-flex min-h-9 items-center justify-center gap-2 rounded-lg px-3.5 text-sm font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-50",
        VARIANTS[variant],
        className,
      )}
    />
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx("skeleton", className)} />;
}

export function LoadingState({ rows = 3, label: text = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-label={text} className="space-y-3">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-16 w-full" />
      ))}
      <span className="sr-only">{text}…</span>
    </div>
  );
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-line px-6 py-12 text-center">
      <div aria-hidden className="mb-3 grid size-10 place-items-center rounded-full bg-surface-2 text-ink-3">
        ∅
      </div>
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint && <p className="mt-1 max-w-sm text-sm text-ink-2">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="rounded-xl border border-failure bg-failure-soft p-4 text-sm text-failure">
      <p className="font-medium">Something went wrong</p>
      <p className="mt-1 break-words text-ink-2">{msg}</p>
      {retry && (
        <Button className="mt-3" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <code className={cx("font-mono text-[0.8125rem]", className)}>{children}</code>;
}

export function Code({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <pre
      tabIndex={0}
      className={cx(
        "max-h-80 overflow-auto rounded-lg border border-line bg-surface-2 p-3 font-mono text-[0.8125rem] leading-relaxed whitespace-pre-wrap break-words text-ink",
        className,
      )}
    >
      {children}
    </pre>
  );
}
