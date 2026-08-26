"""CRUD de Fornecedor."""
import csv

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import IntegerField, OuterRef, Q, Subquery
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from apps.cadastros.forms import FornecedorForm, FornecedorRapidoForm
from apps.cadastros.models import Fornecedor
from apps.cadastros.services.replicacao_service import ReplicacaoCadastrosService
from apps.cadastros.views.audit import cadastro_log_context
from apps.core.services.permissions import PermissaoRequiredMixin


def _usuario_pode_exportar(request):
    perfil = getattr(request.user, '_perfil_ativo', None) or getattr(request.user, 'perfil', None)
    return bool(request.user.is_superuser or getattr(perfil, 'is_admin', False))


def _fornecedor_queryset_filtrado(request, incluir_inativos_por_padrao=False):
    mostrar_inativos = incluir_inativos_por_padrao or request.GET.get('inativos') == '1'
    codigo_global = Fornecedor.objects.filter(
        grupo_replicacao=OuterRef('grupo_replicacao'),
        filial__empresa_id=request.filial_ativa.empresa_id,
    ).order_by('pk').values('pk')[:1]
    qs = Fornecedor.objects.for_filial(request.filial_ativa).annotate(
        codigo_global=Subquery(codigo_global, output_field=IntegerField()),
    )
    if not mostrar_inativos:
        qs = qs.filter(ativo=True)

    busca = request.GET.get('q', '').strip()
    tipo = request.GET.get('tipo', '')
    ordem = request.GET.get('ordem', 'id')
    data_inicio = request.GET.get('data_inicio', '')
    data_fim = request.GET.get('data_fim', '')
    if busca:
        filtro_busca = (
            Q(razao_social__icontains=busca)
            | Q(nome_fantasia__icontains=busca)
            | Q(cpf_cnpj__icontains=busca)
            | Q(telefone__icontains=busca)
            | Q(celular__icontains=busca)
            | Q(email__icontains=busca)
        )
        busca_codigo = busca.lstrip('0')
        if busca_codigo.isdigit():
            grupos_codigo = Fornecedor.objects.filter(
                filial__empresa_id=request.filial_ativa.empresa_id,
                pk=int(busca_codigo),
            ).values('grupo_replicacao')
            filtro_busca |= Q(pk=int(busca_codigo)) | Q(grupo_replicacao__in=grupos_codigo)
        qs = qs.filter(filtro_busca)
    if tipo:
        qs = qs.filter(tipo_pessoa=tipo)

    data_inicio_valida = parse_date(data_inicio) if data_inicio else None
    data_fim_valida = parse_date(data_fim) if data_fim else None
    if data_inicio_valida:
        qs = qs.filter(created_at__date__gte=data_inicio_valida)
    if data_fim_valida:
        qs = qs.filter(created_at__date__lte=data_fim_valida)

    ordenacoes = {
        'id': 'codigo_global',
        'id_desc': '-codigo_global',
        'az': 'razao_social',
        'za': '-razao_social',
        'criado_desc': '-created_at',
        'criado_asc': 'created_at',
    }
    return qs.order_by(ordenacoes.get(ordem, 'id'))


def _codigo(pk):
    return f'{pk:02d}'


def _codigo_cadastro(obj):
    return _codigo(getattr(obj, 'codigo_global', None) or obj.pk)


def _apenas_digitos(valor):
    return ''.join(ch for ch in str(valor or '') if ch.isdigit())


def _formatar_cpf_cnpj(valor):
    digitos = _apenas_digitos(valor)
    if len(digitos) == 11:
        return f'{digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}'
    if len(digitos) == 14:
        return f'{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}'
    return valor or '-'


def _formatar_telefone(valor):
    digitos = _apenas_digitos(valor)
    if len(digitos) == 11:
        return f'({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}'
    if len(digitos) == 10:
        return f'({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}'
    return valor or '-'


def _fornecedor_inline_display(fornecedor, field):
    if field == 'nome':
        return str(fornecedor) or '-'
    if field == 'cpf_cnpj':
        return _formatar_cpf_cnpj(fornecedor.cpf_cnpj)
    if field == 'telefone':
        return _formatar_telefone(fornecedor.telefone)
    if field == 'cidade':
        return f'{fornecedor.cidade}/{fornecedor.uf}' if fornecedor.uf else fornecedor.cidade or '-'
    return getattr(fornecedor, field) or '-'


