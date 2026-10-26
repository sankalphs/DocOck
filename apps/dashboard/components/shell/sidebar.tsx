"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, X } from "lucide-react";

import { NAV_ITEMS } from "@/lib/nav";
import { cn } from "@/lib/utils";

type SidebarProps = {
  open: boolean;
  onClose: () => void;
};

export function Sidebar({ open, onClose }: SidebarProps) {
  const pathname = usePathname();

  return (
    <>
      <div
        aria-hidden={!open}
        onClick={onClose}
        className={cn(
          "fixed inset-0 z-40 bg-black/60 backdrop-blur-sm transition-opacity lg:hidden",
          open ? "opacity-100" : "pointer-events-none opacity-0",
        )}
      />

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col border-r border-border/80 bg-card/70 backdrop-blur-xl transition-transform duration-200",
          "lg:sticky lg:top-0 lg:z-0 lg:h-dvh lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 shrink-0 items-center gap-2.5 border-b border-border/60 px-4">
          <div className="grid size-8 place-items-center rounded-lg bg-primary/15 text-primary ring-1 ring-primary/30">
            <Activity className="size-4" aria-hidden />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold leading-tight">DocOck</div>
            <div className="truncate text-[11px] leading-tight text-muted-foreground">
              incident response lab
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground lg:hidden"
            aria-label="Close navigation"
          >
            <X className="size-4" />
          </button>
        </div>

        <nav className="min-h-0 flex-1 overflow-y-auto px-2 py-3" aria-label="Sections">
          <ul className="space-y-0.5">
            {NAV_ITEMS.map((item) => {
              const active =
                item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
              const Icon = item.icon;
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onClose}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "group flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                      active
                        ? "bg-accent text-foreground shadow-[inset_2px_0_0_0_var(--primary)]"
                        : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                    )}
                  >
                    <Icon
                      className={cn(
                        "size-4 shrink-0",
                        active ? "text-primary" : "text-muted-foreground group-hover:text-foreground",
                      )}
                      aria-hidden
                    />
                    <span className="truncate">{item.label}</span>
                    {!item.ready ? (
                      <span
                        title={`Available in phase ${item.phase}`}
                        className="ml-auto rounded border border-border px-1 text-[10px] font-medium leading-4 text-muted-foreground/80"
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

        <div className="shrink-0 border-t border-border/60 px-4 py-2.5 text-[11px] leading-5 text-muted-foreground">
          <div className="flex items-center gap-1.5">
            <span className="size-1.5 rounded-full bg-success" />
            local only · v0.1.0
          </div>
        </div>
      </aside>
    </>
  );
}
