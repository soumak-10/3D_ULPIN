import type { NextConfig } from 'next';

const API_ORIGIN = process.env.API_ORIGIN ?? 'http://localhost:8000';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,

  // The browser talks to /api/v1 on its own origin and Next proxies to FastAPI.
  // This is what lets the refresh token live in a SameSite=Lax HttpOnly cookie:
  // a cross-origin API would need SameSite=None, and a cookie that travels on
  // third-party requests is exactly what SameSite exists to prevent.
  async rewrites() {
    return [{ source: '/api/v1/:path*', destination: `${API_ORIGIN}/api/v1/:path*` }];
  },

  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'X-Frame-Options', value: 'DENY' },
          {
            key: 'Permissions-Policy',
            value: 'geolocation=(self), camera=(), microphone=()',
          },
        ],
      },
    ];
  },

  typescript: { ignoreBuildErrors: false },
  eslint: { ignoreDuringBuilds: false },
};

export default nextConfig;
