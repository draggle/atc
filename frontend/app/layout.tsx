import type { Metadata } from "next";
import { B612, B612_Mono } from "next/font/google";
import "./globals.css";

// B612 is the typeface Airbus commissioned for cockpit displays: built to stay legible on a
// dense screen, at a glance, under stress. The right voice for an air traffic tool.
const b612 = B612({ subsets: ["latin"], weight: ["400", "700"], variable: "--font-b612", display: "swap" });
const b612Mono = B612_Mono({ subsets: ["latin"], weight: ["400", "700"], variable: "--font-b612-mono", display: "swap" });

export const metadata: Metadata = {
  title: "Tower",
  description: "Plans the best path for every flight, adapts when anything changes, and makes sure every instruction is heard and flown.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`dark ${b612.variable} ${b612Mono.variable}`}>
      <body className="antialiased">{children}</body>
    </html>
  );
}
