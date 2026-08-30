import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    // v4b10 (R17). The hand-rolled service worker this replaces precached a fixed SHELL list
    // that contained no `/assets/*`, so Vite's HASHED bundles only entered the cache lazily on
    // first fetch and a genuinely cold offline load failed. Hashed filenames cannot be
    // hard-listed by hand, which is exactly the problem Workbox solves.
    VitePWA({
      // `prompt`, not `autoUpdate`: the old SW called skipWaiting() unconditionally, so a
      // deploy swapped the app under a housekeeper mid-shift. Now they are asked.
      registerType: 'prompt',
      // Registration lives in <PwaPrompts/> (src/components/PwaPrompts.jsx) so the update
      // prompt has something to hook; the plugin's auto-injected snippet would register twice.
      injectRegister: null,
      // No `includeAssets`: everything in public/ is already copied into dist by Vite and
      // picked up by globPatterns below. Listing them again only produced duplicate precache
      // entries for the same files.
      manifest: {
        name: 'Hotel Bhimas — Staff & Admin',
        short_name: 'Bhimas',
        description: 'Front-desk admin, housekeeping and maintenance for Hotel Bhimas',
        // Scope covers the whole origin so /admin is inside the installed app — it was
        // scoped to /staff before, which left the back office outside it entirely. The
        // public marketing/booking pages are the same SPA bundle and are unaffected: nothing
        // offers to install them, because the prompts mount only in the two staff layouts.
        scope: '/',
        start_url: '/admin',
        display: 'standalone',
        background_color: '#0F172A',
        theme_color: '#0F172A',
        icons: [
          { src: '/android-chrome-192x192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/android-chrome-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          // Separate maskable art. The old manifest declared the transparent, edge-to-edge
          // outline above as `maskable`, which Android crops into and renders with no
          // background of its own.
          { src: '/pwa-maskable-192.png', sizes: '192x192', type: 'image/png', purpose: 'maskable' },
          { src: '/pwa-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // The real bundles, hashes and all.
        globPatterns: ['**/*.{js,css,html,ico,png,svg,webmanifest,woff,woff2}'],
        // ...but NOT the marketing photography. Every bundled PNG under assets/ belongs to the
        // public pages (hero shots, room photos, ~30 MB in total); precaching them would make
        // installing the staff app download the whole brochure onto a housekeeper's tablet
        // over hotel wifi. The public pages still load them normally over the network.
        // The 2 MiB per-file limit is deliberately left at its default so a genuinely oversized
        // APP asset still fails the build loudly instead of silently dropping out of the shell.
        globIgnores: ['**/assets/*.{png,jpg,jpeg,webp,avif,gif}'],
        navigateFallback: '/index.html',
        cleanupOutdatedCaches: true,
        clientsClaim: true,
        // Paired with registerType 'prompt' — the new worker waits until the user says yes.
        skipWaiting: false,
        // ⚠️ NO runtimeCaching for the API, deliberately. The backend is a different origin, so
        // Workbox ignores it by default and we keep it that way: a stale room status, folio
        // balance or key-card window is worse than an honest error. Offline reads are not a
        // feature here; the offline BANNER is.
      },
      devOptions: { enabled: false },
    }),
  ],
})
