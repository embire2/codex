# Prepared CircleCI ARM64 job — not connected or run

This is one manual Apple Silicon native build of the exact reviewed source,
patch, normalized lock and upstream helper packages. It never publishes, deploys,
loads production configuration or receives npm/client/provider credentials.

The selected stable image is Xcode 26.5.0 / macOS 26.3.1, resource
`m4pro.medium` (6 vCPU, 28 GB). Four bounded Cargo build jobs use that machine;
native optimization, LTO, codegen units, compiler and target remain unchanged.
The official image manifest contains Python 3.14.3 and Node 24.15.0. Rustup
1.28.2 is bootstrapped from a checksum-pinned official ARM64 installer into owned
temporary directories, then installs the exact Rust 1.95.0 toolchain.

CircleCI Free caps ordinary jobs at one hour. The orchestration stops its own
process groups after 55 minutes, leaving room for checkout and artifact upload.
Documented teardown can take up to 10 additional minutes; this remains below
the requested 90-minute ceiling. At 200 credits/minute, reserve at least 18,000
actually available free credits before the one trigger, covering the whole bound.
No retry, paid plan, card, credit purchase, larger runner or automatic top-up is
authorized by these files. There are no contexts, caches or persistent workspaces.

Before triggering, the account owner must complete official CircleCI account/OAuth
authentication. Root must verify Free status, current credits, absence of billing
commitments and set/read back **one-day artifact retention in Plan → Usage Controls**.
CircleCI does not expose a per-`store_artifacts` YAML retention field. Those controls
can be organization-wide: inventory the account first, prefer a dedicated new
organization, and never alter unrelated projects to meet this requirement. If
one-day retention cannot be set safely, hold for an explicit correctly labelled
public-artifact retention exception; do not pass a false one-day parameter. Passing a
pipeline parameter is an extra guard; it is not proof of those account settings.

The pipeline is disabled by default. Root supplies `run_native_arm64=true`,
`free_quota_verified=true`, the exact reviewed branch commit,
`free_credit_reserve>=18000` and `artifact_retention_days=1` only after those checks.
The only allowed branch is `magnetapi/coderx-01672-20261112`; PR/tag work is rejected.
Public HTTPS checkout uses no stored Git credential. Child environments discard
automatic CI/OIDC/account secrets and use an empty npm configuration.

The build preserves failed native bytes and provenance before smoke, checks
the actual Mach-O minimum OS against the original public native binary, then
requires all 17 native checks, 10 real loopback tool continuations, useful output,
stable model IDs, managed auth/update boundaries and actual terminal branding.
All upstream helpers, legal notices and source boundaries retain the existing
independent checksum checks. Failed/partial artifacts are never release evidence.

Circle provenance uses actual Circle workflow/job/pipeline identifiers and commit;
it never fabricates a GitHub job. `provider_evidence.py` accepts only successful
authenticated job/workflow/pipeline readbacks, the exact owned repository/branch/
commit, and the exact reviewed source configuration. Root will collect those
readbacks after a genuine run. A live API schema difference must be reviewed;
missing or unknown fields do not become inferred success.

`verification-and-seal.diff` is a staged proposal, not an applied acceptance change.
It extends the existing verifier only for the ARM64 Circle provider. Existing
GitHub receipts, all six platform gates and the actual GNU saved-goal proof remain.

Official references, checked 12 November 2026:

- https://circleci.com/pricing/
- https://circleci.com/pricing/price-list/
- https://circleci.com/docs/guides/execute/using-macos/
- https://circle-macos-docs.s3.amazonaws.com/image-manifest/v18380/manifest.txt
- https://circleci.com/docs/reference/configuration-reference/
- https://circleci.com/docs/guides/optimize/artifacts/
- https://circleci.com/docs/api/v2/index.html
- https://static.rust-lang.org/rustup/archive/1.28.2/aarch64-apple-darwin/rustup-init.sha256
