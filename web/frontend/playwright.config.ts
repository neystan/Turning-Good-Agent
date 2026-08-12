import { defineConfig } from "@playwright/test";

export default defineConfig({
  testIgnore: "**/multi_agent_view.spec.ts",
});
