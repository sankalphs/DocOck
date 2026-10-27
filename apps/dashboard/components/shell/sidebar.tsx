"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { X } from "lucide-react";

import { Mascot, moodFor } from "@/components/brand/mascot";
import { useLive } from "@/components/live-provider";
import { NAV_ITEMS, isNavItemActive } from "@/lib/nav";
import { cn } from "@/lib/utils";

type SidebarProps = {
  open: boolean;
  onClose: () => void;
};

export function Sidebar({ open, onClose }: SidebarProps) {
  const pathname = usePathname();
  const { health } = useLive();
  const mood = moodFor(health.data?.status ?? (health.error ? "down" : null));

  return (
    <>
      <div
        aria-hidden={!open}
        onClick={onClose}
        className={cn(
          "fixed inset-0 z-40 bg-foreground/25 transition-opacity duration-300 lg:hidden",
          open ? "opacity-100" : "pointer-events-none opacity-0",
        )}
      />

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col border-r bg-background transition-transform duration-300 ease-[var(--ease-out)]",
          "lg:sticky lg:top-0 lg:z-0 lg:h-dvh lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-16 shrink-0 items-center gap-2.5 px-4">
          <Link
            href="/"
            className="group flex min-w-0 items-center gap-2.5 rounded-lg"
            aria-label="DocOck home"
          >
            <Mascot
              variant="head"
              mood={mood}
              className="size-9 shrink-0 transition-transform duration-300 ease-[var(--ease-out)] group-hover:-rotate-6"
            />
            <div className="min-w-0">
              <div className="truncate text-[15px] font-bold leading-tight">DocOck</div>
              <div className="truncate text-xs leading-tight text-muted-foreground">
                incident response lab
              </div>
            </div>
          </Link>
          <button
            type="button"
            onClick={onClose}
            className="press ml-auto grid size-9 place-items-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground lg:hidden"
            aria-label="Close navigation"
          >
            <X className="size-4" />
          </button>
        </div>

        <nav className="min-h-0 flex-1 overflow-y-auto px-3 py-3" aria-label="Sections">
          <ul className="space-y-1">
            {NAV_ITEMS.map((item) => {
              const active = isNavItemActive(item, pathname);
              const Icon = item.icon;
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onClose}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "press group flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm",
                      active
                        ? "bg-card font-semibold text-foreground ring-1 ring-border"
                        : "text-muted-foreground hover:bg-accent hover:text-foreground",
                    )}
                  >
                    <Icon
                      className={cn(
                        "size-4 shrink-0 transition-colors",
                        active ? "text-primary" : "text-muted-foreground group-hover:text-foreground",
                      )}
                      aria-hidden
                    />
                    <span className="truncate">{item.label}</span>
                    {!item.ready ? (
                      <span
                        title={`Available in phase ${item.phase}`}
                        className="ml-auto rounded-full bg-muted px-1.5 text-[11px] font-medium leading-5 text-muted-foreground"
                      >
                        {item.phase}
                      </span>
                    ) : null}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="shrink-0 px-4 py-3 text-xs leading-5 text-muted-foreground">
          <div className="flex items-center gap-1.5">
            <span className="size-1.5 rounded-full bg-leaf-strong" />
            local only · v0.1.0
          </div>
        </div>
      </aside>
    </>
  );
}
