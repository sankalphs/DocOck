"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, FlaskConical, History, ShieldCheck, XCircle } from "lucide-react";

import { useLive } from "@/components/live-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  actionVariant,
  approvalVariant,
  approve,
  canDecide,
  citedCodes,
  diagnosisOf,
  dryRun,
  fetchApprovalAudit,
  fetchApprovals,
  plannedCheck,
  reject,
  riskVariant,
  ttlLabel,
  type ApprovalView,
  type DryRunResult,
  type PlanCheck,
} from "@/lib/api/approvals";
import { formatClock } from "@/lib/format";
import { usePolling } from "@/lib/hooks/use-polling";

function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

export function ApprovalBoard() {
  const { stream } = useLive();
  const latest = stream.events.find(
    (event) =>
      event.type.startsWith("approval.") ||
      event.type.startsWith("action.") ||
      event.type.startsWith("verification."),
  );
  const list = usePolling(
    `approvals:${latest?.id ?? 0}`,
    useCallback((signal: AbortSignal) => fetchApprovals("all", 40, signal), []),
    5000,
  );
  const now = useNow();
  const items = list.data?.items ?? [];
  const pending = items.filter((item) => item.status === "PENDING");
  const decided = items.filter((item) => item.status !== "PENDING");
  const [selected, setSelected] = useState<string | null>(null);
  const auditFor = selected ?? decided[0]?.incident_ref ?? pending[0]?.incident_ref ?? null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="size-3.5 text-primary" aria-hidden />
              Pending decisions
              <Badge variant={pending.length ? "warning" : "outline"}>{pending.length}</Badge>
            </CardTitle>
            <CardDescription>
              A gated diagnosis proposes its recommended action here. Approving makes the server
              mint a single-use token and hand it to the executor; no token ever reaches this page.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {list.error ? <p className="text-[11px] text-destructive">{list.error}</p> : null}
          {pending.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              Nothing awaits approval. Investigate an incident on the Agent page; a diagnosis
              above the confidence gate proposes its action here.
            </p>
          ) : (
            pending.map((item) => (
              <ApprovalCard key={item.id} approval={item} now={now} onDone={list.refresh} />
            ))
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <History className="size-3.5 text-primary" aria-hidden />
              Decided
            </CardTitle>
            <CardDescription>
              Every decision with its outcome: who decided, the executed action, snapshots, and
              rollback.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {decided.length === 0 ? (
            <p className="text-xs text-muted-foreground">No decisions yet.</p>
          ) : (
            decided.map((item) => (
              <ApprovalCard
                key={item.id}
                approval={item}
                now={now}
                onDone={list.refresh}
                onSelect={() => setSelected(item.incident_ref)}
              />
            ))
          )}
        </CardContent>
      </Card>

      {auditFor ? <AuditTrail incident={auditFor} refreshKey={latest?.id ?? 0} /> : null}
    </div>
  );
}

