import type { Metadata } from "next";
import { DM_Sans, IBM_Plex_Mono, Manrope } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";

const dmSans = DM_Sans({ variable: "--font-body", subsets: ["latin"] });
const manrope = Manrope({ variable: "--font-display", subsets: ["latin"] });
const plexMono = IBM_Plex_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("host") ?? "localhost:3000";
  const protocol = requestHeaders.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  const origin = `${protocol}://${host}`;
  const description = "A measured atlas of what previous surveys failed to see around nearby galaxies.";

  return {
    metadataBase: new URL(origin),
    title: { default: "Rubin Missing Light Atlas", template: "%s · Rubin Missing Light Atlas" },
    description,
    icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
    openGraph: {
      title: "Rubin Missing Light Atlas",
      description,
      type: "website",
      images: [{ url: `${origin}/og.png`, width: 1672, height: 941, alt: "Rubin Missing Light Atlas" }],
    },
    twitter: {
      card: "summary_large_image",
      title: "Rubin Missing Light Atlas",
      description,
      images: [`${origin}/og.png`],
    },
  };
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${dmSans.variable} ${manrope.variable} ${plexMono.variable}`}>{children}</body>
    </html>
  );
}
