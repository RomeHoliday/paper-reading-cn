# Source: DOI or arXiv identifier

The user gave a bare DOI or arXiv id/link. Resolve it before extraction.

## Resolution rules

- Resolve to the actual article first:
  - arXiv → abstract page, then PDF (and HTML/source when useful).
  - DOI → landing page, then open-access PDF or HTML if lawfully available.
- After resolving, this becomes a `pdf-text`, `scanned-pdf`, or `html` job.
  Load that fragment next. This fragment covers retrieval only.
- Capture bibliographic metadata (title, authors, venue, year, DOI/arXiv id)
  into `report.json` / notes.
- Prefer a lawful open-access version when the version of record is paywalled.
  Record which version was read in `translation_notes.md` (arXiv and published
  versions can differ).
- If resolution fails or only the abstract is reachable, build a draft bundle
  from what is available and mark the rest as not retrieved. Do not fabricate
  body text, figures, or results.
- Apply the copyright caution to the resolved artifact.

## Report implications

- Put the resolved `source.pdf` (when obtained) into the output bundle.
- Version drift between preprint and published PDF must not be silently
  merged; if both are consulted, separate anchors by version note.