function ApprovalCard({
  approval,
  now,
  onDone,
  onSelect,
}: {
  approval: ApprovalView;
  now: number;
  onDone: () => void;
  onSelect?: () => void;
}) {
  const action = approval.action;
  const remaining = (new Date(approval.expires_at).getTime() - now) / 1000;
  const decidable = canDecide(approval, remaining);
  const diagnosis = diagnosisOf(action);
  const codes = citedCodes(action);
  const check = plannedCheck(action);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dry, setDry] = useState<DryRunResult | null>(null);

  const act = async (kind: "approve" | "reject" | "dry") => {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "dry") setDry(await dryRun(approval.id));
      else if (kind === "approve") await approve(approval.id, note);
      else await reject(approval.id, note);
      if (kind !== "dry") onDone();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Request failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <article
      className="rounded-md border border-border p-3 text-xs"
      data-testid={`approval-${approval.id}`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-muted-foreground">#{approval.id}</span>
        <code className="font-mono font-semibold">{action.action_id}</code>
        <span className="text-muted-foreground">→</span>
        <code className="font-mono">{action.target}</code>
        <Badge variant={riskVariant(action.risk)}>risk {action.risk ?? "—"}</Badge>
        <Badge variant="outline">approval {action.approval_mode ?? "—"}</Badge>
        {(action.args as Record<string, unknown>)["override"] ? (
          <Badge variant="warning">operator override</Badge>
        ) : null}
        <Badge variant={approvalVariant(approval.status)}>{approval.status}</Badge>
        <Badge variant={actionVariant(action.status)}>action {action.status}</Badge>
        <span className="ml-auto flex items-center gap-2 text-muted-foreground">
          <Link
            href={`/agent?incident=${approval.incident_ref}`}
            className="font-mono underline-offset-2 hover:underline"
          >
            {approval.incident_ref}
          </Link>
          <span>{approval.incident_status}</span>
          {approval.status === "PENDING" ? (
            <span
              className={remaining < 60 ? "font-mono text-destructive" : "font-mono"}
              aria-label="Time left"
            >
              expires in {ttlLabel(remaining)}
            </span>
          ) : null}
        </span>
      </header>

      <p className="mt-2 text-muted-foreground">
        {action.description} Verification: {action.verification}.
      </p>
      {diagnosis.root_cause ? (
        <p className="mt-1">
          Diagnosis: <strong>{diagnosis.root_cause}</strong> · {diagnosis.fault_class} ·
          confidence {diagnosis.confidence?.toFixed(2)} — {diagnosis.summary}
        </p>
      ) : null}
      {codes.length ? (
        <p className="mt-1 flex flex-wrap gap-1">
          <span className="text-muted-foreground">Based on</span>
          {codes.map((code) => (
            <Link
              key={code}
              href={`/agent?incident=${approval.incident_ref}#evidence-${code}`}
              className="font-mono text-primary underline-offset-2 hover:underline"
            >
              {code}
            </Link>
          ))}
        </p>
      ) : null}

      {check ? <CheckList check={check} title="Planned" /> : null}
      {dry ? (
        <div className="mt-2 rounded border border-border bg-muted/40 p-2">
          <p className="font-medium">
            Dry-run ({formatClock(dry.generated_at)}):{" "}
            {dry.would_execute ? "would execute" : "would be refused"}
          </p>
          <CheckList check={dry.check} title="Now" />
        </div>
      ) : null}

      {approval.status !== "PENDING" ? (
        <div className="mt-2 space-y-1">
          <p>
            {approval.decided_by ? `Decided by ${approval.decided_by}` : "No decision"}
            {approval.decided_at ? ` at ${formatClock(approval.decided_at)}` : ""}
            {approval.note ? ` — “${approval.note}”` : ""} · token{" "}
            {approval.token_issued ? (approval.token_used ? "issued, used once" : "issued") : "never issued"}
          </p>
          {action.error ? <p className="text-destructive">Error: {action.error}</p> : null}
          {action.rollback ? (
            <p>
              Rollback ({String(action.rollback["policy"])}):{" "}
              {String(action.rollback["detail"] ?? "")}
            </p>
          ) : null}
          {action.pre_snapshot || action.post_snapshot ? (
            <details>
              <summary className="cursor-pointer text-muted-foreground">
                Snapshots and result
              </summary>
              <pre className="mt-1 max-h-64 overflow-auto rounded bg-muted/40 p-2 font-mono text-[10px]">
                {JSON.stringify(
                  { before: action.pre_snapshot, after: action.post_snapshot, result: action.result },
                  null,
                  2,
                )}
              </pre>
            </details>
          ) : null}
          {onSelect ? (
            <button
              type="button"
              className="text-primary underline-offset-2 hover:underline"
              onClick={onSelect}
            >
              Show audit trail
            </button>
          ) : null}
        </div>
      ) : null}

      {approval.status === "PENDING" ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Note (optional)"
            maxLength={500}
            className="h-8 min-w-48 flex-1 rounded-md border border-border bg-background px-2"
            aria-label="Decision note"
          />
          <Button size="sm" variant="outline" disabled={!!busy} onClick={() => act("dry")}>
            <FlaskConical className="size-3.5" aria-hidden /> Dry-run
          </Button>
          <Button size="sm" disabled={!decidable || !!busy} onClick={() => act("approve")}>
            <CheckCircle2 className="size-3.5" aria-hidden />
            {busy === "approve" ? "Approving…" : "Approve"}
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={!decidable || !!busy}
            onClick={() => act("reject")}
          >
            <XCircle className="size-3.5" aria-hidden />
            {busy === "reject" ? "Rejecting…" : "Reject"}
          </Button>
        </div>
      ) : null}
      {error ? <p className="mt-2 text-destructive">{error}</p> : null}
    </article>
  );
}

function CheckList({ check, title }: { check: PlanCheck; title: string }) {
  return (
    <div className="mt-2 grid gap-2 md:grid-cols-2">
      <div>
        <p className="font-medium">{title} preconditions</p>
        <ul className="mt-1 space-y-0.5">
          {(check.preconditions ?? []).map((item) => (
            <li key={item.name} className={item.ok ? "text-success" : "text-destructive"}>
              {item.ok ? "✓" : "✗"} <span className="font-mono">{item.name}</span> — {item.detail}
            </li>
          ))}
        </ul>
      </div>
      <div>
        <p className="font-medium">Steps</p>
        <ol className="mt-1 list-decimal space-y-0.5 pl-4">
          {(check.steps ?? []).map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
        <p className="mt-1 text-muted-foreground">
          Rollback: {String(check.rollback?.["policy"] ?? "none")} — {String(check.rollback?.["note"] ?? "")}
        </p>
      </div>
    </div>
  );
}

function AuditTrail({ incident, refreshKey }: { incident: string; refreshKey: number }) {
  const audit = usePolling(
    `audit:${incident}:${refreshKey}`,
    useCallback((signal: AbortSignal) => fetchApprovalAudit(incident, signal), [incident]),
    15000,
  );
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <History className="size-3.5 text-primary" aria-hidden />
            Audit trail · <span className="font-mono">{incident}</span>
          </CardTitle>
          <CardDescription>
            runtime/audit/actions.jsonl — append-only; every plan, decision, dry-run, refusal,
            execution and rollback.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <table className="w-full text-left text-[11px]">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 pr-2 font-medium">At</th>
              <th className="py-1 pr-2 font-medium">Actor</th>
              <th className="py-1 pr-2 font-medium">Event</th>
              <th className="py-1 pr-2 font-medium">Action</th>
              <th className="py-1 pr-2 font-medium">Outcome</th>
              <th className="py-1 font-medium">Detail</th>
            </tr>
          </thead>
          <tbody>
            {(audit.data ?? []).map((entry, index) => (
              <tr key={`${entry.at}-${index}`} className="border-t border-border align-top">
                <td className="py-1 pr-2 font-mono">{formatClock(entry.at)}</td>
                <td className="py-1 pr-2">{entry.actor}</td>
                <td className="py-1 pr-2 font-mono">{entry.event}</td>
                <td className="py-1 pr-2 font-mono">
                  {entry.action_id ?? "—"} → {entry.target ?? "—"}
                </td>
                <td className="py-1 pr-2">{entry.outcome}</td>
                <td className="py-1 break-all">{entry.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
