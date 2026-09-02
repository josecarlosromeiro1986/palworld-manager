import { defineConfig } from "@playwright/test";

const host = process.env.E2E_HOST || "127.0.0.1";
const port = process.env.E2E_PORT || "8081";
const baseURL = "http://" + host + ":" + port;

export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: "line",
  timeout: 60_000,
  expect: {
    timeout: 15_000,
  },
  outputDir: process.env.PLAYWRIGHT_OUTPUT_DIR || "test-results",
  use: {
    baseURL,
    browserName: "chromium",
    locale: "pt-BR",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "off",
  },
  webServer: {
    command: "python scripts/run-e2e-server.py",
    url: baseURL + "/health",
    reuseExistingServer: false,
    timeout: 120_000,
    gracefulShutdown: {
      signal: "SIGTERM",
      timeout: 10_000,
    },
    stdout: "pipe",
    stderr: "pipe",
  },
});
