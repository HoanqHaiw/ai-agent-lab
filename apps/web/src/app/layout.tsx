import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agent Lab — Understand the task before you code",
  description: "Repository-aware task analysis and implementation planning for developers.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const clerkConfigured = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY);
  return (
    <html lang="en">
      <body>{clerkConfigured ? <ClerkProvider>{children}</ClerkProvider> : children}</body>
    </html>
  );
}
