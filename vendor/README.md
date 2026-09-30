# Vendored dependencies

Code here is third-party source, copied in so that Kinesis runs without a separate install step.
Each directory keeps its upstream `LICENSE`.

## eyetrax

- **Upstream:** https://github.com/ck-zhang/eyetrax
- **Revision:** `84e13a16af168ac7c383f7d50ec901cd6c0ad61d` (package version 0.4.0)
- **Licence:** MIT (see `eyetrax/LICENSE`)
- **Why vendored:** it is a real runtime dependency of Kinesis's gaze tracking, and the whole point of
  Kinesis is that it runs from a clone with no extra install step. Users no longer need to
  `pip install -e vendor/eyetrax`.
- **Imported by:** `kinesis/vendored.py` puts `eyetrax/src` on `sys.path` at import time, so
  `import eyetrax` resolves here whether or not anything is installed. An already-installed copy
  still wins if present, which is identical code.
- **Local modifications:** none. The upstream source is byte-for-byte as cloned; the nested `.git`
  directory was removed so the files can be committed as normal tracked source rather than a gitlink.

### Updating

```bash
cd vendor
rm -rf eyetrax
git clone https://github.com/ck-zhang/eyetrax.git
cd eyetrax && git checkout <new-commit> && rm -rf .git
cd .. && rm -rf eyetrax/**/__pycache__
```

Then update the revision above, run the test suite, and check `kinesis/vendored.py` still finds the
`src` layout.

## Not vendored (research only, ignored by git)

`awesome-hand-pose-estimation/` and `Virtual-Mouse/` are reference implementations kept out of the
repository. Kinesis does not import them; they are there to read.
