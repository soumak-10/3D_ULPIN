import Link from "next/link";
import { Boxes } from "lucide-react";

/**
 * The unauthenticated shell.
 *
 * Two columns on a desktop: the form on the left at a readable measure, and a
 * panel on the right that says what the system is. A government service that
 * opens on a bare login box gives a first-time user nothing to tell them
 * whether they are in the right place.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <main id="main" className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-md">
          <Link href="/" className="mb-8 flex items-center gap-2.5">
            <span className="flex size-9 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <Boxes className="size-5" aria-hidden />
            </span>
            <span className="leading-tight">
              <span className="block text-sm font-semibold">3D ULPIN System</span>
              <span className="block text-xs text-muted-foreground">
                Vertical Property Mapping
              </span>
            </span>
          </Link>
          {children}
        </div>
      </main>

      <aside className="hidden bg-primary p-12 text-primary-foreground lg:flex lg:flex-col lg:justify-between">
        <div className="max-w-md space-y-5">
          <h2 className="text-3xl font-semibold leading-tight">
            One identifier for every volume of property.
          </h2>
          <p className="text-sm leading-relaxed text-primary-foreground/80">
            India&apos;s Unique Land Parcel Identification Number identifies a parcel of
            ground. A fourteen-storey tower standing on that parcel is one ULPIN and a
            hundred and twelve separately-owned homes. This register extends the
            identifier upward, so each of those homes carries its own — derived from the
            parcel, resolvable back to it, and verifiable on its own terms.
          </p>
          <dl className="grid grid-cols-2 gap-4 pt-2 text-sm">
            <div>
              <dt className="text-primary-foreground/70">Identifier form</dt>
              <dd className="ulpin mt-0.5 font-medium">WB-KOL-B001-F03-U301</dd>
            </div>
            <div>
              <dt className="text-primary-foreground/70">Backed by</dt>
              <dd className="mt-0.5 font-medium">PostGIS 3D geometry</dd>
            </div>
          </dl>
        </div>

        <p className="text-xs text-primary-foreground/60">
          Digital India Land Records Modernisation Programme · Demonstration system.
          Records shown are for evaluation only.
        </p>
      </aside>
    </div>
  );
}
