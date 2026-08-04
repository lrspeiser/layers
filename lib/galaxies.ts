export type Galaxy = {
  slug: string;
  name: string;
  catalog: string;
  constellation: string;
  distance: string;
  inclination: string;
  morphology: string;
  status: "Analyzed" | "Review" | "Queued";
  feature: string;
  diskDelta: string;
  gravityDelta: string;
  confidence: number;
  crop: string;
  outerContext: string;
  outerLevel: "large" | "above" | "expected" | "pending";
  gravityContext: string;
  gravityLevel: "large" | "above" | "expected" | "pending";
  inclinationDelta: string;
  inclinationContext: string;
  inclinationLevel: "large" | "above" | "expected" | "pending";
  structures: string;
  structuresContext: string;
  structuresLevel: "large" | "above" | "expected" | "pending";
};

export const galaxies: Galaxy[] = [
  {
    slug: "ngc-300",
    name: "NGC 300",
    catalog: "SPARC 001",
    constellation: "Sculptor",
    distance: "2.0 Mpc",
    inclination: "42°",
    morphology: "Flocculent spiral",
    status: "Analyzed",
    feature: "Extended outer disk",
    diskDelta: "+38%",
    gravityDelta: "+9%",
    confidence: 94,
    crop: "72% 62%",
    outerContext: "Typical audit shift: 5–15%",
    outerLevel: "large",
    gravityContext: "Typical audit shift: under 3%",
    gravityLevel: "large",
    inclinationDelta: "−4°",
    inclinationContext: "Typical survey scatter: ±3°",
    inclinationLevel: "above",
    structures: "02",
    structuresContext: "Any validated feature is notable",
    structuresLevel: "above",
  },
  {
    slug: "ngc-55",
    name: "NGC 55",
    catalog: "SPARC 014",
    constellation: "Sculptor",
    distance: "2.1 Mpc",
    inclination: "80°",
    morphology: "Magellanic spiral",
    status: "Review",
    feature: "Asymmetric halo",
    diskDelta: "+21%",
    gravityDelta: "+5%",
    confidence: 87,
    crop: "13% 23%",
    outerContext: "Typical audit shift: 5–15%",
    outerLevel: "above",
    gravityContext: "Typical audit shift: under 3%",
    gravityLevel: "above",
    inclinationDelta: "−1°",
    inclinationContext: "Typical survey scatter: ±3°",
    inclinationLevel: "expected",
    structures: "01",
    structuresContext: "Candidate awaiting review",
    structuresLevel: "above",
  },
  {
    slug: "ngc-7793",
    name: "NGC 7793",
    catalog: "SPARC 118",
    constellation: "Sculptor",
    distance: "3.9 Mpc",
    inclination: "50°",
    morphology: "Flocculent spiral",
    status: "Analyzed",
    feature: "Diffuse stellar halo",
    diskDelta: "+17%",
    gravityDelta: "+4%",
    confidence: 91,
    crop: "50% 44%",
    outerContext: "Typical audit shift: 5–15%",
    outerLevel: "above",
    gravityContext: "Typical audit shift: under 3%",
    gravityLevel: "above",
    inclinationDelta: "+2°",
    inclinationContext: "Typical survey scatter: ±3°",
    inclinationLevel: "expected",
    structures: "01",
    structuresContext: "Candidate awaiting review",
    structuresLevel: "above",
  },
  {
    slug: "ngc-24",
    name: "NGC 24",
    catalog: "SPARC 072",
    constellation: "Sculptor",
    distance: "7.3 Mpc",
    inclination: "64°",
    morphology: "Late-type spiral",
    status: "Queued",
    feature: "Outer-disk candidate",
    diskDelta: "Pending",
    gravityDelta: "Pending",
    confidence: 76,
    crop: "88% 20%",
    outerContext: "Measurement not yet run",
    outerLevel: "pending",
    gravityContext: "Requires a validated light profile",
    gravityLevel: "pending",
    inclinationDelta: "Pending",
    inclinationContext: "Cross-survey fit queued",
    inclinationLevel: "pending",
    structures: "—",
    structuresContext: "Diffuse-light review queued",
    structuresLevel: "pending",
  },
  {
    slug: "eso-116-g012",
    name: "ESO 116-G012",
    catalog: "SPARC 173",
    constellation: "Eridanus",
    distance: "13.0 Mpc",
    inclination: "74°",
    morphology: "Low-surface-brightness",
    status: "Review",
    feature: "Faint plume candidate",
    diskDelta: "+12%",
    gravityDelta: "+3%",
    confidence: 82,
    crop: "32% 76%",
    outerContext: "Typical audit shift: 5–15%",
    outerLevel: "expected",
    gravityContext: "Typical audit shift: under 3%",
    gravityLevel: "expected",
    inclinationDelta: "+1°",
    inclinationContext: "Typical survey scatter: ±3°",
    inclinationLevel: "expected",
    structures: "01",
    structuresContext: "Candidate awaiting review",
    structuresLevel: "above",
  },
];

export const getGalaxy = (slug: string) =>
  galaxies.find((galaxy) => galaxy.slug === slug);
