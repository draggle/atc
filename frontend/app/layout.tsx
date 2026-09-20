import type { Metadata } from "next";
import { B612, B612_Mono, Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

// Plus Jakarta Sans is the UI face: the wordmark, every label, every button. Hierarchy comes from
// weight and opacity, so 400 to 700 are all loaded.
const jakarta = Plus_Jakarta_Sans({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-jakarta", display: "swap" });
// B612 is the typeface Airbus commissioned for cockpit displays. B612 Mono is kept for data that
// has to line up: callsigns, flight levels, transcripts. B612 proportional stays available under
// --font-b612 for anything that still wants it.
const b612 = B612({ subsets: ["latin"], weight: ["400", "700"], variable: "--font-b612", display: "swap" });
const b612Mono = B612_Mono({ subsets: ["latin"], weight: ["400", "700"], variable: "--font-b612-mono", display: "swap" });

export const metadata: Metadata = {
  title: "squack.",
  description: "Plans the best path for every flight, adapts when anything changes, and makes sure every instruction is heard and flown.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`dark ${b612.variable} ${b612Mono.variable} ${jakarta.variable}`}>
      <body className="antialiased">{children}</body>
    </html>
  );
}
