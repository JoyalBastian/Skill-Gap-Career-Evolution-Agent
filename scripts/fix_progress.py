from pathlib import Path

p = Path(__file__).resolve().parent.parent / "templates" / "progress" / "dashboard.html"
t = p.read_text(encoding="utf-8")
start = t.index("        {% for label, value in progress.items %}")
end = t.index("        {% endfor %}", start) + len("        {% endfor %}")
new = """        {% for item in progress_metrics %}
        <div style="margin-bottom:16px;">
            <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px;">
                <span style="font-weight:500;">{{ item.label }}</span>
                <span style="color:var(--primary);font-weight:600;">{{ item.value }}%</span>
            </div>
            <div class="progress-track">
                <motion class="progress-fill {% if item.value >= 70 %}success{% elif item.value >= 40 %}{% else %}warning{% endif %}" style="width:{{ item.value }}%;"></div>
            </div>
        </div>
        {% endfor %}"""
BAD = "<" + "motion"
new = new.replace(BAD, "<div")
p.write_text(t[:start] + new + t[end:], encoding="utf-8")
print("done")
