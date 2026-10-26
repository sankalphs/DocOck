"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, PowerOff } from "lucide-react";

import { ActiveFaults } from "@/components/faults/active-faults";
import { FaultHistory } from "@/components/faults/fault-history";
import { InjectForm } from "@/components/faults/inject-form";
import { openStatus } from "@/components/faults/fault-status";
import { useLive } from "@/components/live-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { InjectionPlan, InjectionRequest, ResetReport } from "@/lib/api/faults";
import { useFaultActions, useFaultCatalog, useFaults, useNow } from "@/lib/hooks/use-faults";
import { formatClock } from "@/lib/format";

export function FaultLab() {
  const { stream } = useLive();
  const lastFaultEvent = stream.events.find((event) => event.type.startsWith("fault."));
  const faults = useFaults(lastFaultEvent?.id ?? null);
  const catalog = useFaultCatalog();
  const now = useNow(1000);
  const actions = useFaultActions(faults.refresh);

  const [plan, setPlan] = useState<InjectionPlan | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [report, setReport] = useState<ResetReport | null>(null);
  const [revertingId, setRevertingId] = useState<number | null>(null);

  const items = faults.data?.items ?? [];
  const open = items.filter((fault) => openStatus(fault.status));

  const handleDryRun = async (body: InjectionRequest) => {
    setNotice(null);
    const result = await actions.dryRun(body);
    if (result?.plan) setPlan(result.plan);
  };

  const handleInject = async (body: InjectionRequest) => {
    setPlan(null);
    setReport(null);
    const result = await actions.inject(body);
    if (result?.fault) {
      setNotice(
        `${result.fault.label} injected on ${result.fault.target} as ${result.fault.status} (fault #${result.fault.id}).`,
      );
    }
  };

  const handleRevert = async (id: number) => {
    setRevertingId(id);
    const fault = await actions.revert(id);
    setRevertingId(null);
    if (fault) setNotice(`Fault #${fault.id} is ${fault.status}; the flag/container was read back.`);
  };

  const handleResetAll = async () => {
    if (!confirmReset) {
      setConfirmReset(true);
      return;
    }
    setConfirmReset(false);
    setNotice(null);
    const result = await actions.resetAll();
    if (result) {
      setReport(result);
      setPlan(null);
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Lab state</CardTitle>
            <CardDescription>
              {faults.data
                ? `${open.length} open · ${faults.data.total} tracked · updated ${formatClock(
                    new Date(faults.updatedAt ?? now).toISOString(),
                  )}`
                : "loading faults…"}
              {lastFaultEvent ? (
                <>
                  {" "}
                  · last event <code className="font-mono text-[10px]">{lastFaultEvent.type}</code>
                </>
              ) : null}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            {faults.error ? <Badge variant="destructive">poll failed</Badge> : null}
            <Badge variant={open.length > 0 ? "destructive" : "success"}>
              {open.length > 0 ? "fault injected" : "clean"}
            </Badge>
            <Button
              variant={confirmReset ? "destructive" : "outline"}
              size="sm"
              disabled={actions.busy !== null}
              onClick={handleResetAll}
              title="Revert every open fault and reset flag drift"
            >
              <PowerOff className="size-3.5" />
              {actions.busy === "reset-all"
                ? "Resetting…"
                : confirmReset
                  ? "Confirm reset all"
                  : "Reset all"}
            </Button>
            {confirmReset ? (
              <Button variant="ghost" size="sm" onClick={() => setConfirmReset(false)}>
                Cancel
              </Button>
            ) : null}
          </div>
        </CardHeader>
        {notice || report ? (
          <CardContent>
            {notice ? (
              <p className="flex items-start gap-2 rounded-md border border-success/30 bg-success/10 p-2 text-xs text-success">
                <CheckCircle2 className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                <span>
                  {notice}{" "}
                  <Link href="/" className="underline underline-offset-2">
                    watch the overview charts
                  </Link>
                  .
                </span>
              </p>
            ) : null}
            {report ? (
              <p className="mt-2 flex items-start gap-2 rounded-md border border-border/70 bg-muted/30 p-2 text-xs text-muted-foreground">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                <span>
                  Reset all: {report.reverted.length} reverted · {report.flag_drift.length} flag(s)
                  reset · {report.failed.length} failed.
                  {report.failed.length > 0
                    ? ` First error: ${report.failed[0]?.message ?? ""}`
                    : ""}
                </span>
              </p>
            ) : null}
          </CardContent>
        ) : null}
      </Card>

      {catalog.data ? (
        <InjectForm
          catalog={catalog.data}
          busy={actions.busy}
          error={actions.error}
          plan={plan}
          onDryRun={handleDryRun}
          onInject={handleInject}
        />
      ) : (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Loading the fault catalog…</CardTitle>
              <CardDescription>
                {catalog.error ?? "GET /api/faults/catalog"}
              </CardDescription>
            </div>
          </CardHeader>
        </Card>
      )}

      <ActiveFaults
        faults={items}
        now={now}
        busyId={revertingId}
        onRevert={handleRevert}
      />

      <FaultHistory faults={items} />
    </div>
  );
}
