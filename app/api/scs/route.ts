import agreement from "@/public/data/layers/selected-regions/difference-agreement-slim.json";
import placements from "@/public/data/layers/selected-regions/register-placements.json";

// IVOA Simple Cone Search over the positions this project can publish.
//
// Why this and not the bespoke JSON routes next door: no astronomer will write a
// client for a schema invented here, and they do not have to. SCS returns a
// VOTable, which TOPCAT and Aladin load from a URL and pyvo reads in three
// lines. The protocol is a GET with RA, DEC and SR in degrees.
//
// What is deliberately NOT served: the 50,233-source catalogue. Its Rubin fluxes
// are measurements of access-restricted DP2 pixels, and docs/DATA_STORAGE.md
// forbids publishing derived science bytes for exactly that reason. Serving it
// needs a data-rights decision, not more code -- see docs/MACHINE_ACCESS.md.
//
// Everything below is already public in this repository.

export const dynamic = "force-static";

type Row = {
  id: string;
  ra: number;
  dec: number;
  kind: string;
  detail: string;
  references: string;
};

const ROWS: Row[] = [
  ...agreement.confirmed.map((item) => ({
    id: `confirmed-${item.regionId}`,
    ra: item.sky.raDeg,
    dec: item.sky.decDeg,
    kind: "difference-confirmed",
    detail: "optical difference seen against more than one independent reference",
    references: Object.keys(item.seenIn).join(","),
  })),
  ...Object.entries(placements.byRegion).flatMap(([regionId, items]) =>
    (items as Array<{ operator: string; what: string | null; sky: { raDeg: number; decDeg: number } }>).map(
      (item, index) => ({
        id: `${regionId}-${item.operator}-${index}`,
        ra: item.sky.raDeg,
        dec: item.sky.decDeg,
        kind: item.operator,
        detail: item.what ?? item.operator,
        references: "",
      }),
    ),
  ),
];

const FIELDS: Array<{ name: string; datatype: string; ucd?: string; unit?: string; width?: number }> = [
  { name: "id", datatype: "char", ucd: "meta.id;meta.main", width: 64 },
  { name: "ra", datatype: "double", ucd: "pos.eq.ra;meta.main", unit: "deg" },
  { name: "dec", datatype: "double", ucd: "pos.eq.dec;meta.main", unit: "deg" },
  { name: "kind", datatype: "char", ucd: "meta.code.class", width: 32 },
  { name: "detail", datatype: "char", ucd: "meta.note", width: 128 },
  { name: "references", datatype: "char", ucd: "meta.ref", width: 32 },
];

const escape = (value: string) =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function votable(rows: Row[], note: string): string {
  const fields = FIELDS.map(
    (field) =>
      `<FIELD name="${field.name}" datatype="${field.datatype}"` +
      (field.ucd ? ` ucd="${field.ucd}"` : "") +
      (field.unit ? ` unit="${field.unit}"` : "") +
      (field.width ? ` arraysize="${field.width}"` : "") +
      "/>",
  ).join("\n      ");
  const data = rows
    .map(
      (row) =>
        "        <TR>" +
        [row.id, row.ra, row.dec, row.kind, row.detail, row.references]
          .map((value) => `<TD>${escape(String(value))}</TD>`)
          .join("") +
        "</TR>",
    )
    .join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>
<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">
  <RESOURCE type="results">
    <INFO name="QUERY_STATUS" value="OK"/>
    <INFO name="note" value="${escape(note)}"/>
    <TABLE name="layers_positions">
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

// Small-angle separation is exact enough: this table spans arcseconds, not degrees.
function separationDeg(ra1: number, dec1: number, ra2: number, dec2: number): number {
  const mean = ((dec1 + dec2) / 2) * (Math.PI / 180);
  const dRa = (ra1 - ra2) * Math.cos(mean);
  const dDec = dec1 - dec2;
  return Math.hypot(dRa, dDec);
}

export function GET(request: Request) {
  const url = new URL(request.url);
  const ra = Number(url.searchParams.get("RA") ?? url.searchParams.get("ra"));
  const dec = Number(url.searchParams.get("DEC") ?? url.searchParams.get("dec"));
  const sr = Number(url.searchParams.get("SR") ?? url.searchParams.get("sr"));

  const headers = { "Content-Type": "application/x-votable+xml; charset=utf-8" };
  const note =
    "Positions this project can publish. The 50,233-source catalogue is not served: its " +
    "Rubin fluxes measure access-restricted DP2 pixels. See docs/MACHINE_ACCESS.md.";

  // No parameters at all: return the whole table rather than an error, since
  // that is what someone exploring the endpoint by hand actually wants.
  if (!url.searchParams.has("RA") && !url.searchParams.has("ra")) {
    return new Response(votable(ROWS, note), { headers });
  }
  if (!Number.isFinite(ra) || !Number.isFinite(dec) || !Number.isFinite(sr)) {
    return new Response(errorTable("RA, DEC and SR are required and must be numeric degrees"), {
      status: 400,
      headers,
    });
  }
  if (sr <= 0 || sr > 90) {
    return new Response(errorTable("SR must be between 0 and 90 degrees"), { status: 400, headers });
  }

  const matched = ROWS.filter((row) => separationDeg(row.ra, row.dec, ra, dec) <= sr);
  return new Response(votable(matched, note), { headers });
}
