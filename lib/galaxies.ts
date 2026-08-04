export type Galaxy = {
  slug: string;
  name: string;
  catalog: string;
  constellation: string;
  distance: string;
  inclination: string;
  morphology: string;
  raDeg: number;
  decDeg: number;
  fieldWidthArcmin: number;
  coverage: "unchecked" | "covered" | "not-covered";
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
    raDeg: 13.722694,
    decDeg: -37.684214,
    fieldWidthArcmin: 36,
    coverage: "unchecked",
  },
  {
    slug: "ngc-55",
    name: "NGC 55",
    catalog: "SPARC 014",
    constellation: "Sculptor",
    distance: "2.1 Mpc",
    inclination: "80°",
    morphology: "Magellanic spiral",
    raDeg: 3.723342,
    decDeg: -39.196628,
    fieldWidthArcmin: 36,
    coverage: "unchecked",
  },
  {
    slug: "ngc-7793",
    name: "NGC 7793",
    catalog: "SPARC 118",
    constellation: "Sculptor",
    distance: "3.9 Mpc",
    inclination: "50°",
    morphology: "Flocculent spiral",
    raDeg: 359.457308,
    decDeg: -32.591028,
    fieldWidthArcmin: 24,
    coverage: "unchecked",
  },
  {
    slug: "ngc-24",
    name: "NGC 24",
    catalog: "SPARC 072",
    constellation: "Sculptor",
    distance: "7.3 Mpc",
    inclination: "64°",
    morphology: "Late-type spiral",
    raDeg: 2.485592,
    decDeg: -24.963131,
    fieldWidthArcmin: 16,
    coverage: "unchecked",
  },
  {
    slug: "eso-116-g012",
    name: "ESO 116-G012",
    catalog: "SPARC 173",
    constellation: "Eridanus",
    distance: "13.0 Mpc",
    inclination: "74°",
    morphology: "Low-surface-brightness galaxy",
    raDeg: 48.269775,
    decDeg: -57.357247,
    fieldWidthArcmin: 12,
    coverage: "unchecked",
  },
];

export const getGalaxy = (slug: string) =>
  galaxies.find((galaxy) => galaxy.slug === slug);
