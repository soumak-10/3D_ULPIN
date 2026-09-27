import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import { Providers } from "@/providers";
import "./globals.css";

const sans = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

/**
 * A monospace face for identifiers.
 *
 * JetBrains Mono is chosen for its slashed zero and disambiguated l/1/I — a
 * ULPIN is read character by character against a printed deed, and a font that
 * collapses O and 0 turns a transcription into a dispute.
 */
const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "3D ULPIN — Vertical Property Mapping System",
    template: "%s · 3D ULPIN",
  },
  description:
    "Generation and verification of three-dimensional Unique Land Parcel Identification Numbers for individually-owned volumes in multi-storey buildings.",
  applicationName: "3D ULPIN System",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0b1220" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-IN" suppressHydrationWarning>
      <body className={`${sans.variable} ${mono.variable} min-h-screen font-sans antialiased`}>
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
        >
          Skip to content
        </a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
