# Back up the experiment evidence

Run this in the Codespaces terminal from the Beacon checkout:

```bash
git pull --ff-only origin main && bash scripts/backup-evidence.sh
```

The script writes a new directory under `artifacts/backups/`. It includes every
run under `artifacts/integration/` and `artifacts/study/`, including failed and
interrupted runs. It also saves a Git bundle containing the committed source
history from normal branches and tags, the current commit, and the working-tree
status. Private tooling refs are excluded. Uncommitted source
changes are listed but are not included in the bundle.

The archive excludes the root `.env`, virtual environments, editor files, live
database volumes, and local coding-tool notes. Saved Compose files contain
synthetic test credentials. Container images are not included; keep the recorded
images separately if you need to replay the exact environment.

The script compares the archive with its source files, verifies the Git bundle,
and checks the archive's SHA-256. Leave completed evidence directories unchanged
while it runs. Each backup gets a new directory, so an earlier backup is not
overwritten.

In the Codespaces file explorer, open the printed directory, right-click each
of these files, and choose **Download**:

- `beacon-evidence.tar.gz`
- `beacon-evidence.tar.gz.sha256`

Keep them together outside Codespaces. A copy that remains in the Codespace is
not an external backup. The script does not upload or publish anything.

## Check and restore

In a directory containing the two downloaded files:

```bash
sha256sum -c beacon-evidence.tar.gz.sha256
mkdir restored-evidence
tar -xzf beacon-evidence.tar.gz -C restored-evidence
```

The extracted `artifacts/integration/` and `artifacts/study/` contain the raw
results. The source bundle is under the extracted `artifacts/backups/<stamp>/`.
Clone that bundle into a separate directory to recover the source history:

```bash
BUNDLE=$(find restored-evidence/artifacts/backups -name source.bundle -print -quit)
git clone "$BUNDLE" Beacon-restored
```

The measured study used
commit `4304151015775f6baa3c962f86af074cfe432bdf`. Later presentation changes do
not change that tested revision. Use the recorded revision when analyzing its
source fingerprint; a new measured batch needs a calibration for its own inputs.