def _fornecedor_csv_response(qs, filename):
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Codigo', 'Nome', 'Razao Social', 'CPF/CNPJ', 'Contato', 'Cidade', 'UF', 'Tipo',
        'Criado em', 'Ativo', 'Percentual no prazo',
    ])
    for f in qs:
        writer.writerow([
            _codigo_cadastro(f),
            str(f),
            f.razao_social,
            f.cpf_cnpj,
            f.telefone,
            f.cidade,
            f.uf,
            f.get_tipo_pessoa_display(),
            timezone.localtime(f.created_at).strftime('%d/%m/%Y %H:%M') if f.created_at else '',
            'Sim' if f.ativo else 'Nao',
            f.percentual_no_prazo,
        ])
    return response


def _fornecedor_pdf_response(qs):
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="fornecedores_filtrados.pdf"'
    doc = SimpleDocTemplate(response, pagesize=landscape(A4), rightMargin=24, leftMargin=24, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    elementos = [
        Paragraph('Fornecedores filtrados', styles['Title']),
        Paragraph(f'Gerado em {timezone.localtime().strftime("%d/%m/%Y %H:%M")}', styles['Normal']),
        Spacer(1, 12),
    ]
    dados = [['Cod.', 'Nome', 'CPF/CNPJ', 'Contato', 'Cidade/UF', 'Tipo', 'Criado em', 'Ativo']]
    for f in qs:
        dados.append([
            _codigo_cadastro(f),
            str(f),
            f.cpf_cnpj or '-',
            _formatar_telefone(f.telefone),
            f'{f.cidade}/{f.uf}' if f.uf else f.cidade or '-',
            f.get_tipo_pessoa_display(),
            timezone.localtime(f.created_at).strftime('%d/%m/%Y %H:%M') if f.created_at else '-',
            'Sim' if f.ativo else 'Nao',
        ])
    table = Table(dados, repeatRows=1, colWidths=[38, 150, 92, 90, 100, 78, 86, 42])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8824a')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#d1d5db')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8fafc')]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elementos.append(table)
    doc.build(elementos)
    return response


class FornecedorListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    template_name = 'cadastros/fornecedor/list.html'

    def get(self, request):
        mostrar_inativos = request.GET.get('inativos') == '1'
        qs = _fornecedor_queryset_filtrado(request)
        busca = request.GET.get('q', '').strip()
        tipo = request.GET.get('tipo', '')
        ordem = request.GET.get('ordem', 'id')
        data_inicio = request.GET.get('data_inicio', '')
        data_fim = request.GET.get('data_fim', '')
        page_obj = Paginator(qs, 50).get_page(request.GET.get('page'))
        query_params = request.GET.copy()
        query_params.pop('page', None)
        sort_urls = {}
        for key, value in {
            'codigo': 'id_desc' if ordem == 'id' else 'id',
            'nome': 'za' if ordem == 'az' else 'az',
            'criado': 'criado_asc' if ordem == 'criado_desc' else 'criado_desc',
        }.items():
            params = request.GET.copy()
            params.pop('page', None)
            params['ordem'] = value
            sort_urls[key] = params.urlencode()

        return render(request, self.template_name, {
            'page_obj': page_obj,
            'page_querystring': query_params.urlencode(),
            'sort_urls': sort_urls,
            'fornecedores': page_obj.object_list,
            'busca': busca,
            'tipo': tipo,
            'tipos': Fornecedor._meta.get_field('tipo_pessoa').choices,
            'ordem': ordem,
            'data_inicio': data_inicio,
            'data_fim': data_fim,
            'mostrar_inativos': mostrar_inativos,
            'pode_exportar': _usuario_pode_exportar(request),
            'pode_editar': request.user.tem_permissao('cadastros', 'editar'),
        })


