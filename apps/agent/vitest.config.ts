import { defineConfig } from "vitest/config";

/**
 * vitest config — node environment, tests under ./tests, .ts source resolves
 * through the app's own tsconfig (ESM / NodeNext). The e2e suite uses a longer
 * per-test timeout because it spawns a Python gateway subprocess.
 */
export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts"],
    environment: "node",
    globals: false,
    testTimeout: 20_000,
    hookTimeout: 20_000,
  },
});
