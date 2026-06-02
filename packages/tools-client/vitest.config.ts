import { defineConfig } from "vitest/config";

/**
 * vitest config — node environment, tests under ./tests, .ts source resolves
 * through the package's own tsconfig (ESM / NodeNext). Coverage output
 * configured but only emitted under explicit `vitest run --coverage`.
 */
export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts"],
    environment: "node",
    globals: false,
  },
});