class FornecedorInlineEditView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'
    campos_permitidos = {'nome', 'cpf_cnpj', 'cidade', 'telefone'}

    def post(self, request, pk):
        fornecedor = get_object_or_404(
            Fornecedor.objects.for_filial(request.filial_ativa), pk=pk,
        )
        field = request.POST.get('field', '').strip()
        value = request.POST.get('value', '').strip()
        if field not in self.campos_permitidos:
            return JsonResponse({'ok': False, 'error': 'Campo nao permitido.'}, status=400)

        try:
            dados = self._dados_limpos(request, fornecedor, field, value)
        except ValueError as exc:
            return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

        with transaction.atomic():
            for campo, valor in dados.items():
                setattr(fornecedor, campo, valor)
            fornecedor.save()
            ReplicacaoCadastrosService.sincronizar_fornecedor(fornecedor)

        return JsonResponse({
            'ok': True,
            'display': _fornecedor_inline_display(fornecedor, field),
            'value': str(fornecedor) if field == 'nome' else getattr(fornecedor, field) or '',
        })

    def _dados_limpos(self, request, fornecedor, field, value):
        if field == 'nome':
            value = value.strip()
            if not value:
                raise ValueError('Nome do fornecedor e obrigatorio.')
            campo_nome = 'nome_fantasia' if fornecedor.nome_fantasia else 'razao_social'
            limite = 100 if campo_nome == 'nome_fantasia' else 150
            return {campo_nome: value[:limite]}
        if field == 'cpf_cnpj':
            value = _apenas_digitos(value)
            if value and len(value) not in (11, 14):
                raise ValueError('CPF/CNPJ invalido.')
            duplicado = Fornecedor.objects.for_filial(request.filial_ativa).filter(
                cpf_cnpj=value,
            ).exclude(pk=fornecedor.pk).exists()
            if value and duplicado:
                raise ValueError(f'Ja existe outro fornecedor com este CPF/CNPJ ({value}) na filial.')
            return {field: value}
        if field == 'telefone':
            return {field: _apenas_digitos(value)[:20]}
        if field == 'cidade':
            return {field: value.strip()[:80]}
        return {field: value}


class FornecedorCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'criar'
    template_name = 'cadastros/fornecedor/form.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': FornecedorForm(),
            'title': 'Novo Fornecedor',
            'cancel_url': reverse_lazy('cadastros:fornecedor-list'),
        })

    def post(self, request):
        form = FornecedorForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                fornecedor = form.save(commit=False)
                fornecedor.filial = request.filial_ativa
                fornecedor.save()
                ReplicacaoCadastrosService.sincronizar_fornecedor(fornecedor)
            messages.success(request, f'Fornecedor "{fornecedor}" criado.')
            return redirect('cadastros:fornecedor-update', pk=fornecedor.pk)
        return render(request, self.template_name, {
            'form': form,
            'title': 'Novo Fornecedor',
            'cancel_url': reverse_lazy('cadastros:fornecedor-list'),
        })


class FornecedorAjaxCreateView(PermissaoRequiredMixin, View):
    """Cria um fornecedor no modal de contas a pagar e retorna a opção criada."""

    permissao_modulo = 'cadastros'
    permissao_acao = 'criar'

    def post(self, request):
        filial = request.filial_ativa
        form = FornecedorRapidoForm(request.POST, filial=filial)
        if not form.is_valid():
            erros = {
                campo: [erro['message'] for erro in mensagens]
                for campo, mensagens in form.errors.get_json_data().items()
            }
            # A MENSAGEM DIZ QUAL CAMPO. Quem chama isto de um modal so' recebe
            # o texto cru, e "Este campo e' obrigatorio" sem o nome do campo e'
            # inutil quando o campo faltando nem aparece na tela -- foi
            # exatamente o que aconteceu com `tipo_pessoa` no cadastro
            # relampago de produtor.
            return JsonResponse(
                {
                    'ok': False,
                    'errors': erros,
                    'mensagem': ' '.join(
                        f'{form.fields[campo].label or campo}: {texto}'
                        if campo in form.fields else texto
                        for campo, textos in erros.items()
                        for texto in textos
                    ),
                },
                status=400,
            )

        with transaction.atomic():
            fornecedor = form.save(commit=False)
            fornecedor.filial = filial
            fornecedor.save()
            ReplicacaoCadastrosService.sincronizar_fornecedor(fornecedor)

        label = str(fornecedor)
        documento = fornecedor.cpf_cnpj
        search = ' '.join(filter(None, [
            fornecedor.razao_social, fornecedor.nome_fantasia, documento,
        ]))
        return JsonResponse({
            'ok': True,
            'id': fornecedor.pk,
            'label': label,
            'search': search,
        })


class FornecedorUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'
    template_name = 'cadastros/fornecedor/form.html'

    def get(self, request, pk):
        fornecedor = get_object_or_404(
            Fornecedor.objects.for_filial(request.filial_ativa), pk=pk,
        )
        return render(request, self.template_name, {
            'form': FornecedorForm(instance=fornecedor),
            'fornecedor': fornecedor,
            'cadastro_log_pk': fornecedor.pk,
            **cadastro_log_context(fornecedor, 'fornecedores', 'Fornecedor', request.user),
            'title': f'Editar — {fornecedor}',
            'cancel_url': reverse_lazy('cadastros:fornecedor-list'),
        })

    def post(self, request, pk):
        fornecedor = get_object_or_404(
            Fornecedor.objects.for_filial(request.filial_ativa), pk=pk,
        )
        form = FornecedorForm(request.POST, instance=fornecedor)
        if form.is_valid():
            with transaction.atomic():
                fornecedor = form.save()
                ReplicacaoCadastrosService.sincronizar_fornecedor(fornecedor)
            messages.success(request, 'Fornecedor atualizado.')
            return redirect('cadastros:fornecedor-list')
        return render(request, self.template_name, {
            'form': form,
            'fornecedor': fornecedor,
            'cadastro_log_pk': fornecedor.pk,
            **cadastro_log_context(fornecedor, 'fornecedores', 'Fornecedor', request.user),
            'title': f'Editar — {fornecedor}',
            'cancel_url': reverse_lazy('cadastros:fornecedor-list'),
        })


class FornecedorDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'excluir'

    def post(self, request, pk):
        fornecedor = get_object_or_404(
            Fornecedor.objects.for_filial(request.filial_ativa), pk=pk,
        )
        fornecedor.ativo = False
        fornecedor.save(update_fields=['ativo', 'updated_at'])
        messages.success(request, f'Fornecedor "{fornecedor}" desativado.')
        return redirect('cadastros:fornecedor-list')


class FornecedorToggleAtivoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request, pk):
        fornecedor = get_object_or_404(
            Fornecedor.objects.for_filial(request.filial_ativa), pk=pk,
        )
        with transaction.atomic():
            fornecedor.ativo = not fornecedor.ativo
            fornecedor.save(update_fields=['ativo', 'updated_at'])
            ReplicacaoCadastrosService.sincronizar_fornecedor(fornecedor)
        status = 'ativado' if fornecedor.ativo else 'desativado'
        messages.success(request, f'Fornecedor "{fornecedor}" {status}.')
        return redirect(request.META.get('HTTP_REFERER', 'cadastros:fornecedor-list'))


class FornecedorExportCsvView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        if not _usuario_pode_exportar(request):
            messages.error(request, 'Apenas administradores podem exportar fornecedores.')
            return redirect('cadastros:fornecedor-list')
        return _fornecedor_csv_response(_fornecedor_queryset_filtrado(request), 'fornecedores_filtrados.csv')


class FornecedorExportTodosCsvView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        if not _usuario_pode_exportar(request):
            messages.error(request, 'Apenas administradores podem exportar fornecedores.')
            return redirect('cadastros:fornecedor-list')
        qs = Fornecedor.objects.for_filial(request.filial_ativa).order_by('id')
        return _fornecedor_csv_response(qs, 'fornecedores_todos.csv')


class FornecedorExportPdfView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        if not _usuario_pode_exportar(request):
            messages.error(request, 'Apenas administradores podem exportar fornecedores.')
            return redirect('cadastros:fornecedor-list')
        return _fornecedor_pdf_response(_fornecedor_queryset_filtrado(request))
