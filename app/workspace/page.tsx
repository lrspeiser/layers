import type { Metadata } from "next";
import { AtlasExperience, type PrototypeScience } from "@/components/AtlasExperience";
import catalogData from "@/public/data/layers-catalog.json";
import prototypeScience from "@/public/data/prototype-science.json";
import type { LayersCatalog } from "@/lib/layers";

export const metadata: Metadata = {
  title: "Comparison workspace",
  description: "Inspect calibrated Rubin pilot fields and linked SPARC and multi-survey evidence.",
};

export default function WorkspacePage() {
  return <AtlasExperience catalog={catalogData as unknown as LayersCatalog} prototypeScience={prototypeScience as PrototypeScience} />;
}
