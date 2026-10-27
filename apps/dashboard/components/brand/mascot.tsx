"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

export type OckMood = "ok" | "degraded" | "down" | "unknown";

const INK = "#1c2419";

const MOOD: Record<OckMood, { bulb: string; eyes: string; mouth: string }> = {
  ok: { bulb: "var(--leaf)", eyes: "var(--leaf)", mouth: "M90 113 q10 9 20 0" },
  degraded: { bulb: "#ffd27a", eyes: "var(--leaf)", mouth: "M91 116 h18" },
  down: { bulb: "var(--rose)", eyes: "var(--rose)", mouth: "M90 119 q10 -8 20 0" },
  unknown: { bulb: "#cfdcc3", eyes: "var(--leaf)", mouth: "M91 116 h18" },
};

/** Maps an API health status onto one of Ock's four faces. */
export function moodFor(status: string | null | undefined): OckMood {
  if (status === "ok" || status === "degraded" || status === "down") return status;
  return "unknown";
}

type MascotProps = {
  mood?: OckMood;
  /** "full" draws the whole robot; "head" is the compact logo. */
  variant?: "full" | "head";
  /** Eyes follow the pointer across the window. */
  track?: boolean;
  /** Click or keyboard poke: squash, wave and a happy face. */
  onPoke?: () => void;
  className?: string;
  label?: string;
};

/**
 * Ock, DocOck's tiny on-call robot. All motion is CSS (globals.css, `.ock-*`)
 * on transform and opacity, and stops under prefers-reduced-motion.
 */
export function Mascot({
  mood = "ok",
  variant = "full",
  track = false,
  onPoke,
  className,
  label = "Ock, the DocOck robot",
}: MascotProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [poked, setPoked] = useState(false);
  const pokeTimer = useRef<number | undefined>(undefined);
  const face = MOOD[mood];
  const interactive = Boolean(onPoke);

  useEffect(() => {
    if (!track) return;
    const svg = svgRef.current;
    if (!svg) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let frame = 0;
    const onMove = (event: PointerEvent) => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const box = svg.getBoundingClientRect();
        const dx = event.clientX - (box.left + box.width / 2);
        const dy = event.clientY - (box.top + box.height * 0.4);
        const distance = Math.hypot(dx, dy) || 1;
        const reach = Math.min(distance / 260, 1);
        svg.style.setProperty("--look-x", `${((dx / distance) * 7 * reach).toFixed(2)}px`);
        svg.style.setProperty("--look-y", `${((dy / distance) * 5 * reach).toFixed(2)}px`);
      });
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", onMove);
    };
  }, [track]);

  useEffect(() => () => window.clearTimeout(pokeTimer.current), []);

  const poke = useCallback(() => {
    onPoke?.();
    // Drop the attribute for a frame so a second poke restarts the animation.
    setPoked(false);
    requestAnimationFrame(() => requestAnimationFrame(() => setPoked(true)));
    window.clearTimeout(pokeTimer.current);
    pokeTimer.current = window.setTimeout(() => setPoked(false), 1000);
  }, [onPoke]);

  const head = (
    <g className="ock-head">
      <g className="ock-antenna">
        <line x1="100" y1="46" x2="100" y2="22" stroke={INK} strokeWidth="5" strokeLinecap="round" />
        <circle
          className="ock-bulb"
          cx="100"
          cy="18"
          r="10"
          fill={face.bulb}
          stroke={INK}
          strokeWidth="4"
        />
      </g>
      <rect x="22" y="80" width="16" height="34" rx="8" fill="var(--rose)" stroke={INK} strokeWidth="4" />
      <rect x="162" y="80" width="16" height="34" rx="8" fill="var(--rose)" stroke={INK} strokeWidth="4" />
      <rect x="34" y="44" width="132" height="104" rx="40" fill="var(--mint)" stroke={INK} strokeWidth="5" />
      <rect x="50" y="62" width="100" height="68" rx="26" fill={INK} />
      <circle cx="64" cy="116" r="6" fill="var(--rose)" opacity="0.85" />
      <circle cx="136" cy="116" r="6" fill="var(--rose)" opacity="0.85" />

      <g className="ock-eyes">
        <g className="ock-face" opacity={poked ? 0 : 1}>
          <rect className="ock-eye" x="74" y="80" width="15" height="22" rx="7.5" fill={face.eyes} />
          <rect className="ock-eye" x="111" y="80" width="15" height="22" rx="7.5" fill={face.eyes} />
        </g>
        <g
          className="ock-face"
          opacity={poked ? 1 : 0}
          fill="none"
          stroke="var(--leaf)"
          strokeWidth="5"
          strokeLinecap="round"
        >
          <path d="M73 96 q8.5 -12 17 0" />
          <path d="M110 96 q8.5 -12 17 0" />
        </g>
      </g>
      <path
        d={poked ? MOOD.ok.mouth : face.mouth}
        fill="none"
        stroke={poked ? "var(--leaf)" : face.eyes}
        strokeWidth="4"
        strokeLinecap="round"
      />
    </g>
  );

  const svg = (
    <svg
      ref={svgRef}
      viewBox={variant === "head" ? "18 0 164 156" : "0 0 200 240"}
      className={cn("ock", className)}
      data-mood={mood}
      data-poked={poked}
      data-interactive={interactive}
      role={interactive ? undefined : "img"}
      aria-label={interactive ? undefined : label}
      aria-hidden={interactive ? true : undefined}
    >
      {variant === "head" ? (
        head
      ) : (
        <>
          <ellipse className="ock-shadow" cx="100" cy="230" rx="46" ry="7" fill={INK} opacity="0.5" />
          <g className="ock-float">
            <g className="ock-body">
              <rect x="38" y="152" width="20" height="44" rx="10" fill="var(--leaf)" stroke={INK} strokeWidth="4" />
              <rect
                className="ock-arm-right"
                x="142"
                y="152"
                width="20"
                height="44"
                rx="10"
                fill="var(--leaf)"
                stroke={INK}
                strokeWidth="4"
              />
              <rect x="74" y="200" width="20" height="16" rx="8" fill={INK} />
              <rect x="106" y="200" width="20" height="16" rx="8" fill={INK} />
              <rect x="56" y="144" width="88" height="64" rx="24" fill="var(--leaf)" stroke={INK} strokeWidth="5" />
              <rect x="72" y="158" width="56" height="32" rx="10" fill={INK} />
              <polyline
                className="ock-pulse"
                points="77,175 89,175 94,166 100,184 106,170 111,175 123,175"
                fill="none"
                stroke={mood === "down" ? "var(--rose)" : "var(--leaf)"}
                strokeWidth="3"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              {head}
            </g>
          </g>
        </>
      )}
    </svg>
  );

  if (!interactive) return svg;

  return (
    <button
      type="button"
      onClick={poke}
      aria-label={`Poke ${label}`}
      className="cursor-pointer rounded-[2rem] outline-offset-8 [-webkit-tap-highlight-color:transparent]"
    >
      {svg}
    </button>
  );
}
