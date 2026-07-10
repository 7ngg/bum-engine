import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "bum-engine — floor-plan generator",
  description: "Natural-language brief → validated floor-plan variants → native Revit .rvt",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
