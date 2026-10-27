import type { Metadata } from "next";

import { Landing } from "@/components/landing/landing";

export const metadata: Metadata = {
  title: { absolute: "DocOck — Break the stack. Ock finds why." },
};

export default function LandingPage() {
  return <Landing />;
}
