"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { Check, Copy, Download, FileJson, FileText, ListChecks } from "lucide-react";

import { useLive } from "@/components/live-provider";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  beforeAfter,
  cleanProgress,
  fetchReport,
  fetchReports,
  percent,
  reportUrl,
  verdictVariant,
  type IncidentReport,
  type ReportAction,
} from "@/lib/api/reports";
import { severityBadge } from "@/components/incidents/badges";
import { formatClock, formatMetric } from "@/lib/format";
import { usePolling } from "@/lib/hooks/use-polling";

export function ReportViewer({ initialIncident }: { initialIncident: string | null }) {
  const { stream } = useLive();
  const latest = stream.events.find(
    (event) =>
      event.type.startsWith("verification.") ||
      event.type === "report.generated" ||
      event.type === "incident.transition",
  );
  const list = usePolling(
    `reports:${latest?.id ?? 0}`,
    useCallback((signal: AbortSignal) => fetchReports(40, signal), []),
    10000,
  );
  const items = list.data?.items ?? [];
  const [chosen, setChosen] = useState<string | null>(initialIncident);
  const selected =
    chosen ?? items.find((item) => item.verdict)?.incident_ref ?? items[0]?.incident_ref ?? null;

  return (
    <div className="grid gap-4 xl:grid-cols-[360px_1fr]">
      <Card className="self-start">
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <ListChecks className="size-3.5 text-primary" aria-hidden />
              Incidents
            </CardTitle>
            <CardDescription>
              A closed incident&apos;s report is stored when the verifier concludes; an open one is
              built live.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-1">
          {list.error ? <p className="text-[11px] text-destructive">{list.error}</p> : null}
          {items.length === 0 ? (
            <p className="text-xs text-muted-foreground">No incidents yet.</p>
          ) : (
            items.map((item) => (
              <button
                key={item.incident_ref}
                type="button"
                onClick={() => {
                  setChosen(item.incident_ref);
                  window.history.replaceState(null, "", `/reports?incident=${item.incident_ref}`);
                }}
                className={`w-full rounded-md border px-2.5 py-2 text-left text-xs transition-colors ${
                  item.incident_ref === selected
                    ? "border-primary/60 bg-primary/10"
                    : "border-transparent hover:bg-accent"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono">{item.incident_ref}</span>
                  <Badge variant="outline">{item.status}</Badge>
                  {item.verdict ? (
                    <Badge variant={verdictVariant(item.verdict)}>{item.verdict}</Badge>
                  ) : null}
                  {item.stored_at ? (
                    <span className="ml-auto text-[10px] text-muted-foreground">stored</span>
                  ) : null}
                </div>
                <p className="mt-1 truncate text-muted-foreground">{item.title}</p>
                {item.action ? (
                  <p className="font-mono text-[11px] text-muted-foreground">{item.action}</p>
                ) : null}
              </button>
            ))
          )}
        </CardContent>
      </Card>
      {selected ? <ReportPanel key={selected} incident={selected} refreshKey={latest?.id ?? 0} /> : null}
    </div>
  );
}

function ReportPanel({ incident, refreshKey }: { incident: string; refreshKey: number }) {
  const report = usePolling(
    `report:${incident}:${refreshKey}`,
    useCallback((signal: AbortSignal) => fetchReport(incident, signal), [incident]),
    15000,
  );
  const data = report.data;
  if (report.error && !data) {
    return <p className="text-xs text-destructive">{report.error}</p>;
  }
  if (!data) {
    return <p className="text-xs text-muted-foreground">Loading {incident}…</p>;
  }
  return (
    <div className="min-w-0 space-y-4">
      <Header report={data} />
      {data.remediation.map((item) => (
        <ActionCard key={item.action.id} item={item} />
      ))}
      <DiagnosisCard report={data} />
      <TimelineCard report={data} />
      <AuditCard report={data} />
    </div>
  );
}

function CopyMarkdown({ incident }: { incident: string }) {
  const [state, setState] = useState<"idle" | "copied" | "error">("idle");
  return (
    <Button
      size="sm"
      variant="outline"
      onClick={async () => {
        try {
          const response = await fetch(reportUrl(incident, "md"));
          await navigator.clipboard.writeText(await response.text());
          setState("copied");
        } catch {
          setState("error");
        }
        setTimeout(() => setState("idle"), 2000);
      }}
    >
      {state === "copied" ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      {state === "error" ? "Copy failed" : "Copy Markdown"}
    </Button>
  );
}

function Header({ report }: { report: IncidentReport }) {
  const { incident, outcome } = report;
  return (
    <Card>
      <CardHeader>
        <div className="min-w-0 space-y-1">
          <CardTitle className="flex flex-wrap items-center gap-2">
            <FileText className="size-3.5 text-primary" aria-hidden />
            <span className="font-mono">{incident.ref}</span>
            <Badge variant={severityBadge(incident.severity)}>{incident.severity}</Badge>
            <Badge variant="outline">{incident.status}</Badge>
            {outcome.verdict ? (
              <Badge variant={verdictVariant(outcome.verdict)}>
                verification {outcome.verdict}
              </Badge>
            ) : null}
          </CardTitle>
          <CardDescription className="break-words">{incident.title}</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs md:grid-cols-4">
          <Field label="Opened" value={formatClock(incident.opened_at)} />
          <Field label="Closed" value={formatClock(incident.closed_at)} />
          <Field
            label="Duration"
            value={incident.duration_s != null ? `${incident.duration_s} s` : "open"}
          />
          <Field label="Closed by" value={outcome.closed_by ?? "—"} />
          <Field label="Action" value={outcome.action ?? "none"} mono />
          <Field
            label="Observed"
            value={outcome.observed_for_s != null ? `${outcome.observed_for_s} s` : "—"}
          />
          <Field label="Primary service" value={incident.primary_service ?? "—"} mono />
          <Field label="Generated" value={formatClock(report.generated_at)} />
        </dl>
        <div className="flex flex-wrap gap-2">
          <a
            className={buttonVariants({ size: "sm", variant: "outline" })}
            href={reportUrl(incident.ref, "md", true)}
          >
            <Download className="size-3.5" /> Markdown
          </a>
          <a
            className={buttonVariants({ size: "sm", variant: "outline" })}
            href={reportUrl(incident.ref, "json")}
            target="_blank"
            rel="noreferrer"
          >
            <FileJson className="size-3.5" /> JSON
          </a>
          <CopyMarkdown incident={incident.ref} />
          <Link
            className={buttonVariants({ size: "sm", variant: "ghost" })}
            href={`/agent?incident=${incident.ref}`}
          >
            Open in Agent
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className={`truncate ${mono ? "font-mono" : ""}`}>{value}</dd>
    </div>
  );
}

function ActionCard({ item }: { item: ReportAction }) {
  const { action, approval, verification } = item;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex flex-wrap items-center gap-2">
            <span className="font-mono">
              #{action.id} {action.action_id} → {action.target}
            </span>
            <Badge variant="outline">{action.status}</Badge>
            {approval ? <Badge variant="outline">approval {approval.status}</Badge> : null}
            {verification ? (
              <Badge variant={verdictVariant(verification.verdict)}>
                {verification.verdict} after {verification.observed_for_s} s
              </Badge>
            ) : null}
          </CardTitle>
          <CardDescription>
            {approval
              ? `Decided by ${approval.decided_by ?? "—"} at ${formatClock(approval.decided_at)}` +
                (approval.note ? ` — “${approval.note}”` : "") +
                ` · token ${approval.token_issued ? (approval.token_used ? "issued, used once" : "issued, unused") : "never issued"}`
              : "No approval record."}
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        {action.error ? <p className="text-destructive">Error: {action.error}</p> : null}
        {verification ? (
          <>
            <p className="text-muted-foreground">
              Focus: {(verification.criteria.focus ?? []).join(", ") || "—"} ·{" "}
              {cleanProgress(verification)} · timeout {verification.criteria.timeout_s ?? "—"} s
              {verification.criteria.rollback
                ? ` · rollback: ${String(verification.criteria.rollback.detail ?? "")}`
                : ""}
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Criterion</TableHead>
                  <TableHead>Result</TableHead>
                  <TableHead>Detail</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(verification.criteria.final ?? []).map((criterion) => (
                  <TableRow key={criterion.id}>
                    <TableCell>{criterion.label}</TableCell>
                    <TableCell>
                      <Badge variant={criterion.ok ? "success" : "destructive"}>
                        {criterion.ok ? "pass" : "fail"}
                      </Badge>
                    </TableCell>
                    <TableCell className="whitespace-normal break-words">{criterion.detail}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Service</TableHead>
                  <TableHead>Error rate before → after</TableHead>
                  <TableHead>p95 before → after</TableHead>
                  <TableHead>Throughput before → after</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {beforeAfter(verification).map((row) => (
                  <TableRow key={row.service}>
                    <TableCell className="font-mono">{row.service}</TableCell>
                    <TableCell>
                      {percent(row.before.error_rate)} → {percent(row.after.error_rate)}
                    </TableCell>
                    <TableCell>
                      {formatMetric(row.before.p95_latency ?? null, "ms")} →{" "}
                      {formatMetric(row.after.p95_latency ?? null, "ms")}
                    </TableCell>
                    <TableCell>
                      {formatMetric(row.before.throughput ?? null, "rps")} →{" "}
                      {formatMetric(row.after.throughput ?? null, "rps")}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <SampleStrip samples={verification.after.samples ?? []} />
          </>
        ) : (
          <p className="text-muted-foreground">
            {action.status === "EXECUTED"
              ? "No verdict yet: the verifier is still watching, or the action ran before P15."
              : "Not verified: the action did not execute."}
          </p>
        )}
        {(action.result?.steps ?? []).length ? (
          <details>
            <summary className="cursor-pointer text-muted-foreground">Executed steps</summary>
            <pre className="mt-1 overflow-x-auto rounded bg-muted/40 p-2 text-[11px]">
              {JSON.stringify(action.result?.steps, null, 2)}
            </pre>
          </details>
        ) : null}
      </CardContent>
    </Card>
  );
}

function SampleStrip({
  samples,
}: {
  samples: { at: string; clean: number; failing?: string[]; stale?: boolean }[];
}) {
  if (!samples.length) return null;
  return (
    <div>
      <p className="mb-1 text-muted-foreground">
        Detector cycles after the action ({samples.length}): green = every criterion held
      </p>
      <div className="flex flex-wrap gap-0.5" aria-label="Verification samples">
        {samples.map((sample, index) => (
          <span
            key={`${sample.at}-${index}`}
            title={`${formatClock(sample.at)} ${sample.stale ? "stale" : sample.failing?.length ? `failing: ${sample.failing.join(", ")}` : `clean ${sample.clean}`}`}
            className={`h-3 w-2 rounded-sm ${
              sample.stale
                ? "bg-muted"
                : sample.failing?.length
                  ? "bg-destructive/70"
                  : "bg-leaf-strong"
            }`}
          />
        ))}
      </div>
    </div>
  );
}

function DiagnosisCard({ report }: { report: IncidentReport }) {
  const { diagnosis, evidence } = report;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Diagnosis and cited evidence</CardTitle>
          <CardDescription>
            {diagnosis
              ? `${diagnosis.root_cause_service} · ${diagnosis.fault_class} · confidence ${diagnosis.confidence.toFixed(2)}`
              : "No diagnosis was produced."}
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        {diagnosis ? <p className="leading-relaxed">{diagnosis.summary}</p> : null}
        {evidence.missing_citations.length ? (
          <p className="text-destructive">Missing citations: {evidence.missing_citations.join(", ")}</p>
        ) : null}
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Code</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Service</TableHead>
              <TableHead>Summary</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {evidence.cited.map((row) => (
              <TableRow key={row.code}>
                <TableCell className="font-mono">
                  <Link
                    className="text-primary hover:underline"
                    href={`/agent?incident=${report.incident.ref}#evidence-${row.code}`}
                  >
                    {row.code}
                  </Link>
                </TableCell>
                <TableCell>{row.type}</TableCell>
                <TableCell className="font-mono">{row.service ?? "—"}</TableCell>
                <TableCell className="whitespace-normal break-words">{row.summary}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function TimelineCard({ report }: { report: IncidentReport }) {
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Timeline</CardTitle>
          <CardDescription>Every incident event, in order.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <ol className="space-y-1 text-xs" aria-label="Report timeline">
          {report.timeline.map((event) => (
            <li key={event.seq} className="grid grid-cols-[3rem_5.5rem_9rem_1fr] gap-2">
              <span className="text-muted-foreground">#{event.seq}</span>
              <span className="font-mono text-muted-foreground">{formatClock(event.at)}</span>
              <span className="font-mono">{event.kind}</span>
              <span className="break-words">{event.detail}</span>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

function AuditCard({ report }: { report: IncidentReport }) {
  if (!report.audit.length) return null;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Audit trail</CardTitle>
          <CardDescription>From runtime/audit/actions.jsonl, captured in the report.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>At</TableHead>
              <TableHead>Actor</TableHead>
              <TableHead>Event</TableHead>
              <TableHead>Outcome</TableHead>
              <TableHead>Detail</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {report.audit.map((entry, index) => (
              <TableRow key={`${entry.at}-${index}`}>
                <TableCell className="font-mono">{formatClock(entry.at)}</TableCell>
                <TableCell>{entry.actor}</TableCell>
                <TableCell>{entry.event}</TableCell>
                <TableCell>{entry.outcome}</TableCell>
                <TableCell className="whitespace-normal break-words">{entry.detail}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
