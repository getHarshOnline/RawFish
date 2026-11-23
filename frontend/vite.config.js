import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
      '@locales': path.resolve(__dirname, '../locales')
    }
  },
  // The graph-layout worker is imported as an ES module, so Vite must
  // emit workers in ES format for both local development and production.
  worker: {
    format: 'es',
  },
  // Test files live beside the code they protect, which keeps cockpit,
  // graph, utility, and worker behavior visible during refactors.
  test: {
    environment: 'happy-dom',
    globals: true,
    include: [
      'src/__tests__/**/*.{test,spec}.{js,mjs,ts}',
      'src/cockpit/__tests__/**/*.{test,spec}.{js,mjs,ts}',
      'src/components/__tests__/**/*.{test,spec}.{js,mjs,ts}',
      'src/i18n/__tests__/**/*.{test,spec}.{js,mjs,ts}',
      'src/utils/__tests__/**/*.{test,spec}.{js,mjs,ts}',
      'src/workers/__tests__/**/*.{test,spec}.{js,mjs,ts}'
    ],
    exclude: [
      // Retired policy/wizard suites were deleted in the RawFish cutover.
      // Keep excludes limited to generated/dependency folders so active
      // cockpit tests remain visible.
      'node_modules/**',
      'dist/**'
    ],
    setupFiles: [],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary', 'html'],
      include: [
        'src/api/**/*.js',
        'src/cockpit/**/*.js',
        'src/cockpit/**/*.vue',
        'src/router/index.js',
        'src/store/**/*.js',
        'src/utils/**/*.js',
        'src/workers/**/*.js'
      ],
      thresholds: {
        lines: 70,
        functions: 50,
        statements: 70,
        branches: 60
      }
    }
  },
  server: {
    port: 3000,
    open: false,
    proxy: {
      '/api': {
        target: 'http://localhost:5001',
        changeOrigin: true,
        secure: false
      }
    }
  },
  // The Docker image serves built assets with `vite preview`; preview has
  // its own host and proxy settings, separate from the dev server.
  preview: {
    host: '0.0.0.0',
    port: 3000,
    open: false,
    // Keep frame-ancestor protection in response headers, where browsers enforce it.
    headers: {
      'X-Frame-Options': 'DENY',
      'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'strict-origin-when-cross-origin',
      'Content-Security-Policy': "frame-ancestors 'none'"
    },
    proxy: {
      '/api': {
        target: 'http://localhost:5001',
        changeOrigin: true,
        secure: false
      }
    }
  }
})
