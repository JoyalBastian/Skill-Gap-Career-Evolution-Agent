"""One-off template patches for UX rebuild."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "templates"
BAD_OPEN = "<" + "motion"
BAD_CLOSE = "</" + "motion>"

for p in ROOT.rglob("*.html"):
    t = p.read_text(encoding="utf-8")
    n = t.replace(BAD_OPEN, "<div")
    n = n.replace(BAD_CLOSE, "</div>")
    if n != t:
        p.write_text(n, encoding="utf-8")
        print("fixed", p.name)

gap = ROOT / "skills" / "gap.html"
t = gap.read_text(encoding="utf-8")
marker = "<!-- Next steps -->"
if marker in t:
    start = t.index(marker)
    end = t.index("{% else %}", start)
    t = t[:start] + "{% if not journey.has_roadmap %}\n{% include \"partials/_next_step.html\" %}\n{% endif %}\n\n" + t[end:]
    gap.write_text(t, encoding="utf-8")
    print("patched skills/gap.html")
