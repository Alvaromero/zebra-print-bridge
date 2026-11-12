# Security

## Reporting a vulnerability

Please do **not** open a public issue. Report it privately through GitHub:
**Security → Report a vulnerability** on this repository
(<https://github.com/Alvaromero/zebra-print-bridge/security/advisories/new>).

Include the version, how to reproduce it and what an attacker could do. You should get an answer
within a week. Fixes are released as a new version and credited in the advisory unless you prefer otherwise.

## Supported versions

Only the latest release gets security fixes.

## Scope and design

The bridge is a local HTTP service. What it is meant to guarantee:

- It listens on `127.0.0.1` only (unless `host` is changed in `config.json`, which logs a warning).
- A website can use the printers only after the user approves it in the viewer, or when it matches
  `allowed_origins`; unknown websites get a 403. Requests without an `Origin` header (local programs) are accepted.
- The viewer (`/ui`) answers only same-origin requests on `127.0.0.1`/`localhost` (Host and Origin are
  checked, against DNS rebinding) and sends no CORS headers, so websites cannot read the job history
  or change settings. Its API only accepts JSON bodies, which forces a CORS preflight.
- The viewer escapes every value it renders (`static/html.js`).
- The job history is written with mode 0600 (labels may contain names and addresses).

Reports about any of these, or about the `sendUrl` download, the image/PDF conversion, the `.deb`
maintainer scripts or the udev rule, are in scope.
