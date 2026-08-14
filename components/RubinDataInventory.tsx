import Link from "next/link";

const ARCHIVE_DEEP_COADD_DATASETS = 925_460;
const rows = [
  { id: "ugc00191", name: "UGC00191", regionArcmin: 12, records: 16, bands: ["g", "i", "r", "z"], shownBands: ["i", "z"], comparisons: 2, noPixels: false },
  { id: "ugc00634", name: "UGC00634", regionArcmin: 12, records: 21, bands: ["i", "r", "z"], shownBands: ["i", "z"], comparisons: 2, noPixels: false },
  { id: "ugc00891", name: "UGC00891", regionArcmin: 12, records: 17, bands: ["i"], shownBands: ["i"], comparisons: 2, noPixels: false },
  { id: "ngc0100", name: "NGC0100", regionArcmin: 12, records: 2, bands: [], shownBands: [], comparisons: 0, noPixels: true },
] as const;

const retrievedRecords = rows.reduce((total, row) => total + row.records, 0);
const usableRecords = rows.filter((row) => !row.noPixels).reduce((total, row) => total + row.records, 0);
const mosaicCount = rows.reduce((total, row) => total + row.bands.length, 0);
const shownMosaicCount = rows.reduce((total, row) => total + row.shownBands.length, 0);

export function RubinDataInventory({ defaultOpen = false }: { defaultOpen?: boolean }) {
  return (
    <details className="rubin-data-inventory" open={defaultOpen}>
      <summary>
        <span><strong>Rubin data inventory</strong><small>Archive → retrieved → built → visible</small></span>
        <b>{ARCHIVE_DEEP_COADD_DATASETS.toLocaleString("en-US")} archive datasets · {mosaicCount} mosaics built · {shownMosaicCount}/{mosaicCount} shown</b>
      </summary>
      <div className="rubin-inventory-content">
        <div className="rubin-inventory-stats">
          <span><small>EARLY DP2 ARCHIVE</small><strong>{ARCHIVE_DEEP_COADD_DATASETS.toLocaleString("en-US")}</strong><em>deep-coadd patch-band datasets</em></span>
          <span><small>OUR SPARC QUERY</small><strong>{retrievedRecords}</strong><em>source records returned across 4 fields</em></span>
          <span><small>USABLE INPUTS</small><strong>{usableRecords}</strong><em>records used to build 8 field-band mosaics</em></span>
          <span><small>VISIBLE NOW</small><strong>{shownMosaicCount} / {mosaicCount}</strong><em>unique field-band mosaics in public viewers</em></span>
        </div>

        <div className="rubin-inventory-table" role="table" aria-label="Rubin images retrieved and shown">
          <div className="rubin-inventory-head" role="row"><span>FIELD</span><span>RETRIEVED</span><span>SHOWN NOW</span><span>NOT YET SHOWN</span><span>ACCESS</span></div>
          {rows.map(({ id, name, regionArcmin, records, bands, comparisons, shownBands, noPixels }) => {
            const hiddenBands = bands.filter((band) => !(shownBands as readonly string[]).includes(band));
            return <div role="row" key={id}>
              <span><strong>{name}</strong><small>{regionArcmin}&prime; field</small></span>
              <span><strong>{records} records</strong><small>{noPixels ? "NO_DATA pixels only" : `${bands.join(" · ")} mosaics`}</small></span>
              <span><strong>{noPixels ? "None" : shownBands.join(" · ")}</strong><small>{comparisons ? `${comparisons} aligned comparison views` : "no usable view"}</small></span>
              <span><strong>{hiddenBands.length ? hiddenBands.join(" · ") : "None"}</strong><small>{noPixels ? "footprint false positive" : hiddenBands.length ? "mosaics built; viewer pending" : "all usable bands represented"}</small></span>
              <span><Link href={`/target/${id}`}>{noPixels ? "Open audit" : "Open field"} →</Link>{id === "ugc00191" && <Link href="/prototype">Prototype →</Link>}</span>
            </div>;
          })}
        </div>

        <div className="rubin-pixel-payload">
          <div><span className="eyebrow">DATA BEHIND EACH RUBIN MOSAIC</span><p><strong>Image</strong> calibrated flux in nJy · <strong>Variance</strong> uncertainty in nJy² · <strong>Mask</strong> processing and validity bits · <strong>Metadata</strong> WCS, PSF, filter, patch inputs, provenance, and checksums.</p></div>
          <div><span className="eyebrow">PUBLIC WEB STATUS</span><p>Display images, masks, coverage maps, comparison metadata, and checksums are public here. Full calibrated FITS planes remain in analysis storage and are not yet served as bulk web downloads.</p></div>
          <a href="https://dp2.lsst.io/products/images/deep_coadd.html" target="_blank" rel="noreferrer">Official Rubin archive definition ↗</a>
          <Link href="/api/catalog">Machine-readable Layers catalog →</Link>
        </div>
      </div>
    </details>
  );
}
