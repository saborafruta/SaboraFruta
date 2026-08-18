from django import forms

from apps.core.models import (
    Empresa, Filial, PerfilAcesso, Permissao, PoliticaReplicacao, Usuario,
    PoliticaReplicacaoFilial, UsuarioFilialAcesso,
)


def _digits(value):
    return ''.join(ch for ch in (value or '') if ch.isdigit())


def _actor_is_company_admin(actor):
    return bool(actor and (actor.is_superuser or getattr(getattr(actor, 'perfil', None), 'is_admin', False)))


def _politica_defaults_from_empresa(filial):
    campos = [
        'ativo',
        'replicar_clientes',
        'replicar_fornecedores',
        'replicar_produtos_basicos',
        'replicar_categorias',
        'replicar_marcas',
        'replicar_unidades',
        'replicar_tabelas_preco',
        'replicar_preco_venda',
        'replicar_custo_base',
        'replicar_fiscal_basico',
        'replicar_ficha_tecnica',
        'replicar_qualidade',
        'replicar_transportadoras',
        'replicar_representantes',
        'perguntar_ao_salvar',
    ]
    defaults = {'ativo': True}
    if not filial or not filial.empresa_id:
        return defaults
    try:
        politica_empresa = filial.empresa.politica_replicacao
    except PoliticaReplicacao.DoesNotExist:
        return defaults
    except Exception:
        return defaults
    for campo in campos:
        defaults[campo] = getattr(politica_empresa, campo, False)
    return defaults


def get_or_create_politica_filial(filial):
    defaults = _politica_defaults_from_empresa(filial)
    politica, _ = PoliticaReplicacaoFilial.objects.get_or_create(
        filial=filial,
        defaults=defaults,
    )
    return politica


