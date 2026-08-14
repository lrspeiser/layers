import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import {
  TractEvidenceExplorer,
  type TractEvidenceRoute,
} from "@/components/TractEvidenceExplorer";
import manifestData from "@/public/data/layers/family-examples/tract-manifest.json";
import spectrumData from "@/public/data/layers/family-examples/spectroscopy.json";
import xrayData from "@/public/data/layers/family-examples/xray.json";
import neutralGasData from "@/public/data/layers/family-examples/neutralGas.json";
import lensingData from "@/public/data/layers/family-examples/lensing.json";

type PageProps = { params: Promise<{ tract: string }> };
const routes = manifestData.routes as unknown as TractEvidenceRoute[];

export function generateStaticParams() {
  return routes.map((route) => ({ tract: String(route.tract) }));
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { tract } = await params;
  const route = routes.find((item) => String(item.tract) === tract);
  if (!route) return { title: "Tract evidence not found" };
  return {
    title: `Rubin tract ${tract} evidence`,
    description: `Real ${route.families.join(", ")} products overlapping Rubin DP2 tract ${tract}, with explicit scientific limitations.`,
  };
}

export default async function TractEvidencePage({ params }: PageProps) {
  const { tract } = await params;
  const route = routes.find((item) => String(item.tract) === tract);
  if (!route) notFound();
  return <main id="top">
    <header className="layers-header">
      <Link className="layers-brand" href="/"><span className="brand-glyph"><i /><b /></span><strong>Layers</strong><small>science comparison workspace</small></Link>
      <nav><Link href="/">Full footprint</Link><Link href="/pilots">Live multi-survey pilots</Link><Link href="/prototype">Aligned optical prototype</Link><a href="/data/layers/family-examples/tract-manifest.json">Evidence manifest</a></nav>
      <span className="release-chip">TRACT {route.tract} · EVIDENCE</span>
    </header>
    <TractEvidenceExplorer
      route={route}
      spectrum={route.tract === 9813 ? spectrumData as never : undefined}
      xray={route.tract === 9813 ? xrayData as never : undefined}
      neutralGas={route.tract === 5061 ? neutralGasData as never : undefined}
      lensing={route.tract === 9813 ? lensingData as never : undefined}
    />
  </main>;
}
