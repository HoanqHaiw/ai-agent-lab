import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Agent Lab — Understand the task before you code",
  description: "Repository-aware task analysis and implementation planning for developers.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
