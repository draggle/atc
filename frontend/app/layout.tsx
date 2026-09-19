import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Tower",
  description: "A second set of ears on the frequency",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="antialiased">{children}</body>
    </html>
  );
}
