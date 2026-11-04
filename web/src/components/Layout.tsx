import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { useApprovals } from "../api/hooks";
import { cx } from "./ui";

const NAV = [
  { to: "/run", label: "Run console", icon: "▶" },
  { to: "/queue", label: "Review queue", icon: "✓", badge: true },
  { to: "/traces", label: "Trace explorer", icon: "⌥" },
  { to: "/memory", label: "Memory", icon: "◈" },
  { to: "/cost", label: "Cost", icon: "◔" },
];

type Theme = "light" | "dark" | "system";

function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("conductor-theme") as Theme | null) ?? "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
    try {
      if (theme === "system") localStorage.removeItem("conductor-theme");
      else localStorage.setItem("conductor-theme", theme);
    } catch {
      /* private mode: the choice just will not persist */
    }
  }, [theme]);
  return [theme, setTheme];
}

function ThemeSwitch() {
  const [theme, setTheme] = useTheme();
  const opts: { v: Theme; l: string }[] = [
    { v: "light", l: "Light" },
    { v: "system", l: "Auto" },
    { v: "dark", l: "Dark" },
  ];
  return (
    <div role="radiogroup" aria-label="Theme" className="inline-flex rounded-lg border border-line bg-surface p-0.5 text-[0.75rem]">
      {opts.map((o) => (
        <button
          key={o.v}
          type="button"
          role="radio"
          aria-checked={theme === o.v}
          onClick={() => setTheme(o.v)}
          className={cx(
            "rounded-md px-2.5 py-1 font-medium transition-colors",
            theme === o.v ? "bg-accent text-accent-ink" : "text-ink-2 hover:text-ink",
          )}
        >
          {o.l}
        </button>
      ))}
    </div>
  );
}

function Nav({ vertical }: { vertical: boolean }) {
  const pending = useApprovals("pending").data?.length ?? 0;
  return (
    <nav aria-label="Main" className={cx("flex gap-1", vertical ? "flex-col" : "overflow-x-auto")}>
      {NAV.map((n) => (
        <NavLink
          key={n.to}
          to={n.to}
          className={({ isActive }) =>
            cx(
              "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors",
              isActive ? "bg-accent-soft text-accent" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
            )
          }
        >
          <span aria-hidden className="w-4 text-center text-[0.8rem]">
            {n.icon}
          </span>
          {n.label}
          {n.badge && pending > 0 && (
            <span aria-label={`${pending} pending`} className="ml-auto rounded-full bg-escalated px-1.5 text-[0.7rem] font-semibold text-white dark:text-black">
              {pending}
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  );
}

function Wordmark() {
  return (
    <div className="flex items-center gap-2.5">
      <span aria-hidden className="grid size-8 place-items-center rounded-lg bg-accent text-accent-ink">
        <svg viewBox="0 0 24 24" className="size-4" fill="currentColor">
          <path d="M7 5v14l12-7z" />
        </svg>
      </span>
      <span className="text-[1.05rem] font-semibold tracking-tight">Conductor</span>
    </div>
  );
}

export default function Layout() {
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[15rem_1fr]">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-2 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>
      <aside className="hidden border-r border-line bg-surface lg:flex lg:flex-col lg:gap-6 lg:p-4">
        <Wordmark />
        <Nav vertical />
        <div className="mt-auto space-y-3">
          <ThemeSwitch />
          <p className="text-[0.75rem] leading-snug text-ink-3">
            Reviews only. Conductor never merges or deploys: that stays with a human.
          </p>
        </div>
      </aside>
      <div className="min-w-0">
        <header className="sticky top-0 z-30 border-b border-line bg-surface/90 backdrop-blur lg:hidden">
          <div className="flex items-center justify-between gap-3 px-4 py-2.5">
            <Wordmark />
            <ThemeSwitch />
          </div>
          <div className="px-3 pb-2">
            <Nav vertical={false} />
          </div>
        </header>
        <main id="main" className="mx-auto w-full max-w-[84rem] px-4 py-6 md:px-8 md:py-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
