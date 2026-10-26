import { Construction } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type PhaseNoticeProps = {
  phase: string;
  title: string;
  description: string;
  capabilities: string[];
};

/**
 * Honest empty state. Nothing here is fabricated: the page states which phase
 * brings the real data and exactly what will appear.
 */
export function PhaseNotice({ phase, title, description, capabilities }: PhaseNoticeProps) {
  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <div className="flex items-center gap-3">
          <div className="grid size-9 place-items-center rounded-lg border border-border bg-muted/50 text-muted-foreground">
            <Construction className="size-4" aria-hidden />
          </div>
          <div>
            <CardTitle className="text-base">{title}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">{description}</p>
          </div>
        </div>
        <Badge variant="outline">planned · {phase}</Badge>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2 text-sm text-muted-foreground">
          {capabilities.map((capability) => (
            <li key={capability} className="flex gap-2">
              <span className="mt-2 size-1 shrink-0 rounded-full bg-muted-foreground/70" />
              <span>{capability}</span>
            </li>
          ))}
        </ul>
        <p className="mt-4 border-t border-border/60 pt-3 text-xs text-muted-foreground">
          This page renders no simulated data. It stays empty until the phase lands
          and the API behind it returns real telemetry.
        </p>
      </CardContent>
    </Card>
  );
}
