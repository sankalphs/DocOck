import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-lg py-24 text-center">
      <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
        404
      </p>
      <h1 className="mt-2 text-xl font-semibold">No such section</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        DocOck has ten sections; this address is not one of them.
      </p>
      <Button asChild className="mt-6">
        <Link href="/">Back to overview</Link>
      </Button>
    </div>
  );
}
