import { cn } from "@/lib/utils";

function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("animate-pulse rounded-md bg-muted", className)} {...props} />;
}

/**
 * A table body of skeleton rows. Emits bare `<tr>`, so it is only valid inside
 * a `<tbody>` — a list of cards wants `SkeletonCards` instead.
 */
function SkeletonRows({
  rows = 5,
  cols = 4,
  className,
}: {
  rows?: number;
  cols?: number;
  className?: string;
}) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} className={cn("border-b", className)}>
          {Array.from({ length: cols }).map((__, c) => (
            <td key={c} className="px-3 py-3">
              <Skeleton className="h-4 w-full max-w-[12rem]" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

/** Block placeholders sized to the cards they stand in for. */
function SkeletonCards({ count = 4, className }: { count?: number; className?: string }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} className={cn("h-24 w-full", className)} />
      ))}
    </div>
  );
}

export { Skeleton, SkeletonCards, SkeletonRows };
