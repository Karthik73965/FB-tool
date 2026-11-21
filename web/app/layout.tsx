import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Orbitron, Space_Grotesk } from "next/font/google";
import "./globals.css";

const sans = Space_Grotesk({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });
const display = Orbitron({ subsets: ["latin"], variable: "--font-display", display: "swap", weight: ["500", "600", "700"] });

export const metadata: Metadata = {
  title: "FB Clip Maker",
  description: "Turn talk-show videos into vertical clips with word-highlight captions.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { themeColor: "#03050b", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} ${display.variable}`}>
      <body>
        <div className="backdrop" aria-hidden>
          <div className="grid-floor" />
          <div className="scanlines" />
        </div>
        {children}
      </body>
    </html>
  );
}
