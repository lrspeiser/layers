"use client";

import type { Galaxy } from "@/lib/galaxies";
import { GalaxyComparison } from "@/components/GalaxyComparison";

export function GalaxyRecordComparison({ galaxy }: { galaxy: Galaxy }) {
  return <GalaxyComparison galaxy={galaxy} record />;
}
