import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "open-mem sandbox",
  description: "Write something, watch it climb the staircase, search it, inspect the trace.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