class EmpresaAdminForm(forms.ModelForm):
    class Meta:
        model = Empresa
        exclude = [
            'created_at',
            'updated_at',
            'certificado_digital_path',
            'certificado_senha_hash',
            'certificado_validade',
            # Gerenciado pela tela de Módulos, na Central. Como JSONField ele
            # apareceria aqui como caixa de texto crua, e um colchete a menos
            # deixaria a empresa sem módulo nenhum.
            'modulos_extras',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['codigo_regime_tributario'].label = 'Codigo do regime tributario'
        self.fields['ambiente_nfe'].label = 'Ambiente NFe'
        if 'segmento' in self.fields:
            self.fields['segmento'].label = 'Segmento (vertical)'
            self.fields['segmento'].help_text = (
                'Libera os módulos especializados do ramo. '
                'Também dá para definir pela Central Administrativa.'
            )

    def clean_cnpj(self):
        cnpj = _digits(self.cleaned_data.get('cnpj'))
        if len(cnpj) != 14:
            raise forms.ValidationError('CNPJ deve conter 14 digitos.')
        return cnpj

    def clean_cep(self):
        cep = _digits(self.cleaned_data.get('cep'))
        if cep and len(cep) != 8:
            raise forms.ValidationError('CEP deve conter 8 digitos.')
        return cep

    def clean_uf(self):
        return (self.cleaned_data.get('uf') or '').strip().upper()


class FilialAdminForm(forms.ModelForm):
    CAMPOS_REPLICACAO = [
        'ativo',
        'replicar_clientes',
        'replicar_fornecedores',
        'replicar_produtos_basicos',
        'replicar_categorias',
        'replicar_marcas',
        'replicar_unidades',
        'replicar_tabelas_preco',
        'replicar_preco_venda',
        'replicar_custo_base',
        'replicar_fiscal_basico',
        'replicar_ficha_tecnica',
        'replicar_qualidade',
        'replicar_transportadoras',
        'replicar_representantes',
    ]

    replicacao_ativa = forms.BooleanField(label='Replicacao ativa', required=False, initial=True)
    replicar_clientes = forms.BooleanField(label='Clientes', required=False, initial=True)
    replicar_fornecedores = forms.BooleanField(label='Fornecedores', required=False, initial=True)
    replicar_produtos_basicos = forms.BooleanField(label='Dados basicos do produto', required=False, initial=True)
    replicar_categorias = forms.BooleanField(label='Categorias', required=False, initial=True)
    replicar_marcas = forms.BooleanField(label='Fabricantes', required=False, initial=True)
    replicar_unidades = forms.BooleanField(label='Unidades', required=False, initial=True)
    replicar_tabelas_preco = forms.BooleanField(label='Tabelas de preco', required=False, initial=True)
    replicar_preco_venda = forms.BooleanField(label='Preco de venda', required=False, initial=True)
    replicar_custo_base = forms.BooleanField(label='Custo base', required=False, initial=True)
    replicar_fiscal_basico = forms.BooleanField(label='Fiscal basico', required=False, initial=True)
    replicar_ficha_tecnica = forms.BooleanField(label='Ficha tecnica', required=False, initial=True)
    replicar_qualidade = forms.BooleanField(label='Qualidade', required=False, initial=True)
    replicar_transportadoras = forms.BooleanField(label='Transportadoras', required=False, initial=True)
    replicar_representantes = forms.BooleanField(label='Representantes', required=False, initial=True)

    class Meta:
        model = Filial
        exclude = [
            'created_at',
            'updated_at',
            'certificado_digital_path',
            'certificado_senha_hash',
            'certificado_validade',
            'participa_replicacao',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['is_matriz'].label = 'É matriz?'
        self.fields['regime_tributario'].label = 'Regime tributario da filial'
        self.fields['codigo_regime_tributario'].label = 'Codigo do regime tributario'
        self.fields['ambiente_nfe'].label = 'Ambiente NFe'
        if 'segmento' in self.fields:
            self.fields['segmento'].label = 'Segmento (vertical)'
            self.fields['segmento'].help_text = (
                'Libera os módulos especializados do ramo. '
                'Também dá para definir pela Central Administrativa.'
            )
        self.fields['serie_nfe'].label = 'Serie NFe'
        self.fields['serie_nfce'].label = 'Serie NFCe'
        self.fields['serie_nfse'].label = 'Serie NFSe'
        self.fields['proximo_numero_nfe'].label = 'Proximo numero NFe'
        self.fields['proximo_numero_nfce'].label = 'Proximo numero NFCe'
        self.fields['proximo_numero_nfse'].label = 'Proximo numero NFSe'
        self.fields['focusnfe_token'].label = 'Token Focus NFe'
        self.fields['focusnfe_ambiente'].label = 'Ambiente Focus NFe'
        if self.instance and self.instance.pk:
            if not self.initial.get('regime_tributario') and self.instance.empresa_id:
                self.initial['regime_tributario'] = self.instance.empresa.regime_tributario
            if not self.initial.get('codigo_regime_tributario') and self.instance.empresa_id:
                self.initial['codigo_regime_tributario'] = self.instance.empresa.codigo_regime_tributario
        politica = None
        if self.instance and self.instance.pk and self.instance.empresa_id:
            try:
                politica = self.instance.politica_replicacao
            except PoliticaReplicacaoFilial.DoesNotExist:
                try:
                    politica = self.instance.empresa.politica_replicacao
                except PoliticaReplicacao.DoesNotExist:
                    politica = None
                except Exception:
                    politica = None
            except Exception:
                politica = None
        self.fields['replicacao_ativa'].initial = (
            self.instance.participa_replicacao if self.instance and self.instance.pk else True
        )
        if politica:
            for field_name in self.CAMPOS_REPLICACAO:
                if field_name == 'ativo':
                    continue
                form_field = 'replicacao_ativa' if field_name == 'ativo' else field_name
                self.fields[form_field].initial = getattr(politica, field_name, False)

    def clean_cnpj(self):
        cnpj = _digits(self.cleaned_data.get('cnpj'))
        if len(cnpj) != 14:
            raise forms.ValidationError('CNPJ deve conter 14 digitos.')
        return cnpj

    def clean_cep(self):
        cep = _digits(self.cleaned_data.get('cep'))
        if cep and len(cep) != 8:
            raise forms.ValidationError('CEP deve conter 8 digitos.')
        return cep

    def clean_uf(self):
        return (self.cleaned_data.get('uf') or '').strip().upper()

    def salvar_politica_replicacao(self, filial):
        participa_replicacao = self.cleaned_data.get('replicacao_ativa', False)
        if filial.participa_replicacao != participa_replicacao:
            filial.participa_replicacao = participa_replicacao
            filial.save(update_fields=['participa_replicacao', 'updated_at'])
        politica = get_or_create_politica_filial(filial)
        politica.ativo = True
        for field_name in self.CAMPOS_REPLICACAO:
            if field_name == 'ativo':
                continue
            setattr(politica, field_name, self.cleaned_data.get(field_name, False))
        politica.save()
        return politica


class PoliticaReplicacaoForm(forms.ModelForm):
    class Meta:
        model = PoliticaReplicacaoFilial
        fields = [
            'ativo',
            'replicar_clientes',
            'replicar_fornecedores',
            'replicar_produtos_basicos',
            'replicar_categorias',
            'replicar_marcas',
            'replicar_unidades',
            'replicar_tabelas_preco',
            'replicar_preco_venda',
            'replicar_custo_base',
            'replicar_fiscal_basico',
            'replicar_ficha_tecnica',
            'replicar_qualidade',
            'replicar_transportadoras',
            'replicar_representantes',
        ]


class UsuarioAdminForm(forms.ModelForm):
    senha = forms.CharField(
        label='Senha',
        widget=forms.PasswordInput(
            attrs={'autocomplete': 'new-password'},
            render_value=False,
        ),
        required=False,
        help_text='Obrigatoria ao criar. Deixe em branco ao editar para manter a senha atual.',
    )
    senha_confirmacao = forms.CharField(
        label='Confirmar senha',
        widget=forms.PasswordInput(
            attrs={'autocomplete': 'new-password'},
            render_value=False,
        ),
        required=False,
    )
    replicar_para_filiais = forms.ModelMultipleChoiceField(
        label='Replicar acesso para filiais',
        queryset=Filial.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='Cria o mesmo login e perfil selecionado para outras filiais da mesma empresa.',
    )

    class Meta:
        model = Usuario
        fields = [
            'empresa',
            'filial',
            'perfil',
            'nome',
            'cpf',
            'email',
            'telefone',
            'foto',
            'comissao_percentual',
            'pin_code',
            'pin_exige_supervisor',
            'ativo',
            'is_staff',
            'is_superuser',
        ]

    def __init__(self, *args, actor=None, scope_filial=None, super_admin_context=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.scope_filial = scope_filial
        self.super_admin_context = super_admin_context
        self.existing_user = None
        self.fields['pin_code'].label = 'PIN do PDV'
        self.fields['pin_code'].help_text = 'Codigo curto usado pelo usuario no PDV, se sua operacao exigir.'
        self.fields['pin_exige_supervisor'].label = 'Exigir supervisor no PDV'
        self.fields['pin_exige_supervisor'].help_text = 'Marque quando as acoes deste usuario no PDV precisam de liberacao de supervisor.'
        self.fields['comissao_percentual'].label = 'Comissao de venda (%)'
        self.fields['replicar_para_filiais'].label = 'Dar acesso tambem a outras filiais'
        self.fields['replicar_para_filiais'].help_text = 'Replica este mesmo login e perfil para as filiais escolhidas.'

        if actor and actor.is_superuser and super_admin_context:
            self.fields['empresa'].queryset = Empresa.objects.filter(ativo=True).order_by('razao_social')
            self.fields['empresa'].label = 'Empresa de referencia'
            self.fields['empresa'].help_text = 'Usada apenas como referencia tecnica. O super administrador acessa todas as empresas.'
            self.fields['filial'].required = False
            self.fields['filial'].widget = forms.HiddenInput()
            self.fields['perfil'].required = False
            self.fields['perfil'].widget = forms.HiddenInput()
            self.fields['pin_code'].required = False
            self.fields['pin_code'].widget = forms.HiddenInput()
            self.fields['pin_exige_supervisor'].required = False
            self.fields['pin_exige_supervisor'].widget = forms.HiddenInput()
            self.fields['comissao_percentual'].required = False
            self.fields['comissao_percentual'].widget = forms.HiddenInput()
            self.fields['is_staff'].required = False
            self.fields['is_staff'].widget = forms.HiddenInput()
            self.fields['is_superuser'].required = False
            self.fields['is_superuser'].widget = forms.HiddenInput()
            self.fields['replicar_para_filiais'].required = False
            self.fields['replicar_para_filiais'].widget = forms.HiddenInput()
            self.initial['is_staff'] = True
            self.initial['is_superuser'] = True
            return

        if 'is_staff' in self.fields:
            self.fields['is_staff'].required = False
            self.fields['is_staff'].widget = forms.HiddenInput()
        if 'is_superuser' in self.fields:
            self.fields['is_superuser'].required = False
            self.fields['is_superuser'].widget = forms.HiddenInput()

        if actor and actor.is_superuser and scope_filial:
            self.fields['empresa'].queryset = Empresa.objects.filter(pk=scope_filial.empresa_id)
            self.fields['empresa'].initial = scope_filial.empresa_id
            self.fields['filial'].queryset = Filial.objects.filter(pk=scope_filial.pk)
            self.fields['filial'].initial = scope_filial.pk
            self.fields['perfil'].queryset = PerfilAcesso.objects.filter(
                empresa_id=scope_filial.empresa_id,
                ativo=True,
            ).order_by('nome')
            self.fields['replicar_para_filiais'].queryset = Filial.objects.filter(
                empresa_id=scope_filial.empresa_id,
                ativo=True,
            ).exclude(pk=scope_filial.pk).order_by('razao_social')
            return

        if not actor or actor.is_superuser:
            self.fields['replicar_para_filiais'].queryset = Filial.objects.filter(
                ativo=True,
            ).select_related('empresa').order_by('empresa__razao_social', 'razao_social')
            return

        self.fields.pop('is_staff', None)
        self.fields.pop('is_superuser', None)
        working_filial = scope_filial or actor.filial

        self.fields['empresa'].queryset = Empresa.objects.filter(pk=actor.empresa_id)
        self.fields['empresa'].initial = actor.empresa_id
        self.fields['empresa'].disabled = True
        self.fields['empresa'].widget = forms.HiddenInput()

        filiais = Filial.objects.filter(empresa_id=actor.empresa_id, ativo=True)
        if working_filial:
            filiais = filiais.filter(pk=working_filial.pk)
            self.fields['filial'].initial = working_filial.pk
            self.fields['filial'].disabled = True
            self.fields['filial'].widget = forms.HiddenInput()
        self.fields['filial'].queryset = filiais

        perfis = PerfilAcesso.objects.filter(
            empresa_id=actor.empresa_id,
            ativo=True,
        )
        if not _actor_is_company_admin(actor):
            perfis = perfis.filter(is_admin=False)
        self.fields['perfil'].queryset = perfis.order_by('nome')
        self.fields['replicar_para_filiais'].queryset = Filial.objects.filter(
            empresa_id=actor.empresa_id,
            ativo=True,
        ).exclude(pk=working_filial.pk if working_filial else 0).order_by('razao_social')

    def clean_cpf(self):
        return _digits(self.cleaned_data.get('cpf'))

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        qs = Usuario.objects.filter(email__iexact=email)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            if self.instance.pk:
                raise forms.ValidationError('Ja existe um usuario com este e-mail.')
            self.existing_user = qs.first()
            self.existing_user_original = {
                'empresa_id': self.existing_user.empresa_id,
                'filial_id': self.existing_user.filial_id,
                'perfil_id': self.existing_user.perfil_id,
                'is_staff': self.existing_user.is_staff,
                'is_superuser': self.existing_user.is_superuser,
            }
            self.instance = self.existing_user
        return email

    def clean(self):
        cleaned = super().clean()
        senha = cleaned.get('senha')
        confirmacao = cleaned.get('senha_confirmacao')

        existing_user = getattr(self, 'existing_user', None)
        if not self.instance.pk and not existing_user and not senha:
            raise forms.ValidationError('Senha obrigatoria para novo usuario.')
        if self.instance.pk and senha and not confirmacao:
            cleaned['senha'] = ''
            cleaned['senha_confirmacao'] = ''
            senha = ''
        if senha and senha != confirmacao:
            raise forms.ValidationError('As senhas nao coincidem.')

        actor = getattr(self, 'actor', None)
        scope_filial = getattr(self, 'scope_filial', None)
        super_admin_context = getattr(self, 'super_admin_context', False)
        perfil = cleaned.get('perfil')
        filial = cleaned.get('filial')
        filiais_replicadas = cleaned.get('replicar_para_filiais')
        if actor and actor.is_superuser and super_admin_context:
            empresa = cleaned.get('empresa') or Empresa.objects.filter(ativo=True).order_by('razao_social').first()
            if not empresa:
                raise forms.ValidationError('Cadastre uma empresa antes de criar um super administrador.')
            if existing_user and not existing_user.is_superuser:
                raise forms.ValidationError('Este e-mail ja pertence a um usuario comum. Edite o usuario original ou use outro e-mail.')
            perfil_padrao = PerfilAcesso.objects.filter(empresa=empresa, is_admin=True).order_by('nome').first()
            if not perfil_padrao:
                perfil_padrao = PerfilAcesso.objects.filter(empresa=empresa).order_by('nome').first()
            if not perfil_padrao:
                perfil_padrao = PerfilAcesso.objects.create(
                    empresa=empresa,
                    nome='Super Administrador',
                    descricao='Perfil tecnico para usuarios super administradores.',
                    is_admin=True,
                    ativo=True,
                )
            cleaned['empresa'] = empresa
            cleaned['filial'] = None
            cleaned['perfil'] = perfil_padrao
            cleaned['is_staff'] = True
            cleaned['is_superuser'] = True
            return cleaned

        for filial_replicada in filiais_replicadas or []:
            if perfil and filial_replicada.empresa_id != perfil.empresa_id:
                raise forms.ValidationError('As filiais replicadas precisam pertencer a empresa do perfil.')
        if perfil and filial and perfil.empresa_id != filial.empresa_id:
            raise forms.ValidationError('Perfil e filial precisam pertencer a mesma empresa.')
        if existing_user:
            if actor and not actor.is_superuser:
                if existing_user.is_superuser or existing_user.empresa_id != actor.empresa_id:
                    raise forms.ValidationError('Este e-mail ja pertence a outro escopo de acesso.')
            if actor and actor.is_superuser and scope_filial and existing_user.empresa_id != scope_filial.empresa_id:
                raise forms.ValidationError('Este e-mail ja pertence a outra empresa. Use outro e-mail ou ajuste o usuario original.')
            if perfil and existing_user.empresa_id != perfil.empresa_id:
                raise forms.ValidationError('O perfil precisa pertencer a empresa original deste usuario.')
        if actor and actor.is_superuser and scope_filial:
            cleaned['empresa'] = scope_filial.empresa
            cleaned['filial'] = scope_filial
            perfil = cleaned.get('perfil')
            if perfil and perfil.empresa_id != scope_filial.empresa_id:
                raise forms.ValidationError('Perfil fora da empresa/filial ativa.')
            return cleaned

        if actor and not actor.is_superuser:
            cleaned['empresa'] = actor.empresa
            working_filial = scope_filial or actor.filial
            if working_filial:
                cleaned['filial'] = working_filial

            perfil = cleaned.get('perfil')
            filial = cleaned.get('filial')
            if perfil and perfil.empresa_id != actor.empresa_id:
                raise forms.ValidationError('Perfil fora do escopo permitido.')
            if perfil and perfil.is_admin and not _actor_is_company_admin(actor):
                raise forms.ValidationError('Perfil fora do escopo permitido.')
            if filial and filial.empresa_id != actor.empresa_id:
                raise forms.ValidationError('Filial fora do escopo permitido.')

        return cleaned

    def validate_unique(self):
        try:
            super().validate_unique()
        except forms.ValidationError as exc:
            if getattr(self, 'existing_user', None) and 'email' in getattr(exc, 'error_dict', {}):
                exc.error_dict.pop('email', None)
                if exc.error_dict:
                    raise exc
            else:
                raise

    def save(self, commit=True):
        existing_user = getattr(self, 'existing_user', None)
        if existing_user:
            usuario = existing_user
            original = getattr(self, 'existing_user_original', {})
            for field, value in original.items():
                setattr(usuario, field, value)
            for field in [
                'nome',
                'cpf',
                'telefone',
                'foto',
                'comissao_percentual',
                'pin_code',
                'pin_exige_supervisor',
                'ativo',
            ]:
                if field in self.cleaned_data:
                    setattr(usuario, field, self.cleaned_data[field])
            senha = self.cleaned_data.get('senha')
            if senha:
                usuario.set_password(senha)
            if commit:
                usuario.save()
                self._salvar_acessos(usuario)
            return usuario

        usuario = super().save(commit=False)
        actor = getattr(self, 'actor', None)
        scope_filial = getattr(self, 'scope_filial', None)
        super_admin_context = getattr(self, 'super_admin_context', False)
        if actor and actor.is_superuser and super_admin_context:
            usuario.empresa = self.cleaned_data['empresa']
            usuario.filial = None
            usuario.perfil = self.cleaned_data['perfil']
            usuario.is_staff = True
            usuario.is_superuser = True
            usuario.pin_code = ''
            usuario.pin_exige_supervisor = False
            usuario.comissao_percentual = 0
        if actor and actor.is_superuser and scope_filial:
            usuario.empresa = scope_filial.empresa
            usuario.filial = scope_filial
        if actor and not actor.is_superuser:
            usuario.empresa = actor.empresa
            working_filial = scope_filial or actor.filial
            if working_filial:
                usuario.filial = working_filial
            usuario.is_staff = False
            usuario.is_superuser = False
        senha = self.cleaned_data.get('senha')
        if senha:
            usuario.set_password(senha)
        if commit:
            usuario.save()
            self.save_m2m()
            self._salvar_acessos(usuario)
        return usuario

    def _salvar_acessos(self, usuario):
        if getattr(self, 'super_admin_context', False):
            return
        perfil = self.cleaned_data.get('perfil')
        filial = self.cleaned_data.get('filial')
        if not perfil:
            return
        filiais = []
        if filial:
            filiais.append(filial)
        filiais.extend(list(self.cleaned_data.get('replicar_para_filiais') or []))

        for filial_item in filiais:
            if filial_item.empresa_id != perfil.empresa_id:
                continue
            UsuarioFilialAcesso.objects.update_or_create(
                usuario=usuario,
                filial=filial_item,
                defaults={
                    'perfil': perfil,
                    'ativo': True,
                    'is_padrao': filial and filial_item.pk == filial.pk,
                },
            )


class PerfilAcessoAdminForm(forms.ModelForm):
    class Meta:
        model = PerfilAcesso
        fields = ['empresa', 'nome', 'descricao', 'is_admin', 'ativo']
        widgets = {'descricao': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, actor=None, scope_filial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.scope_filial = scope_filial

        if actor and actor.is_superuser and scope_filial:
            self.fields['empresa'].queryset = Empresa.objects.filter(pk=scope_filial.empresa_id)
            self.fields['empresa'].initial = scope_filial.empresa_id
            return

        if not actor or actor.is_superuser:
            return

        if not _actor_is_company_admin(actor):
            self.fields.pop('is_admin', None)
        self.fields['empresa'].queryset = Empresa.objects.filter(pk=actor.empresa_id)
        self.fields['empresa'].initial = actor.empresa_id
        self.fields['empresa'].disabled = True
        self.fields['empresa'].widget = forms.HiddenInput()

    def clean(self):
        cleaned = super().clean()
        actor = getattr(self, 'actor', None)
        scope_filial = getattr(self, 'scope_filial', None)
        if actor and actor.is_superuser and scope_filial:
            cleaned['empresa'] = scope_filial.empresa
            return cleaned

        if actor and not actor.is_superuser:
            cleaned['empresa'] = actor.empresa
        return cleaned

    def save(self, commit=True):
        perfil = super().save(commit=False)
        actor = getattr(self, 'actor', None)
        scope_filial = getattr(self, 'scope_filial', None)
        if actor and actor.is_superuser and scope_filial:
            perfil.empresa = scope_filial.empresa
        if actor and not actor.is_superuser:
            perfil.empresa = actor.empresa
            if not _actor_is_company_admin(actor):
                perfil.is_admin = False
        if commit:
            perfil.save()
            self.save_m2m()
        return perfil


class PermissaoMatrix:
    fields = [
        'pode_ver',
        'pode_criar',
        'pode_editar',
        'pode_excluir',
        'pode_cancelar',
        'pode_aprovar',
        'pode_exportar',
    ]
    labels = {
        'pode_ver': 'Ver',
        'pode_criar': 'Criar',
        'pode_editar': 'Editar',
        'pode_excluir': 'Excluir',
        'pode_cancelar': 'Cancelar',
        'pode_aprovar': 'Aprovar',
        'pode_exportar': 'Exportar',
    }

    def __init__(self, perfil=None, data=None):
        self.perfil = perfil
        self.data = data
        self.modules = list(Permissao.Modulo.choices)

    @property
    def field_labels(self):
        return [self.labels[field] for field in self.fields]

    def rows(self):
        existing = {}
        if self.perfil:
            for permissao in self.perfil.permissoes.all():
                existing[permissao.modulo] = {
                    field: getattr(permissao, field) for field in self.fields
                }

        rows = []
        for code, label in self.modules:
            current = existing.get(code, {})
            rows.append({
                'code': code,
                'label': label,
                'cells': [
                    {
                        'name': f'perm_{code}_{field}',
                        'label': self.labels[field],
                        'action': field,
                        'checked': current.get(field, False),
                    }
                    for field in self.fields
                ],
            })
        return rows

    def save(self, perfil):
        if self.data is None:
            return
        for code, _label in self.modules:
            defaults = {
                field: self.data.get(f'perm_{code}_{field}') == 'on'
                for field in self.fields
            }
            Permissao.objects.update_or_create(
                perfil=perfil,
                modulo=code,
                defaults=defaults,
            )
