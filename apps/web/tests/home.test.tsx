import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

describe("home route", () => {
  it("keeps the guest UI when only the Clerk publishable key is configured", async () => {
    vi.stubEnv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "pk_test_placeholder");
    vi.stubEnv("CLERK_SECRET_KEY", "");

    try {
      const { default: Home } = await import("../src/app/page");
      const markup = renderToStaticMarkup(createElement(Home));

      expect(markup).toContain("Understand the task.");
      expect(markup).toContain("Connect GitHub");
      expect(markup).toContain("Upload a project");
      expect(markup).toContain("Guest");
    } finally {
      vi.unstubAllEnvs();
    }
  });
});
