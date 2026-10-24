# Branding assets

## logo.png

Drop your institution's logo in this folder as **`logo.png`** (or `logo.svg`).
The application detects it at startup and uses it on:

- the candidate sign-in page (`/login`)
- the administrator sign-in page (`/admin/login`)
- the examination header
- every dashboard header

No code or template change is needed. Restart the backend after adding it:

```bash
docker compose restart backend
```

Until a `logo.png` or `logo.svg` is present, `logo-placeholder.svg` is served
instead — a neutral mark that belongs to no institution.

**Recommended:** PNG with a transparent background, at least 240 × 72 px
(480 × 144 for crisp rendering on high-DPI screens). Wide/landscape artwork
suits the header better than a square mark.

Resolution order: `logo.png` → `logo.svg` → `logo-placeholder.svg`.
