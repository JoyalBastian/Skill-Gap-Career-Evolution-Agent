from django import forms

from .models import Profile, ResumeUpload


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["bio", "target_career_level", "is_technical_track", "target_career"]
        widgets = {
            "bio": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "target_career_level": forms.Select(attrs={"class": "form-select"}),
            "is_technical_track": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "target_career": forms.Select(attrs={"class": "form-select"}),
        }


class ResumeUploadForm(forms.ModelForm):
    class Meta:
        model = ResumeUpload
        fields = ["file"]
        widgets = {
            "file": forms.FileInput(attrs={"class": "form-control", "accept": ".pdf"}),
        }

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > 5 * 1024 * 1024:
            raise forms.ValidationError("File size must be under 5MB.")
        if not f.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Only PDF files are allowed.")
        return f
