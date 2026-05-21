"""Write rebuilt templates."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "templates"

FILES = {
    "skills/list.html": r'''{% extends "base.html" %}
{% block title %}My Skills — {{ SITE_NAME }}{% endblock %}
{% block content %}

<div class="page-header">
    <h1>My Skills</h1>
    <p>Skills detected from your resume and AI interview</p>
</div>

{% if user_skills %}
<div class="card" style="margin-bottom:24px;">
    <div class="card-header"><i class="bi bi-tags"></i> Skill Tags</div>
    <div class="card-body tag-cloud">
        {% for us in user_skills %}
        <span class="skill-tag present">{{ us.skill.name }}</span>
        {% endfor %}
    </div>
</motion>

<div class="card">
    <div class="card-header"><i class="bi bi-table"></i> All Skills</div>
    <div class="card-body" style="padding:0;overflow-x:auto;">
        <table class="data-table">
            <thead>
                <tr><th>Skill</th><th>Category</th><th>Proficiency</th><th>Source</th></tr>
            </thead>
            <tbody>
                {% for us in user_skills %}
                <tr>
                    <td><strong>{{ us.skill.name }}</strong></td>
                    <td>{{ us.skill.category|default:"—" }}</td>
                    <td>{{ us.proficiency }}/5</td>
                    <td><span class="badge badge-muted">{{ us.get_source_display }}</span></td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
{% else %}
<div class="card">
    <div class="card-body empty-state">
        <div class="empty-state-icon"><i class="bi bi-tags"></i></div>
        <h2>No skills detected yet</h2>
        <p>Upload a resume or complete the AI interview to discover your skills.</p>
        <div class="empty-state-actions">
            <a href="{% url 'users:resume_upload' %}" class="btn btn-primary">Upload Resume</a>
            <a href="{% url 'questionnaire:start' %}" class="btn btn-ghost">AI Interview</a>
        </div>
    </div>
</div>
{% endif %}

{% endblock %}
''',
}

for rel, content in FILES.items():
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.replace("<motion", "<div").replace("</motion>", "</div>"), encoding="utf-8")
    print("wrote", rel)
