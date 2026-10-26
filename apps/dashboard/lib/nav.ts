import {
  Activity,
  Bot,
  Cpu,
  Database,
  FileText,
  FlaskConical,
  LayoutDashboard,
  Settings,
  ShieldCheck,
  Siren,
  type LucideIcon,
} from "lucide-react";

export type NavItem = {
  href: string;
  label: string;
  /** Short description used by the page header. */
  blurb: string;
  icon: LucideIcon;
  /** Phase that fills the section; P3 renders an honest empty state until then. */
  phase: string;
  ready: boolean;
};

/**
 * The ten sections of PLAN §5, in workflow order:
 * observe → break → detect → investigate → decide → report.
 */
export const NAV_ITEMS: NavItem[] = [
  {
    href: "/",
    label: "Overview",
    blurb: "Stack health, traffic KPIs, and the live event stream.",
    icon: LayoutDashboard,
    phase: "P5",
    ready: true,
  },
  {
    href: "/faults",
    label: "Fault Lab",
    blurb: "Inject and revert the v1 fault catalog with a TTL watchdog.",
    icon: FlaskConical,
    phase: "P7",
    ready: true,
  },
  {
    href: "/telemetry",
    label: "Telemetry",
    blurb: "Metrics, traces, and logs straight from the running demo.",
    icon: Activity,
    phase: "P5",
    ready: true,
  },
  {
    href: "/incidents",
    label: "Incidents",
    blurb: "Detected incidents, evidence, and state transitions.",
    icon: Siren,
    phase: "P8",
    ready: true,
  },
  {
    href: "/agent",
    label: "Agent",
    blurb: "Investigation timeline, tool calls, and the validated diagnosis.",
    icon: Bot,
    phase: "P11–P13",
    ready: false,
  },
  {
    href: "/approvals",
    label: "Approvals",
    blurb: "Human approval of every remediation before it executes.",
    icon: ShieldCheck,
    phase: "P14",
    ready: false,
  },
  {
    href: "/reports",
    label: "Reports",
    blurb: "Self-contained JSON and Markdown incident reports.",
    icon: FileText,
    phase: "P15",
    ready: false,
  },
  {
    href: "/memory",
    label: "Memory",
    blurb: "Similar resolved incidents used as hypotheses, never evidence.",
    icon: Database,
    phase: "P12",
    ready: false,
  },
  {
    href: "/models",
    label: "Models",
    blurb: "Provider, model, embeddings, and thresholds.",
    icon: Cpu,
    phase: "P10",
    ready: false,
  },
  {
    href: "/settings",
    label: "Settings",
    blurb: "Runtime configuration, detector thresholds, and cost guardrails.",
    icon: Settings,
    phase: "P10",
    ready: false,
  },
];

export function navItemFor(pathname: string): NavItem {
  if (pathname === "/") return NAV_ITEMS[0];
  return (
    NAV_ITEMS.find((item) => item.href !== "/" && pathname.startsWith(item.href)) ?? NAV_ITEMS[0]
  );
}
