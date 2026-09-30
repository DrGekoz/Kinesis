"""Check every in-page anchor in README.md resolves to a heading (GitHub slug rules).

A broken nav on a public repo is a bad look, and these are easy to get wrong with colons and
apostrophes in headings.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
md = (ROOT / "README.md").read_text(encoding="utf-8")


def slug(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"`|\*|\[|\]|\(|\)|:|\.|,|/|\"|'|—|·", "", s)
    s = s.replace(" ", "-")
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


headings = [m.group(2) for m in re.finditer(r"^(#{1,6})\s+(.*)$", md, re.M)]
slugs = {slug(h) for h in headings}
# GitHub also de-duplicates repeats; not a concern here

anchors = sorted(set(re.findall(r"\]\(#([a-z0-9\-]+)\)", md)))
missing = [a for a in anchors if a not in slugs]

print(f"headings: {len(headings)}")
print(f"in-page anchors: {len(anchors)}")
for a in anchors:
    print(f"  {'ok  ' if a in slugs else 'DEAD'} #{a}")
if missing:
    print(f"\nDEAD ANCHORS: {missing}")
    raise SystemExit(1)

# also check the outbound links we can verify structurally
links = sorted(set(re.findall(r"https://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+", md)))
print(f"\nrepo links: {len(links)}")
for link in links:
    print(f"  {link}")
print("\nall anchors resolve")
