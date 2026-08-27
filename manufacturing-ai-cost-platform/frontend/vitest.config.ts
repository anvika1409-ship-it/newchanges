import { defineConfig } from 'vitest/config';
import path from 'node:path';

/**
 * Frontend test configuration.
 *
 * The suite targets the pure logic that guards the contract boundary — the
 * wire→view adapters — rather than rendering. Those functions are where a
 * missing figure becomes "—" instead of 0, and where a field the API does not
 * return has to be written as null, so they are the ones worth pinning.
 */
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
});
