import { NextResponse, type NextRequest } from "next/server";

/**
 * Route gating at the edge.
 *
 * What this can and cannot do is worth being precise about, because it is easy
 * to mistake for authorisation.
 *
 * It reads `ulpin_role`, a readable, non-secret hint written by the auth
 * provider at login and cleared at logout. Its presence means a session
 * probably exists and its value says which shell to route to. It is not
 * verified here — the cookie is user-editable.
 *
 * It deliberately does *not* read `ulpin_rt`, the httpOnly refresh token, even
 * though that would seem the more trustworthy signal. The API scopes that
 * cookie to `Path=/api/v1/auth`, so the browser does not send it on a
 * navigation to `/dashboard` and middleware cannot see it — gating on it
 * bounced every signed-in user straight back to `/login`, forever. Widening
 * its Path to `/` would fix the loop by attaching the refresh token to every
 * page load, image and script request instead, which is the wrong trade: the
 * token stays narrow and navigation keys off the hint.
 *
 * So this is navigation, not security. It saves an authenticated user from a
 * flash of the login page and saves an owner from loading the fraud dashboard
 * shell only to have every query return 403. The actual decision is taken by
 * FastAPI on every request, which is where it belongs: a middleware check that
 * the client can edit is a suggestion.
 */

const PUBLIC_PATHS = [
  "/login",
  "/register",
  "/forgot-password",
  "/reset-password",
  "/verify-email",
];

/** Routes only Admin and Property Officer should be routed to. */
const STAFF_PREFIXES = ["/fraud", "/verification", "/ulpins", "/owners", "/buildings/new"];

const ADMIN_PREFIXES = ["/admin"];

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  const role = request.cookies.get("ulpin_role")?.value ?? null;
  // The hint's lifetime is set to match the refresh token's, so it expiring is
  // a fair proxy for the session having expired.
  const hasSession = Boolean(role);

  const isPublic = PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));

  // Signed in, sitting on the login page: send them where they were going.
  if (isPublic && hasSession) {
    const next = request.nextUrl.searchParams.get("next");
    const url = request.nextUrl.clone();
    url.search = "";
    url.pathname = next && next.startsWith("/") ? next : "/dashboard";
    return NextResponse.redirect(url);
  }

  if (isPublic) return NextResponse.next();

  if (!hasSession) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    // Preserve the whole destination including its query, so a bookmarked
    // search survives the detour through the login page.
    url.search = `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(url);
  }

  const needsStaff = STAFF_PREFIXES.some((p) => pathname.startsWith(p));
  const needsAdmin = ADMIN_PREFIXES.some((p) => pathname.startsWith(p));

  // Only redirect when the hint says the user definitely lacks the role. A
  // missing cookie falls through to the page, which will render the API's own
  // 403 — better than bouncing a legitimate officer whose hint cookie expired.
  if (needsAdmin && role && role !== "ADMIN") {
    return NextResponse.redirect(new URL("/dashboard?denied=admin", request.url));
  }

  if (needsStaff && role && role !== "ADMIN" && role !== "PROPERTY_OFFICER") {
    return NextResponse.redirect(new URL("/dashboard?denied=staff", request.url));
  }

  return NextResponse.next();
}

export const config = {
  // Everything except Next's own assets, the API proxy, and files with an
  // extension. Matching /api would break the refresh call this depends on.
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\.).*)"],
};
