import Link from "next/link";

import { Mascot } from "@/components/brand/mascot";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-lg py-24 text-center">
      <Mascot mood="unknown" className="mx-auto w-28" label="Ock looking puzzled" />
      <h1 className="mt-6 text-2xl font-bold tracking-tight">No such section</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Ock looked everywhere. DocOck has ten sections, and this address is not one of them.
      </p>
      <Button asChild className="mt-6">
        <Link href="/dashboard">Back to overview</Link>
      </Button>
    </div>
  );
}
