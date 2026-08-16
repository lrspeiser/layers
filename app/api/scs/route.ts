import release from "@/public/data/layers/selected-regions/catalogue-release.json";

// IVOA Simple Cone Search over the published source catalogue.
//
// SCS rather than a bespoke JSON API because astronomers already have clients
// that speak it. This URL loads in TOPCAT and Aladin as-is, and pyvo reads it in
// three lines. Nobody has to learn a schema invented here.
//
// The catalogue is 50,233 rows, which a serverless route cannot hold per
// request, so it is tiled into one-degree bins in RA and Dec at publish time.
// A cone of any sane radius touches one or a few tiles, which are fetched as
// static files. See pipeline/publish_catalogue.py.

export const dynamic = "force-dynamic";

type Source = Record<string, number | string | boolean | null>;

const TILE_ROOT = release.coneSearch.tiles.root;

// Columns advertised to a client, with the UCDs it locates them by. Clients find
// position by UCD, not by name: without pos.eq.ra;meta.main a VOTable loads and
// cannot be plotted.
const FIELDS: Array<{ name: string; datatype: string; ucd: string; unit?: string; arraysize?: number }> = [
  { name: "source_id", datatype: "char", ucd: "meta.id;meta.main", arraysize: 64 },
  { name: "region_id", datatype: "char", ucd: "meta.id", arraysize: 32 },
  { name: "ra_deg", datatype: "double", ucd: "pos.eq.ra;meta.main", unit: "deg" },
  { name: "dec_deg", datatype: "double", ucd: "pos.eq.dec;meta.main", unit: "deg" },
  { name: "rubin_flux_njy", datatype: "double", ucd: "phot.flux", unit: "nJy" },
  { name: "rubin_flux_err_njy", datatype: "double", ucd: "stat.error;phot.flux", unit: "nJy" },
  { name: "reference_flux_njy", datatype: "double", ucd: "phot.flux", unit: "nJy" },
  { name: "rubin_mag_ab", datatype: "double", ucd: "phot.mag", unit: "mag" },
  { name: "reference_mag_ab", datatype: "double", ucd: "phot.mag", unit: "mag" },
  { name: "rubin_snr", datatype: "double", ucd: "stat.snr" },
  { name: "flux_ratio", datatype: "double", ucd: "arith.ratio" },
  { name: "departure_significance", datatype: "double", ucd: "stat.snr" },
  { name: "flag_near_edge", datatype: "boolean", ucd: "meta.code.qual" },
  { name: "flag_negative_reference", datatype: "boolean", ucd: "meta.code.qual" },
  { name: "flag_blended", datatype: "boolean", ucd: "meta.code.qual" },
];

const escape = (value: string) =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function cell(value: unknown): string {
  if (value === null || value === undefined) return "<TD></TD>";
  if (typeof value === "boolean") return `<TD>${value ? "T" : "F"}</TD>`;
  return `<TD>${escape(String(value))}</TD>`;
}

function votable(rows: Source[], info: Record<string, string>): string {
  const fields = FIELDS.map(
    (f) =>
      `<FIELD name="${f.name}" datatype="${f.datatype}" ucd="${f.ucd}"` +
      (f.unit ? ` unit="${f.unit}"` : "") +
      (f.arraysize ? ` arraysize="${f.arraysize}"` : "") +
      "/>",
  ).join("\n      ");
  const infos = Object.entries(info)
    .map(([name, value]) => `    <INFO name="${name}" value="${escape(value)}"/>`)
    .join("\n");
  const data = rows
    .map((row) => "        <TR>" + FIELDS.map((f) => cell(row[f.name])).join("") + "</TR>")
    .join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>
<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">
  <RESOURCE type="results">
    <INFO name="QUERY_STATUS" value="OK"/>
${infos}
    <TABLE name="rubin_reference_sources">
      ${fields}
      <DATA>
        <TABLEDATA>
${data}
        </TABLEDATA>
      </DATA>
    </TABLE>
  </RESOURCE>
</VOTABLE>
`;
}

function errorTable(message: string): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">
  <RESOURCE type="results">
    <INFO name="QUERY_STATUS" value="ERROR">${escape(message)}</INFO>
  </RESOURCE>
</VOTABLE>
`;
}

