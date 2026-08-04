import type { Metadata } from "next";
import { AtlasExperience } from "@/components/AtlasExperience";

export const metadata: Metadata = {
  title: "Rubin Missing Light Atlas",
  description: "A measured atlas of what previous surveys failed to see around nearby galaxies.",
};

export default function Home() {
  return <AtlasExperience />;
}
