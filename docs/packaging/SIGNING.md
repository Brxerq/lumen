# Signing and distribution

Unsigned binaries trip Windows SmartScreen and macOS Gatekeeper (the
`install.sh` quarantine `xattr` work-around exists only because of this). The
release workflow signs automatically when the secrets below exist in the
repository settings; without them it prints "shipping unsigned" and carries on,
so forks keep working.

## macOS

You need an Apple Developer Program membership (Developer ID Application
certificate) and an app-specific password for notarisation.

| Secret | Value |
|---|---|
| `MACOS_CERT_P12` | The Developer ID Application certificate + key, exported as `.p12`, base64-encoded (`base64 -i cert.p12 \| pbcopy`). |
| `MACOS_CERT_PASSWORD` | The `.p12` export password. |
| `MACOS_SIGN_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID1234)`. |
| `MACOS_NOTARY_APPLE_ID` | The Apple ID that owns the certificate. |
| `MACOS_NOTARY_PASSWORD` | An app-specific password from appleid.apple.com. |
| `MACOS_NOTARY_TEAM_ID` | The ten-character team id. |

The workflow signs with the hardened runtime and a timestamp, zips the binary,
and submits it to `notarytool --wait`. The single-file binary is stapled
implicitly on first run (Gatekeeper checks the ticket online); an `.app`
bundle would carry the ticket itself — see the roadmap.

## Windows

Any code-signing certificate works (OV is enough to lose the "unknown
publisher" wording after some reputation is built; EV skips it immediately).

| Secret | Value |
|---|---|
| `WINDOWS_CERT_PFX` | The certificate + key as `.pfx`, base64-encoded. |
| `WINDOWS_CERT_PASSWORD` | Its password. |

Signing uses `signtool` from the Windows SDK that ships on the GitHub runner,
SHA-256 with an RFC 3161 timestamp.

## Package managers

Once releases are signed, the two manifests next to this file publish them:

- `lumen.rb` — a Homebrew formula for a tap (`brew tap Brxerq/lumen && brew install lumen`).
  Bump `version` and the two `sha256` lines from `SHA256SUMS` on each release.
- `winget/` — a winget manifest set (`winget install Brxerq.Lumen`). Submit to
  `microsoft/winget-pkgs` with `wingetcreate update Brxerq.Lumen --urls <exe url> --version X.Y.Z`.

Both read the same assets the in-app updater does, so nothing else changes.
