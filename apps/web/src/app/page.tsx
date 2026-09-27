import { redirect } from "next/navigation";

/**
 * The root path is not a landing page.
 *
 * Middleware has already decided whether the visitor has a session; this just
 * sends them to the dashboard, and middleware bounces them to /login if not.
 * One place makes the decision rather than two disagreeing.
 */
export default function RootPage() {
  redirect("/dashboard");
}
