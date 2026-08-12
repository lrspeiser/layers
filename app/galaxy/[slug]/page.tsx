import { notFound, redirect } from "next/navigation";

const legacyTargetIds: Record<string, string> = {
  "ngc-300": "ngc0300",
  "ngc-55": "ngc0055",
  "ngc-7793": "ngc7793",
  "ngc-24": "ngc0024",
  "eso-116-g012": "eso116-g012",
};

export function generateStaticParams() {
  return Object.keys(legacyTargetIds).map((slug) => ({ slug }));
}

export default async function LegacyGalaxyPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const targetId = legacyTargetIds[slug];
  if (!targetId) notFound();
  redirect(`/target/${targetId}`);
}
