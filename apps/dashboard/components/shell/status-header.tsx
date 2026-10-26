"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { RefreshCw } from "lucide-react";

import { useLive } from "@/components/live-provider";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { API_BASE_URL } from "@/lib/api/base-url";
import { formatLatency, formatRelative, toneFor } from "@/lib/format";
import { navItemFor } from "@/lib/nav";
import { cn } from "@/lib/utils";

const STREAM_LABEL = {
  connecting: { text: "connecting", className: "text-muted-foreground", dot: "bg-muted-foreground" },
  open: { text: "live", className: "text-success", dot: "bg-success" },
  reconnecting: { text: "reconnecting", className: "text-warning", dot: "bg-warning" },
  closed: { text: "offline", className: "text-destructive", dot: "bg-destructive" },
} as const;

function DependencyChip({
  name,
  status,
  latency,
  detail,
}: {
  name: string;
  status: string;
  latency: number;
  detail: string;
}) {
  const tone = toneFor(status);
  return (
    <span
      title={detail}
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px]",
        tone.surface,
      )}
    >
      <span className={cn("size-1.5 rounded-full", tone.dot)} aria-hidden />
      <span className="font-medium">{name}</span>
      <span className="tabular text-muted-foreground">{formatLatency(latency)}</span>
    </span>
  );
}

export function StatusHeader({ onMenu }: { onMenu: () => void }) {
  const pathname = usePathname();
  const section = navItemFor(pathname);
  const { health, stream } = useLive();

  const report = health.data;
  const overall = toneFor(report?.status ?? (health.error ? "down" : "unknown"));
  const healthy = report?.dependencies.filter((dep) => dep.status === "ok").length ?? 0;
  const total = report?.dependencies.length ?? 0;
  const streamState = STREAM_LABEL[stream.status];

  return (
    <header className="sticky top-0 z-30 border-b border-border/80 bg-background/85 backdrop-blur-xl">
      <div className="flex h-14 items-center gap-3 px-4 sm:px-6 lg:px-8">
        <button
          type="button"
          onClick={onMenu}
          className="grid size-8 place-items-center rounded-md border border-border text-muted-foreground hover:bg-accent hover:text-foreground lg:hidden"
          aria-label="Open navigation"
        >
          <span className="flex flex-col gap-0.5">
            <span className="block h-0.5 w-4 bg-current" />
            <span className="block h-0.5 w-4 bg-current" />
            <span className="block h-0.5 w-4 bg-current" />
          </span>
        </button>

        <div className="min-w-0 text-[13px]">
          <span className="hidden text-muted-foreground sm:inline">DocOck / </span>
          <span className="font-medium">{section.label}</span>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <div
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium uppercase tracking-wide",
              overall.surface,
            )}
            title={health.error ?? "Overall stack status from /api/health"}
          >
            <span className={cn("size-1.5 rounded-full", overall.dot)} aria-hidden />
            {report ? (report.status === "ok" ? "Stack ok" : report.status) : "Checking"}
            {report ? (
              <span className="tabular text-muted-foreground">
                {healthy}/{total}
              </span>
            ) : null}
          </div>

          <div
            className="hidden items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-[11px] sm:inline-flex"
            title={`SSE ${API_BASE_URL}/api/events`}
          >
            <span className={cn("size-1.5 rounded-full", streamState.dot)} aria-hidden />
            <span className={cn("font-medium uppercase tracking-wide", streamState.className)}>
              {streamState.text}
            </span>
            {stream.lastEventAt ? (
              <span className="tabular text-muted-foreground">
                {formatRelative(stream.lastEventAt)}
              </span>
            ) : null}
          </div>

          <Button
            variant="ghost"
            size="icon"
            onClick={health.refresh}
            title={`Re-check dependencies (${API_BASE_URL})`}
            aria-label="Re-check dependencies"
            className="size-8"
          >
            <RefreshCw className={cn("size-3.5", health.loading && "animate-spin")} />
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 px-4 pb-2 sm:px-6 lg:px-8">
        {report ? (
          <>
            {report.dependencies.map((dep) => (
              <DependencyChip
                key={dep.name}
                name={dep.name}
                status={dep.status}
                latency={dep.latency_ms}
                detail={dep.detail}
              />
            ))}
            <span className="ml-1 shrink-0 text-[11px] text-muted-foreground">
              checked {formatRelative(health.updatedAt)}
              {health.error ? (
                <span className="ml-1 text-destructive" title={health.error}>
                  · refresh failed
                </span>
              ) : null}
            </span>
          </>
        ) : (
          <>
            {Array.from({ length: 7 }).map((_, index) => (
              <Skeleton key={index} className="h-5 w-24 rounded-full" />
            ))}
            <span className="ml-1 text-[11px] text-muted-foreground">
              {health.error ? (
                <span className="text-destructive">API unreachable · {health.error}</span>
              ) : (
                "probing dependencies…"
              )}
            </span>
          </>
        )}
        <Link
          href="/"
          className="ml-auto hidden shrink-0 text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline md:block"
        >
          status
        </Link>
      </div>
    </header>
  );
}
