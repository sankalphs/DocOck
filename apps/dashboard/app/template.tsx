import type { ReactNode } from "react";

/** Re-mounts on every navigation, so each section fades up as it opens. */
export default function Template({ children }: { children: ReactNode }) {
  return <div className="page-enter">{children}</div>;
}
