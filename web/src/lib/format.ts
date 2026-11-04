export function usd(v: number): string {
  if (v === 0) return "$0";
  if (v < 0.01) return `$${v.toFixed(5)}`;
  return `$${v.toFixed(v < 1 ? 4 : 2)}`;
}

export function ms(v: number): string {
  if (v < 1) return "<1 ms";
  if (v < 1000) return `${Math.round(v)} ms`;
  return `${(v / 1000).toFixed(v < 10000 ? 2 : 1)} s`;
}

/** The API sends naive UTC timestamps; treat them as UTC. */
export function ago(iso: string): string {
  const utc = iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`;
  const s = Math.max(0, (Date.now() - new Date(utc).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function pretty(v: unknown): string {
  if (typeof v === "string") {
    try {
      return JSON.stringify(JSON.parse(v), null, 2);
    } catch {
      return v;
    }
  }
  return JSON.stringify(v, null, 2);
}
