/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Separate build folder for a local dev server (NEXT_DIST_DIR=.next-local)
  // so it doesn't clobber / get clobbered by the production `.next` build.
  distDir: process.env.NEXT_DIST_DIR || '.next',
  // Allow LAN PCs to load assets when the host machine serves prod.
  // (Dev mode listens on 0.0.0.0 via package.json script.)
  experimental: {
    serverActions: { allowedOrigins: ['*'] },
  },
};

export default nextConfig;
