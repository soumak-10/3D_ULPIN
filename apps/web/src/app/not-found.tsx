import Link from "next/link";
import { FileQuestion } from "lucide-react";

import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-6 text-center">
      <span className="flex size-12 items-center justify-center rounded-full bg-muted">
        <FileQuestion className="size-6 text-muted-foreground" aria-hidden />
      </span>
      <div className="space-y-1.5">
        <h1 className="text-xl font-semibold">Page not found</h1>
        <p className="max-w-md text-sm text-muted-foreground">
          That address does not exist in this register. If you were following a ULPIN
          link, search for the identifier instead — it may have been superseded.
        </p>
      </div>
      <div className="flex gap-2">
        <Button asChild>
          <Link href="/search">Search the register</Link>
        </Button>
        <Button variant="outline" asChild>
          <Link href="/dashboard">Go to dashboard</Link>
        </Button>
      </div>
    </div>
  );
}
