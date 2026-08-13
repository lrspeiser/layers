import { AtlasExperience, type PrototypeScience } from "@/components/AtlasExperience";
import catalogData from "@/public/data/layers-catalog.json";
import prototypeScience from "@/public/data/prototype-science.json";
import type { LayersCatalog } from "@/lib/layers";

export default function Home() {
  return <AtlasExperience catalog={catalogData as unknown as LayersCatalog} prototypeScience={prototypeScience as PrototypeScience} />;
}