// Angular separation. Small-angle would break for a large SR near the pole, and
// a cone search has to accept any radius the caller asks for.
function separationDeg(ra1: number, dec1: number, ra2: number, dec2: number): number {
  const d2r = Math.PI / 180;
  const a = dec1 * d2r;
  const b = dec2 * d2r;
  const dRa = (ra1 - ra2) * d2r;
  const cosine = Math.sin(a) * Math.sin(b) + Math.cos(a) * Math.cos(b) * Math.cos(dRa);
  return Math.acos(Math.min(1, Math.max(-1, cosine))) / d2r;
}

function tileNames(ra: number, dec: number, sr: number): string[] {
  const decLo = Math.max(-90, dec - sr);
  const decHi = Math.min(90, dec + sr);
  // Near a pole the RA span opens out; clamp rather than compute a huge range.
  const cos = Math.cos(Math.max(Math.abs(decLo), Math.abs(decHi)) * (Math.PI / 180));
  const raSpan = cos < 1e-6 ? 180 : Math.min(180, sr / Math.max(cos, 1e-6));
  const names: string[] = [];
  for (let d = Math.floor(decLo); d <= Math.floor(decHi); d += 1) {
    for (let r = Math.floor(ra - raSpan); r <= Math.floor(ra + raSpan); r += 1) {
      const wrapped = ((r % 360) + 360) % 360;
      const name = `${String(wrapped).padStart(3, "0")}_${d < 0 ? "-" : "+"}${String(Math.abs(d)).padStart(2, "0")}`;
      if (!names.includes(name)) names.push(name);
    }
  }
  return names;
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const get = (key: string) => url.searchParams.get(key) ?? url.searchParams.get(key.toLowerCase());
  const headers = {
    "Content-Type": "application/x-votable+xml; charset=utf-8",
    "Cache-Control": "public, max-age=3600",
  };

  if (!get("RA") && !get("DEC")) {
    return new Response(
      errorTable(
        "IVOA Simple Cone Search. Required: RA, DEC, SR in degrees. " +
          "Example: /api/scs?RA=53.08&DEC=-27.49&SR=0.05 . " +
          "The whole catalogue is at /data/catalogue/rubin-reference-sources.parquet",
      ),
      { status: 400, headers },
    );
  }

  const ra = Number(get("RA"));
  const dec = Number(get("DEC"));
  const sr = Number(get("SR") ?? "0.05");
  if (!Number.isFinite(ra) || !Number.isFinite(dec) || !Number.isFinite(sr)) {
    return new Response(errorTable("RA, DEC and SR must be numeric degrees"), { status: 400, headers });
  }
  if (sr <= 0 || sr > 5) {
    // A cap, stated rather than silent: an unbounded radius would read every
    // tile and return the whole catalogue one row at a time.
    return new Response(
      errorTable("SR must be between 0 and 5 degrees; use the bulk Parquet download for more"),
      { status: 400, headers },
    );
  }

  const origin = url.origin;
  const rows: Source[] = [];
  for (const name of tileNames(ra, dec, sr)) {
    try {
      const response = await fetch(`${origin}${TILE_ROOT}/${name}.json`);
      if (!response.ok) continue;
      const tile = (await response.json()) as { sources: Source[] };
      for (const source of tile.sources) {
        const sourceRa = Number(source.ra_deg);
        const sourceDec = Number(source.dec_deg);
        if (!Number.isFinite(sourceRa) || !Number.isFinite(sourceDec)) continue;
        if (separationDeg(sourceRa, sourceDec, ra, dec) <= sr) rows.push(source);
      }
    } catch {
      // A missing tile is empty sky, not an error: tiles exist only where the
      // survey has sources.
      continue;
    }
  }

  rows.sort(
    (a, b) =>
      separationDeg(Number(a.ra_deg), Number(a.dec_deg), ra, dec) -
      separationDeg(Number(b.ra_deg), Number(b.dec_deg), ra, dec),
  );

  return new Response(
    votable(rows, {
      matches: String(rows.length),
      cutOn: "departure_significance measures distance from the field's own median flux ratio in units of that field's own scatter",
      caveat: release.caveat,
      bulk: "/data/catalogue/rubin-reference-sources.parquet",
    }),
    { headers },
  );
}
