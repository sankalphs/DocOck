export function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}

export function formatRelative(epochMs: number | null | undefined, now = Date.now()): string {
  if (!epochMs) return "never";
  const seconds = Math.max(0, Math.round((now - epochMs) / 1000));
  if (seconds < 2) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function formatClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString([], { hour12: false });
}

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 10_000) return `${(value / 1000).toFixed(1)}k`;
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export type Tone = {
  label: string;
  dot: string;
  text: string;
  surface: string;
};

const TONES: Record<string, Tone> = {
  ok: {
    label: "Healthy",
    dot: "bg-success",
    text: "text-success",
    surface: "bg-success/10 border-success/30",
  },
  degraded: {
    label: "Degraded",
    dot: "bg-warning",
    text: "text-warning",
    surface: "bg-warning/10 border-warning/30",
  },
  down: {
    label: "Down",
    dot: "bg-destructive",
    text: "text-destructive",
    surface: "bg-destructive/10 border-destructive/30",
  },
  unconfigured: {
    label: "Not configured",
    dot: "bg-muted-foreground",
    text: "text-muted-foreground",
    surface: "bg-muted/60 border-border",
  },
  unknown: {
    label: "Unknown",
    dot: "bg-muted-foreground",
    text: "text-muted-foreground",
    surface: "bg-muted/60 border-border",
  },
};

export function toneFor(status: string | null | undefined): Tone {
  return TONES[status ?? "unknown"] ?? TONES.unknown;
}
