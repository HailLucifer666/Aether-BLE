import type { Metadata } from "next";
import "./globals.css";
import ServiceWorkerRegistration from "./ServiceWorkerRegistration";

export const metadata: Metadata = {
  title: "Aether",
  description:
    "Aether spatial mesh dashboard — floor plan, live position, signal lab, timeline, and setup wizard.",
  manifest: "/manifest.json",
};

export const viewport = {
  themeColor: "#0a0f1e",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-slate-950 text-slate-100 antialiased">
        <ServiceWorkerRegistration />
        {children}
      </body>
    </html>
  );
}
