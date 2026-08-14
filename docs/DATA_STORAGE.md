# Data storage policy

Decided 2026-08-14.

## Decision

**Retrieved and derived science bytes are never committed to git and never mirrored to
public object storage. They are reproduced on demand from the tracked region list and
fetch scripts, and verified against SHA-256 checksums that *are* tracked.**

`pipeline/results/` and `pipeline/output/` are ignored in full (~36 GB today: 29 GB and
7.1 GB respectively). Two small legacy JSON manifests under `pipeline/results/` remain
tracked and are explicitly re-included in `.gitignore`.

## Why not LFS or a public bucket

1. **Rubin DP2 pixels are access-restricted.** They require Rubin data rights (US/Chilean
   researchers and students). Redistributing them — via Git LFS, R2, S3, or a public
   preview — would breach that access condition. This alone rules out mirroring the Rubin
   half, independent of cost.
2. The acquisition policy already refuses to serialize credentials or signed URLs
   (`tokensSerialized: false`, `signedUrlsSerialized: false` in
   `public/data/coverage/rubin-pixels-50.json`). Storing the bytes would reintroduce the
   leak risk that policy exists to prevent.
3. Public-survey pixels (Legacy DR10, Pan-STARRS, 2MASS, unWISE, GALEX, ZTF, HIPASS,
   eROSITA, ACT, Planck) are already permanently hosted by their own archives. Mirroring
   them adds cost and a staleness problem, and buys nothing.
4. 36 GB in Git LFS would exceed the free bandwidth tier on any meaningful clone rate.

## What that means in practice

**The reproduction contract is: tracked recipe + tracked checksums → re-fetch → verify.**

| Layer | Tracked in git | Reproduces from |
|---|---|---|
| Which regions to fetch | `public/data/coverage/selected-regions.json` | — |
| How to fetch Rubin | `pipeline/acquire_dp2_pixels.py`, `download_dp2_matches.py`, `query_dp2_sia.py` | RSP token in `.env` (`RUBIN_RSP_TOKEN`) |
| How to fetch references | `pipeline/fetch_*.py` (legacy, panstarrs, allwise, galex, uv_ir_time, radio_xray_hi, lensing_cmb, …) | public archives |
| Integrity anchors | `sourceSha256`, `mosaicSha256`, `previewSha256`, `checksum` in `public/data/**` | — |
| Verification | `pipeline/validate_dp2_pixels.py` and the other `validate_*.py` scripts compare byte length + SHA-256 | — |
| Published artifacts | `public/data/**`, `public/layer-previews/**` (~183 MB) | committed, served by the site |

Source URLs are deliberately **not** serialized. Re-acquisition re-queries SIA from the
region centers rather than replaying a URL, so an expired or signed URL can never become a
reproduction dependency. The SHA-256 recorded at first fetch is what proves the re-fetched
bytes are the same bytes.

## Known gaps

- **No single top-level rebuild/verify entry point.** Verification exists per product
  family (`validate_dp2_pixels.py`, `validate_region_cache.py`,
  `validate_uv_ir_time_pixels.py`, `validate_radio_xray_hi.py`,
  `validate_lensing_cmb_pixels.py`, …) but nothing runs them all and reports one verdict.
  Worth adding a `pipeline/verify_local_store.py` that walks every manifest in
  `public/data/**`, checks each referenced local file's length and hash, and prints
  present / missing / corrupt counts.
- **Disk headroom is thin.** `C:` is at 97% (124 GB free of 3.7 TB). Acquiring a second
  Rubin band for the 50 tracts adds roughly 2 GB (current Rubin footprint: 1.27 GB of
  source cutouts, 864 MB of mosaics), which fits — but a full re-acquisition sweep or a
  move to whole-patch downloads would not.
- **Rate limits bound recovery time.** 30 SODA requests/min and 35 VO cutouts/min mean a
  cold rebuild of the Rubin half is measured in hours, not minutes. Treat the local store
  as expensive-to-rebuild and back it up at the filesystem level if the machine matters.
