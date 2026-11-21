import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const sans = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "FB Clip Maker",
  description: "Turn talk-show videos into vertical clips with word-highlight captions.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { themeColor: "#f6f7f1", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>
        <div className="backdrop" aria-hidden />
        {children}
      </body>
    </html>
  );
}
