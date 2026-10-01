from django import forms

from apps.cadastros.models import Funcionario


LIMITE_FOTO_FUNCIONARIO_BYTES = 5 * 1024 * 1024


class FuncionarioForm(forms.ModelForm):
    cpf = forms.CharField(required=False, max_length=14)

    class Meta:
        model = Funcionario
        exclude = ["filial", "ativo", "created_at", "updated_at"]
        widgets = {
            "foto": forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
            "data_admissao": forms.DateInput(attrs={"type": "date"}),
            "salario_base": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "observacao": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, filial=None, **kwargs):
        self.filial = filial
        super().__init__(*args, **kwargs)

    def clean_cpf(self):
        cpf = "".join(filter(str.isdigit, self.cleaned_data.get("cpf", "") or ""))
        if cpf and len(cpf) != 11:
            raise forms.ValidationError("CPF deve ter 11 digitos.")
        if cpf and self.filial:
            duplicado = Funcionario.objects.for_filial(self.filial).filter(cpf=cpf)
            if self.instance.pk:
                duplicado = duplicado.exclude(pk=self.instance.pk)
            if duplicado.exists():
                raise forms.ValidationError("Ja existe um funcionario com este CPF na filial.")
        return cpf

    def clean_telefone(self):
        return "".join(filter(str.isdigit, self.cleaned_data.get("telefone", "") or ""))

    def clean_foto(self):
        foto = self.cleaned_data.get("foto")
        if not foto or foto is False or not hasattr(foto, "size"):
            return foto
        if foto.size > LIMITE_FOTO_FUNCIONARIO_BYTES:
            raise forms.ValidationError("Envie uma foto com no maximo 5 MB.")
        content_type = getattr(foto, "content_type", "")
        if content_type and content_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise forms.ValidationError("Use uma foto PNG, JPG ou WEBP.")
        return foto
