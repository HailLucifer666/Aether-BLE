import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Aether Protocol v0.1",
  description:
    "Cross-Device AI Arbitration — BLE proximity handoff demo. The conversation follows you.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-slate-950 text-slate-100 antialiased">
        {children}
      </body>
    </html>
  );
}
