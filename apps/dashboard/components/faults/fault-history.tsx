"use client";

import { useCallback } from "react";
import { History } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fetchFaultAudit, type AuditEntry, type FaultView } from "@/lib/api/faults";
import {
  formatParams,
  statusLabel,
  statusVariant,
} from "@/components/faults/fault-status";
import { usePolling } from "@/lib/hooks/use-polling";
import { formatClock } from "@/lib/format";

function duration(fault: FaultView): string {
  if (!fault.reset_at) return "—";
  const seconds = Math.max(
    0,
    Math.round(
      (new Date(fault.reset_at).getTime() - new Date(fault.injected_at).getTime()) / 1000,
    ),
  );
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function FaultHistory({ faults }: { faults: FaultView[] }) {
  const auditFetcher = useCallback(() => fetchFaultAudit(15), []);
  const audit = usePolling<AuditEntry[]>("fault-audit", auditFetcher, 5000);
  const history = faults.filter(
    (fault) => fault.status === "REVERTED" || fault.status === "EXPIRED" || fault.status === "FAILED",
  );

  return (
    <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <History className="size-3.5 text-primary" aria-hidden />
              Fault history
            </CardTitle>
            <CardDescription>
              Showing {Math.min(history.length, 25)} of {history.length} reverted, expired, or
              failed faults, newest first.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          {history.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border/70 px-3 py-6 text-center text-xs text-muted-foreground">
              Nothing has been reverted yet.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Fault</TableHead>
                  <TableHead>Target</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Injected</TableHead>
                  <TableHead>Held</TableHead>
                  <TableHead>Params</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {history.slice(0, 25).map((fault) => (
                  <TableRow key={fault.id}>
                    <TableCell>
                      <span className="font-medium">{fault.label}</span>
                      <span className="tabular ml-1 text-[10px] text-muted-foreground">
                        #{fault.id}
                      </span>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{fault.target}</TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(fault.status)}>
                        {statusLabel(fault.status)}
                      </Badge>
                    </TableCell>
                    <TableCell className="tabular text-xs text-muted-foreground">
                      {formatClock(fault.injected_at)}
                    </TableCell>
                    <TableCell className="tabular text-xs text-muted-foreground">
                      {duration(fault)}
                    </TableCell>
                    <TableCell className="max-w-[16rem] truncate text-[11px] text-muted-foreground">
                      {formatParams(fault.params)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Audit trail</CardTitle>
            <CardDescription>
              <code className="font-mono text-[10px]">runtime/audit/faults.jsonl</code> — every
              write with actor, before, and after.
            </CardDescription>
          </div>
          <Badge variant="outline">{audit.data?.length ?? 0}</Badge>
        </CardHeader>
        <CardContent>
          {audit.error ? (
            <p className="text-[11px] text-destructive">{audit.error}</p>
          ) : null}
          {audit.data && audit.data.length > 0 ? (
            <ul className="-mt-1 max-h-[22rem] space-y-2 overflow-y-auto">
              {[...audit.data].reverse().map((entry, index) => (
                <li
                  key={`${entry.at}-${index}`}
                  className="border-b border-border/50 pb-2 last:border-0"
                >
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary" className="font-mono text-[10px]">
                      {entry.action}
                    </Badge>
                    <span className="truncate text-xs">
                      {entry.fault_id ?? "—"} {entry.target ? `on ${entry.target}` : ""}
                    </span>
                    <span className="tabular ml-auto text-[10px] text-muted-foreground">
                      {formatClock(entry.at)}
                    </span>
                  </div>
                  <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                    {entry.outcome} · {entry.actor} · {entry.detail}
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">No audit entries yet.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
