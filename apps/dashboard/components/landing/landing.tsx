"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { ArrowRight } from "lucide-react";

import { Mascot, moodFor } from "@/components/brand/mascot";
import { API_BASE_URL } from "@/lib/api/base-url";
import { useHealth } from "@/lib/hooks/use-health";
import { cn } from "@/lib/utils";

const STAGES = [
  { name: "Break", text: "Inject a fault from the catalog. A watchdog reverts it when the timer ends." },
  { name: "Detect", text: "Baselines learn normal traffic and open an incident when it drifts." },
  { name: "Investigate", text: "Ock reads metrics, traces and logs, then names a cause with evidence." },
  { name: "Approve", text: "Every fix waits for your yes. Nothing runs on its own." },
  { name: "Report", text: "Each incident closes with a self-contained JSON and Markdown report." },
];

const POKE_LINES = [
  "Beep. Hello!",
  "That tickles.",
  "I read traces for fun.",
  "Go on, inject a fault.",
  "I never fix anything without asking.",
];

function stagger(index: number): CSSProperties {
  return { "--i": index } as CSSProperties;
}

export function Landing() {
  const health = useHealth(10000);
  const [pokeLine, setPokeLine] = useState<string | null>(null);
  const pokes = useRef(0);
  const pokeTimer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(pokeTimer.current), []);

  const report = health.data;
  const mood = moodFor(report?.status ?? (health.error ? "down" : null));
  const deps = report?.dependencies ?? [];
  const failing = deps.filter((dep) => dep.status !== "ok");

  let status: string;
  if (report) {
    if (failing.length === 0) status = `All ${deps.length} checks are healthy. I'm just watching.`;
    else
      status = `${failing.map((dep) => dep.name).join(", ")} ${failing.length === 1 ? "needs" : "need"} a look.`;
  } else if (health.error) {
    status = "I can't reach the API. Start it and I'll check again.";
  } else {
    status = "Checking on the stack…";
  }

  const poke = () => {
    setPokeLine(POKE_LINES[pokes.current % POKE_LINES.length]);
    pokes.current += 1;
    window.clearTimeout(pokeTimer.current);
    pokeTimer.current = window.setTimeout(() => setPokeLine(null), 2400);
  };

  const bubble = pokeLine ?? status;

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="mx-auto flex w-full max-w-[1280px] items-center gap-3 px-5 py-5 sm:px-8">
        <Link href="/" className="group flex items-center gap-2.5 rounded-lg" aria-label="DocOck home">
          <Mascot
            variant="head"
            mood={mood}
            className="size-10 transition-transform duration-300 ease-[var(--ease-out)] group-hover:-rotate-6"
          />
          <span className="text-lg font-bold tracking-tight">DocOck</span>
        </Link>
        <Link
          href="/dashboard"
          className="press -mr-4 ml-auto rounded-full px-4 py-2 text-sm font-semibold text-foreground hover:bg-accent"
        >
          Dashboard
        </Link>
      </header>

      <main className="mx-auto grid w-full max-w-[1280px] flex-1 items-center gap-10 px-5 pb-10 sm:px-8 lg:grid-cols-[1.05fr_0.95fr] lg:gap-16">
        <div className="max-w-xl">
          <h1
            className="rise-in text-[2.6rem] font-extrabold leading-[1.02] tracking-[-0.035em] sm:text-6xl lg:text-[4.25rem]"
            style={stagger(0)}
          >
            Break the stack. Ock finds why.
          </h1>
          <p className="rise-in mt-6 max-w-[36rem] text-pretty text-base leading-relaxed sm:mt-7 sm:text-lg" style={stagger(1)}>
            DocOck is a local incident-response lab. Inject a fault into the OpenTelemetry demo,
            watch the real telemetry, and let Ock investigate with evidence. Nothing gets fixed
            until you approve it.
          </p>
          <div className="rise-in mt-8 flex flex-wrap items-center gap-x-5 gap-y-3" style={stagger(2)}>
            <Link
              href="/dashboard"
              className="press group inline-flex h-13 items-center gap-2.5 rounded-full bg-foreground px-7 text-base font-semibold text-background hover:bg-[#2c3828]"
            >
              Open the dashboard
              <ArrowRight
                className="size-4 transition-transform duration-300 ease-[var(--ease-out)] group-hover:translate-x-1"
                aria-hidden
              />
            </Link>
            <span className="text-sm text-muted-foreground">Runs on your machine. No keys in the browser.</span>
          </div>
        </div>

        <section
          aria-label="Ock's live check of the stack"
          className="rise-in relative flex min-h-[26rem] flex-col items-center justify-end overflow-hidden rounded-[2.25rem] bg-leaf px-6 pb-8 pt-28 sm:min-h-[34rem]"
          style={stagger(1)}
        >
          <div
            role="status"
            aria-live="polite"
            className="absolute left-1/2 top-7 w-[min(22rem,calc(100%-3rem))] -translate-x-1/2 rounded-3xl border-2 border-foreground bg-card px-5 py-3.5 text-center text-[15px] font-medium leading-snug"
          >
            <span key={bubble} className="page-enter block">
              {bubble}
            </span>
            <span
              aria-hidden
              className="absolute -bottom-[9px] left-1/2 size-4 -translate-x-1/2 rotate-45 border-b-2 border-r-2 border-foreground bg-card"
            />
          </div>

          <Mascot mood={mood} track onPoke={poke} className="w-52 sm:w-64 lg:w-72" />

          <ul className="mt-6 flex max-w-md flex-wrap justify-center gap-1.5" aria-label="Dependencies">
            {deps.length
              ? deps.map((dep) => (
                  <li
                    key={dep.name}
                    title={dep.detail}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-colors duration-300",
                      dep.status === "ok" ? "bg-mint text-foreground" : "bg-blush text-destructive",
                    )}
                  >
                    <span
                      className={cn(
                        "size-1.5 rounded-full",
                        dep.status === "ok" ? "bg-primary" : "bg-destructive",
                      )}
                      aria-hidden
                    />
                    {dep.name}
                  </li>
                ))
              : (
                  <li className="text-sm text-foreground/80">
                    {health.error ? `API at ${API_BASE_URL}` : "Probing dependencies…"}
                  </li>
                )}
          </ul>
          <p className="mt-3 text-sm text-foreground/80">Tap Ock to say hello.</p>
        </section>
      </main>

      <section aria-labelledby="loop-title" className="bg-card">
        <div className="mx-auto w-full max-w-[1280px] px-5 py-10 sm:px-8">
          <h2 id="loop-title" className="text-xl font-bold tracking-tight">
            One incident, start to finish
          </h2>
          <ol className="mt-6 grid gap-x-8 gap-y-6 sm:grid-cols-2 lg:grid-cols-5">
            {STAGES.map((stage, index) => (
              <li key={stage.name} className="rise-in" style={stagger(index + 3)}>
                <p className="text-base font-bold">{stage.name}</p>
                <p className="mt-1.5 text-base leading-relaxed">{stage.text}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>
    </div>
  );
}
