import json
from decimal import Decimal, InvalidOperation

from django import forms
from django.db import models
from django.db.models import Q

from apps.cadastros.models import Fornecedor
from apps.produtos.models import (
    CategoriaProduto, ClasseFiscal, LinhaProducao, MarcaProduto, Produto, UnidadeMedida,
)


def _queryset_com_atual(queryset, current_id):
    filtro = Q(pk__in=queryset.values('pk'))
    if current_id:
        filtro |= Q(pk=current_id)
    return queryset.model.objects.filter(filtro)


def _aceitar_decimal_br(field):
    original_to_python = field.to_python

    def to_python(value):
        if isinstance(value, str):
            value = value.strip()
            if ',' in value:
                value = value.replace('.', '').replace(',', '.')
        return original_to_python(value)

    field.to_python = to_python


def _formatar_decimal_br(value, places=2):
    if value in (None, ''):
        return value
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value
    return f'{decimal_value:.{places}f}'.replace('.', ',')


class ProdutoForm(forms.ModelForm):
    DECIMAL_2_FIELDS = {
        'fator_conversao_compra',
        'aliquota_icms',
        'reducao_bc_icms',
        'aliquota_fcp',
        'mva_icms_st',
        'reducao_bc_icms_st',
        'aliquota_icms_st',
        'aliquota_fcp_st',
        'aliquota_ipi',
        'aliquota_pis',
        'aliquota_cofins',
        'aliquota_cbs',
        'reducao_cbs',
        'aliquota_ibs_uf',
        'aliquota_ibs_municipal',
        'reducao_ibs',
        'aliquota_is',
        'estoque_quantidade',
        'estoque_minimo',
        'estoque_maximo',
        'ponto_reposicao',
        'estoque_seguranca',
        'tara_padrao',
        'variacao_peso_permitida',
        'peso_minimo_venda',
        'peso_bruto',
        'peso_liquido',
        'largura',
        'altura',
        'profundidade',
        'quantidade_por_embalagem',
        'temperatura_minima',
        'temperatura_maxima',
        'umidade_relativa',
    }
    STOCK_DECIMAL_FIELDS = {
        'estoque_quantidade',
        'estoque_minimo',
        'estoque_maximo',
        'ponto_reposicao',
        'estoque_seguranca',
    }
    STRICT_DECIMAL_2_FIELDS = DECIMAL_2_FIELDS - STOCK_DECIMAL_FIELDS
    DEFAULT_IF_EMPTY_FIELDS = {
        'fator_conversao_compra',
        'origem_produto',
        'aliquota_icms',
        'reducao_bc_icms',
        'aliquota_fcp',
        'mva_icms_st',
        'reducao_bc_icms_st',
        'aliquota_icms_st',
        'aliquota_fcp_st',
        'aliquota_ipi',
        'aliquota_pis',
        'aliquota_cofins',
        'aliquota_cbs',
        'reducao_cbs',
        'aliquota_ibs_uf',
        'aliquota_ibs_municipal',
        'reducao_ibs',
        'aliquota_is',
        'estoque_minimo',
        'estoque_maximo',
        'ponto_reposicao',
        'estoque_seguranca',
        'lead_time_reposicao_dias',
        'dias_aviso_vencimento',
        'tara_padrao',
        'variacao_peso_permitida',
        'peso_minimo_venda',
        'quantidade_por_embalagem',
        'empilhamento_maximo',
    }

    imagem_produto = forms.FileField(label='Imagem do produto', required=False)
    remover_imagem = forms.BooleanField(label='Remover imagem atual', required=False)
    codigo_barras_extra_1 = forms.CharField(label='Codigo de barras extra 1', required=False, max_length=14)
    codigo_barras_extra_2 = forms.CharField(label='Codigo de barras extra 2', required=False, max_length=14)
    codigo_barras_extra_3 = forms.CharField(label='Codigo de barras extra 3', required=False, max_length=14)
    estoque_quantidade = forms.DecimalField(
        label='Quantidade em estoque',
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=4,
        help_text='Informe o saldo real desta filial.',
    )

    class Meta:
        model = Produto
        exclude = [
            'filial', 'preco_custo_medio', 'margem_lucro', 'markup',
            'preco_sugerido', 'saida_fefo', 'created_at', 'updated_at',
            'foto_url', 'preco_promocional', 'promocao_tipo_desconto',
            'promocao_valor_desconto', 'promocao_inicio', 'promocao_fim',
            'promocao_dias_semana',
        ]
        labels = {
            'descricao': 'Nome do produto',
            'codigo': 'Referencia / Codigo interno',
            'descricao_curta': 'Descricao curta',
            'marca': 'Marca / Fabricante',
            'fornecedor': 'Fornecedor',
            'subcategoria': 'Sub categoria',
            'linha_producao': 'Familia / Linha',
            'tempo_preparo_minutos': 'Tempo de preparo (min)',
            'ativo': 'Status ativo',
            'rascunho_comercial': 'Rascunho comercial',
            'observacao': 'Observacao interna',
            'unidade_medida': 'Unidade de medida',
            'unidade_medida_compra': 'Unidade de compra',
            'fator_conversao_compra': 'Conversao',
            'cfop_venda_interna': 'CFOP venda dentro do estado',
            'cfop_venda_interestadual': 'CFOP venda para outro estado',
            'cfop_venda_exportacao': 'CFOP venda para exportacao',
            'cfop_compra': 'CFOP compra / entrada',
            'cfop_devolucao': 'CFOP devolucao de venda',
            'cfop_devolucao_compra': 'CFOP devolucao de compra',
            'codigo_beneficio_fiscal_icms': 'Beneficio fiscal ICMS',
            'cst_csosn': 'CST / CSOSN (ICMS)',
            'modalidade_bc_icms': 'Modalidade BC ICMS',
            'aliquota_icms': 'Aliquota ICMS (%)',
            'reducao_bc_icms': 'Reducao BC ICMS (%)',
            'aliquota_fcp': 'Aliquota FCP (%)',
            'modalidade_bc_icms_st': 'Modalidade BC ICMS-ST',
            'mva_icms_st': 'MVA ICMS-ST (%)',
            'reducao_bc_icms_st': 'Reducao BC ICMS-ST (%)',
            'aliquota_icms_st': 'Aliquota ICMS-ST (%)',
            'aliquota_fcp_st': 'Aliquota FCP-ST (%)',
            'codigo_enquadramento_ipi': 'Enquadramento IPI',
            'aliquota_ipi': 'Aliquota IPI (%)',
            'aliquota_pis': 'Aliquota PIS (%)',
            'aliquota_cofins': 'Aliquota COFINS (%)',
            'natureza_receita_pis_cofins': 'Natureza receita PIS/COFINS',
            'ex_tipi': 'EX TIPI',
            'cst_cbs': 'CST CBS',
            'classificacao_tributaria_cbs': 'Classificacao tributaria CBS',
            'aliquota_cbs': 'Aliquota CBS (%)',
            'reducao_cbs': 'Reducao CBS (%)',
            'cst_ibs': 'CST IBS',
            'classificacao_tributaria_ibs': 'Classificacao tributaria IBS',
            'aliquota_ibs_uf': 'Aliquota IBS UF (%)',
            'aliquota_ibs_municipal': 'Aliquota IBS municipal (%)',
            'reducao_ibs': 'Reducao IBS (%)',
            'cst_is': 'CST IS',
            'classificacao_tributaria_is': 'Classificacao tributaria IS',
            'aliquota_is': 'Aliquota IS (%)',
            'preco_custo': 'Preco de custo (Custo de producao)',
            'preco_venda': 'Preco de venda',
            'preco_minimo': 'Preco minimo',
            'estoque_seguranca': 'Estoque de seguranca',
            'lead_time_reposicao_dias': 'Lead time de reposicao (dias)',
            'metodo_saida': 'Metodo de saida',
            'dias_aviso_vencimento': 'Dias para alerta de vencimento',
            'codigo_balanca': 'Codigo da balanca',
            'tara_padrao': 'Tara padrao (kg)',
            'peso_minimo_venda': 'Peso minimo de venda (kg)',
            'fracionavel': 'Produto fracionado',
            'vendido_por_peso_granel': 'Produto vendido por peso / granel',
            'gera_etiqueta_balanca': 'Gera etiqueta de balanca',
            'profundidade': 'Comprimento',
            'tipo_embalagem': 'Tipo de embalagem',
            'quantidade_por_embalagem': 'Quantidade por embalagem',
            'empilhamento_maximo': 'Empilhamento maximo',
            'condicao_armazenamento': 'Condicao de armazenamento',
            'temperatura_minima': 'Temperatura minima (C)',
            'temperatura_maxima': 'Temperatura maxima (C)',
            'umidade_relativa': 'Umidade relativa (%)',
        }
        widgets = {
            'descricao': forms.TextInput(attrs={'placeholder': 'Digite o nome do produto'}),
            'codigo': forms.TextInput(attrs={'placeholder': 'Ex.: REF-00123'}),
            'codigo_barras': forms.TextInput(attrs={'placeholder': 'EAN-13', 'maxlength': '14'}),
            'descricao_curta': forms.TextInput(attrs={'placeholder': 'Resumo do produto para identificacao rapida', 'maxlength': '120'}),
            'descricao_completa': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Descricao completa do produto'}),
            'observacao': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Informacoes internas sobre o produto'}),
            'ncm': forms.TextInput(attrs={'placeholder': 'Ex.: 82041100', 'maxlength': '8'}),
            'cest': forms.TextInput(attrs={'placeholder': 'Ex.: 1234567', 'maxlength': '7'}),
            'cfop_venda_interna': forms.TextInput(attrs={'placeholder': '5102', 'maxlength': '5'}),
            'cfop_venda_interestadual': forms.TextInput(attrs={'placeholder': '6102', 'maxlength': '5'}),
            'cfop_venda_exportacao': forms.TextInput(attrs={'placeholder': '7102', 'maxlength': '5'}),
            'cfop_compra': forms.TextInput(attrs={'placeholder': '1102', 'maxlength': '5'}),
            'cfop_devolucao': forms.TextInput(attrs={'placeholder': '5202', 'maxlength': '5'}),
            'cfop_devolucao_compra': forms.TextInput(attrs={'placeholder': '1202', 'maxlength': '5'}),
            'codigo_beneficio_fiscal_icms': forms.TextInput(attrs={'placeholder': 'Ex.: RN000001', 'maxlength': '20'}),
            'cst_csosn': forms.TextInput(attrs={'placeholder': '102', 'maxlength': '3'}),
            'cst_pis': forms.TextInput(attrs={'placeholder': '01', 'maxlength': '2'}),
            'cst_cofins': forms.TextInput(attrs={'placeholder': '01', 'maxlength': '2'}),
            'cst_ipi': forms.TextInput(attrs={'placeholder': '99', 'maxlength': '2'}),
            'codigo_enquadramento_ipi': forms.TextInput(attrs={'placeholder': '999', 'maxlength': '3'}),
            'modalidade_bc_icms': forms.TextInput(attrs={'placeholder': 'Ex.: 3', 'maxlength': '2'}),
            'modalidade_bc_icms_st': forms.TextInput(attrs={'placeholder': 'Ex.: 4', 'maxlength': '2'}),
            'natureza_receita_pis_cofins': forms.TextInput(attrs={'placeholder': 'Ex.: 999', 'maxlength': '3'}),
            'ex_tipi': forms.TextInput(attrs={'placeholder': 'Ex.: 01', 'maxlength': '3'}),
            'cst_cbs': forms.TextInput(attrs={'placeholder': 'Ex.: 000', 'maxlength': '3'}),
            'classificacao_tributaria_cbs': forms.TextInput(attrs={'placeholder': 'Ex.: 000001', 'maxlength': '6'}),
            'cst_ibs': forms.TextInput(attrs={'placeholder': 'Ex.: 000', 'maxlength': '3'}),
            'classificacao_tributaria_ibs': forms.TextInput(attrs={'placeholder': 'Ex.: 000001', 'maxlength': '6'}),
            'cst_is': forms.TextInput(attrs={'placeholder': 'Ex.: 000', 'maxlength': '3'}),
            'classificacao_tributaria_is': forms.TextInput(attrs={'placeholder': 'Ex.: 000001', 'maxlength': '6'}),
            'preco_custo': forms.TextInput(attrs={'inputmode': 'decimal', 'placeholder': '0,00', 'data-decimal-places': '2'}),
            'preco_venda': forms.TextInput(attrs={'inputmode': 'decimal', 'placeholder': '0,00', 'data-decimal-places': '2'}),
            'preco_minimo': forms.TextInput(attrs={'inputmode': 'decimal', 'placeholder': '0,00', 'data-decimal-places': '2'}),
            'especificacoes_tecnicas': forms.HiddenInput(),
        }

    def __init__(self, *args, empresa=None, filial=None, estoque_atual=None, **kwargs):
        super().__init__(*args, **kwargs)
        if estoque_atual is not None:
            self.fields['estoque_quantidade'].initial = estoque_atual
        self.fields['estoque_quantidade'].widget.attrs.update({
            'class': 'product-input estoque-qty-input',
            'step': '0.01',
            'min': '0',
            'placeholder': '0,00',
            'data-stock-input': 'true',
            'data-stock-decimal-input': 'true',
        })
        self.fields['imagem_produto'].widget.attrs.update({
            'class': 'produto-image-file',
            'accept': 'image/png,image/jpeg,image/webp,image/gif',
        })
        self.fields['remover_imagem'].widget.attrs.update({'class': 'produto-image-remove-checkbox'})

        for money_field in ('preco_custo', 'preco_venda', 'preco_minimo'):
            value = self.initial.get(money_field)
            if value is None and self.instance and getattr(self.instance, 'pk', None):
                value = getattr(self.instance, money_field, None)
            if value not in (None, '') and not self.is_bound:
                self.initial[money_field] = f'{value:.2f}'

        for decimal_field in self.DECIMAL_2_FIELDS:
            if decimal_field not in self.fields:
                continue
            field = self.fields[decimal_field]
            if decimal_field in self.STRICT_DECIMAL_2_FIELDS and isinstance(field, forms.DecimalField):
                field.decimal_places = 2
            field.widget.attrs.update({
                'inputmode': 'decimal',
                'step': '0.01',
                'placeholder': '0,00',
                'data-decimal-places': '2',
            })
            if decimal_field in self.STOCK_DECIMAL_FIELDS:
                field.widget.attrs['data-stock-decimal-input'] = 'true'
            value = self.initial.get(decimal_field)
            if value is None and self.instance and getattr(self.instance, 'pk', None) and hasattr(self.instance, decimal_field):
                value = getattr(self.instance, decimal_field, None)
            if value not in (None, '') and not self.is_bound:
                self.initial[decimal_field] = _formatar_decimal_br(value, 2)

        extras = []
        if self.instance and self.instance.pk and isinstance(self.instance.codigos_barras_extras, list):
            extras = self.instance.codigos_barras_extras
        for index in range(3):
            field_name = f'codigo_barras_extra_{index + 1}'
            self.fields[field_name].initial = extras[index] if len(extras) > index else ''
            self.fields[field_name].widget.attrs.update({
                'class': 'product-input',
                'placeholder': f'Codigo alternativo {index + 1}',
            })

        specs = self.instance.especificacoes_tecnicas if self.instance and self.instance.pk else []
        if isinstance(specs, list):
            self.fields['especificacoes_tecnicas'].initial = json.dumps(specs, ensure_ascii=False)

        if not self.is_bound and not getattr(self.instance, 'pk', None):
            self.fields['tipo_produto'].initial = Produto.TipoProduto.UNITARIO
            self.fields['ativo'].initial = True
            self.fields['permite_venda_sem_estoque'].initial = True

        for field_name in (
            'codigo',
            'preco_minimo',
            'codigo_beneficio_fiscal_icms',
            'modalidade_bc_icms',
            'modalidade_bc_icms_st',
            'natureza_receita_pis_cofins',
            'ex_tipi',
            'cst_cbs',
            'classificacao_tributaria_cbs',
            'cst_ibs',
            'classificacao_tributaria_ibs',
            'cst_is',
            'classificacao_tributaria_is',
            'peso_bruto',
            'peso_liquido',
            'largura',
            'altura',
            'profundidade',
            'tipo_embalagem',
        ):
            if field_name in self.fields:
                self.fields[field_name].required = False

        for field_name in (
            'descricao',
            'categoria',
            'tipo_produto',
            'unidade_medida',
            'fator_conversao_compra',
            'condicao_armazenamento',
            'ncm',
            'origem_produto',
            'cfop_venda_interna',
            'cfop_venda_interestadual',
            'cfop_compra',
            'preco_venda',
            'preco_custo',
            'estoque_minimo',
            'ponto_reposicao',
            'metodo_saida',
            'unidade_pesagem',
            'unidade_peso',
            'unidade_dimensao',
            'quantidade_por_embalagem',
            'empilhamento_maximo',
        ):
            if field_name in self.fields:
                self.fields[field_name].required = True

        for field_name in self.DEFAULT_IF_EMPTY_FIELDS:
            if field_name in self.fields:
                self.fields[field_name].required = False

        if empresa:
            categoria_qs = CategoriaProduto.objects.filter(
                empresa=empresa, ativo=True, categoria_pai__isnull=True,
            )
            if filial:
                categoria_qs = CategoriaProduto.objects.for_filial(filial).filter(
                    empresa=empresa, ativo=True, categoria_pai__isnull=True,
                )
            categoria_qs = _queryset_com_atual(categoria_qs, getattr(self.instance, 'categoria_id', None))
            self.fields['categoria'].queryset = categoria_qs.distinct().order_by('nome')

            subcategoria_qs = CategoriaProduto.objects.filter(
                empresa=empresa, ativo=True, categoria_pai__isnull=False,
            )
            if filial:
                subcategoria_qs = CategoriaProduto.objects.for_filial(filial).filter(
                    empresa=empresa, ativo=True, categoria_pai__isnull=False,
                )
            subcategoria_qs = _queryset_com_atual(subcategoria_qs, getattr(self.instance, 'subcategoria_id', None))
            self.fields['subcategoria'].queryset = subcategoria_qs.distinct().order_by('categoria_pai__nome', 'nome')
            self.fields['subcategoria'].label_from_instance = lambda obj: obj.nome
            self.fields['linha_producao'].queryset = LinhaProducao.objects.filter(
                empresa=empresa, ativo=True,
            ).order_by('nome')
            marca_qs = MarcaProduto.objects.filter(
                empresa=empresa, ativo=True,
            )
            if filial:
                marca_qs = MarcaProduto.objects.for_filial(filial).filter(
                    empresa=empresa, ativo=True,
                )
            marca_qs = _queryset_com_atual(marca_qs, getattr(self.instance, 'marca_id', None))
            self.fields['marca'].queryset = marca_qs.distinct().order_by('nome')

            fornecedor_qs = Fornecedor.objects.filter(
                filial__empresa=empresa, ativo=True,
            )
            if filial:
                fornecedor_qs = Fornecedor.objects.for_filial(filial).filter(ativo=True)
            fornecedor_qs = _queryset_com_atual(fornecedor_qs, getattr(self.instance, 'fornecedor_id', None))
            self.fields['fornecedor'].queryset = fornecedor_qs.distinct().order_by('nome_fantasia', 'razao_social')

            unidade_qs = UnidadeMedida.objects.filter(
                empresa=empresa, ativo=True,
            )
            if filial:
                unidade_qs = UnidadeMedida.objects.for_filial(filial).filter(
                    empresa=empresa, ativo=True,
                )
            unidade_ids_atuais = [
                value for value in (
                    getattr(self.instance, 'unidade_medida_id', None),
                    getattr(self.instance, 'unidade_medida_compra_id', None),
                ) if value
            ]
            unidade_filter = Q(pk__in=unidade_qs.values('pk'))
            if unidade_ids_atuais:
                unidade_filter |= Q(pk__in=unidade_ids_atuais)
            unidade_qs = UnidadeMedida.objects.filter(unidade_filter)
            self.fields['unidade_medida'].queryset = unidade_qs.distinct().order_by('sigla')
            self.fields['unidade_medida_compra'].queryset = unidade_qs.distinct().order_by('sigla')

            classe_qs = ClasseFiscal.objects.filter(
                empresa=empresa, ativo=True,
            )
            if filial:
                classe_qs = ClasseFiscal.objects.for_filial(filial).filter(
                    empresa=empresa, ativo=True,
                )
            classe_qs = _queryset_com_atual(classe_qs, getattr(self.instance, 'classe_fiscal_id', None))
            self.fields['classe_fiscal'].queryset = classe_qs.distinct().order_by('codigo')
            if not self.is_bound and not getattr(self.instance, 'pk', None):
                unidade_padrao = self.fields['unidade_medida'].queryset.filter(
                    sigla__iexact='UN',
                ).first() or self.fields['unidade_medida'].queryset.filter(
                    sigla__iexact='UND',
                ).first() or self.fields['unidade_medida'].queryset.filter(
                    descricao__icontains='unidade',
                ).first()
                if unidade_padrao:
                    self.fields['unidade_medida'].initial = unidade_padrao.pk

        for name, field in self.fields.items():
            if isinstance(field, forms.DecimalField):
                _aceitar_decimal_br(field)
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.update({'class': 'product-checkbox'})
            elif isinstance(field.widget, forms.Select):
                attrs = {'class': 'product-input'}
                if field.required:
                    attrs['required'] = 'required'
                field.widget.attrs.update(attrs)
            elif isinstance(field.widget, forms.HiddenInput):
                continue
            else:
                attrs = {'class': field.widget.attrs.get('class', 'product-input')}
                if isinstance(field, forms.DecimalField):
                    attrs['data-decimal-places'] = '2'
                field.widget.attrs.update(attrs)

    def clean_ncm(self):
        ncm = ''.join(filter(str.isdigit, self.cleaned_data.get('ncm', '') or ''))
        if ncm and len(ncm) != 8:
            raise forms.ValidationError('NCM deve conter 8 digitos.')
        return ncm

    def _clean_digits_field(self, field_name, max_len):
        value = ''.join(filter(str.isdigit, self.cleaned_data.get(field_name, '') or ''))
        return value[:max_len]

    def clean_cest(self):
        return self._clean_digits_field('cest', 7)

    def clean_cst_csosn(self):
        return self._clean_digits_field('cst_csosn', 3)

    def clean_cst_pis(self):
        return self._clean_digits_field('cst_pis', 2)

    def clean_cst_cofins(self):
        return self._clean_digits_field('cst_cofins', 2)

    def clean_cst_ipi(self):
        return self._clean_digits_field('cst_ipi', 2)

    def clean_codigo_enquadramento_ipi(self):
        return self._clean_digits_field('codigo_enquadramento_ipi', 3)

    def clean_natureza_receita_pis_cofins(self):
        return self._clean_digits_field('natureza_receita_pis_cofins', 3)

    def clean_cst_cbs(self):
        return self._clean_digits_field('cst_cbs', 3)

    def clean_classificacao_tributaria_cbs(self):
        return self._clean_digits_field('classificacao_tributaria_cbs', 6)

    def clean_cst_ibs(self):
        return self._clean_digits_field('cst_ibs', 3)

    def clean_classificacao_tributaria_ibs(self):
        return self._clean_digits_field('classificacao_tributaria_ibs', 6)

    def clean_cst_is(self):
        return self._clean_digits_field('cst_is', 3)

    def clean_classificacao_tributaria_is(self):
        return self._clean_digits_field('classificacao_tributaria_is', 6)

    def clean_imagem_produto(self):
        imagem = self.cleaned_data.get('imagem_produto')
        if not imagem:
            return imagem
        if getattr(imagem, 'size', 0) > 4 * 1024 * 1024:
            raise forms.ValidationError('Imagem deve ter no maximo 4 MB.')
        content_type = getattr(imagem, 'content_type', '')
        if content_type not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}:
            raise forms.ValidationError('Use imagem PNG, JPG, WEBP ou GIF.')
        return imagem

    def clean_subcategoria(self):
        subcategoria = self.cleaned_data.get('subcategoria')
        categoria = self.cleaned_data.get('categoria')
        if subcategoria and categoria and subcategoria.categoria_pai_id != categoria.id:
            raise forms.ValidationError('Selecione uma sub categoria da categoria escolhida.')
        return subcategoria

    def clean_especificacoes_tecnicas(self):
        value = self.cleaned_data.get('especificacoes_tecnicas') or []
        if isinstance(value, str):
            try:
                parsed = json.loads(value) if value.strip() else []
            except json.JSONDecodeError:
                raise forms.ValidationError('Especificacoes tecnicas invalidas.')
            if not isinstance(parsed, list):
                return []
            return [
                {
                    'especificacao': str(item.get('especificacao', '')).strip(),
                    'valor': str(item.get('valor', '')).strip(),
                    'unidade': str(item.get('unidade', '')).strip(),
                }
                for item in parsed
                if isinstance(item, dict) and (
                    str(item.get('especificacao', '')).strip()
                    or str(item.get('valor', '')).strip()
                    or str(item.get('unidade', '')).strip()
                )
            ]
        return value

    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get('tipo_produto')
        controla_lote = cleaned.get('controla_lote')
        controla_validade = cleaned.get('controla_validade')

        if controla_validade and not controla_lote:
            raise forms.ValidationError(
                'Produto com controle de validade tambem deve controlar lote.'
            )

        if tipo in ('granel_peso', 'granel_volume', 'granel_metragem'):
            if not cleaned.get('codigo_balanca'):
                raise forms.ValidationError(
                    'Produto granel requer codigo de balanca.'
                )

        for field_name, value in list(cleaned.items()):
            if value is not None:
                continue
            try:
                model_field = self._meta.model._meta.get_field(field_name)
            except Exception:
                continue
            if model_field.null:
                continue
            default = model_field.get_default()
            if isinstance(model_field, models.DecimalField):
                cleaned[field_name] = Decimal(str(default if default is not None else '0'))
            elif isinstance(model_field, models.IntegerField):
                cleaned[field_name] = default if default is not None else 0

        return cleaned

    def save(self, commit=True):
        produto = super().save(commit=False)
        produto.codigos_barras_extras = [
            self.cleaned_data.get(f'codigo_barras_extra_{index}') for index in range(1, 4)
            if self.cleaned_data.get(f'codigo_barras_extra_{index}')
        ]
        if commit:
            produto.save()
            self.save_m2m()
        return produto
