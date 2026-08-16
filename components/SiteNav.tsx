import Link from "next/link";

// One navigation, defined once.
//
// Every page used to carry its own hand-written nav, and they had drifted:
// /coverage was "Full footprint" on four pages, /data was "Download the
// catalogue" on two and "Data access" on a third, and /coverage additionally
// offered three routes nothing else linked to. Five pages, five different maps
// of the same site -- which is most of why the navigation was hard to follow.
//
// The destinations here are the ones a reader actually needs. The dynamic detail
// routes (/tract/[tract], /galaxy/[slug], /target/[id], /overlay/[tract]) are
// reached by clicking through from a page, not from a global menu, and the
// development leftovers (/pilots, /prototype, /workspace) still resolve but no
// longer compete as top-level destinations.
const DESTINATIONS = [
  { href: "/", label: "The question" },
  { href: "/coverage", label: "Where the data is" },
  { href: "/explorer", label: "Where it disagrees" },
  { href: "/differences", label: "Cross-survey" },
  { href: "/data", label: "Download" },
  { href: "/goals", label: "What was attempted" },
] as const;

export default function SiteNav({
  chip,
  current,
  extras = [],
}: {
  chip: string;
  current?: string;
  // Page-local utilities: an in-page anchor, an API endpoint. These are not
  // site navigation and do not belong in the shared list, but they are also not
  // clutter -- consolidating the navs the first time swallowed /coverage's
  // "Dataset registry" anchor and its Coverage API link, which a test caught.
  extras?: ReadonlyArray<{ href: string; label: string }>;
}) {
  return (
    <header className="layers-header">
      <Link className="layers-brand" href="/">
        <span className="brand-glyph">
          <i />
          <b />
        </span>
        <strong>Layers</strong>
        <small>Rubin versus everyone else</small>
      </Link>
      <nav>
        {DESTINATIONS.filter((d) => d.href !== current).map((d) => (
          <Link key={d.href} href={d.href}>
            {d.label}
          </Link>
        ))}
        {extras.map((e) => (
          <a key={e.href} href={e.href}>
            {e.label}
          </a>
        ))}
      </nav>
      <span className="release-chip">{chip}</span>
    </header>
  );
}
