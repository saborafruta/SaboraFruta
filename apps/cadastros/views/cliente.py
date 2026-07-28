"""CRUD de Cliente + endpoint de consulta CEP."""
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
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from apps.cadastros.forms import ClienteForm
from apps.cadastros.models import Cliente
from apps.cadastros.services.cep_service import CepService
from apps.cadastros.services.cliente_service import ClienteService
from apps.cadastros.services.replicacao_service import ReplicacaoCadastrosService
from apps.cadastros.views.audit import cadastro_log_context
from apps.core.services.exceptions import DomainError
from apps.core.services.permissions import PermissaoRequiredMixin


def _usuario_pode_exportar(request):
    perfil = getattr(request.user, '_perfil_ativo', None) or getattr(request.user, 'perfil', None)
    return bool(request.user.is_superuser or getattr(perfil, 'is_admin', False))


def _cliente_queryset_filtrado(request, incluir_inativos_por_padrao=False):
    mostrar_inativos = incluir_inativos_por_padrao or request.GET.get('inativos') == '1'
    codigo_global = Cliente.objects.filter(
        grupo_replicacao=OuterRef('grupo_replicacao'),
        filial__empresa_id=request.filial_ativa.empresa_id,
    ).order_by('pk').values('pk')[:1]
    qs = Cliente.objects.for_filial(request.filial_ativa).annotate(
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
            | Q(email__icontains=busca)
        )
        busca_codigo = busca.lstrip('0')
        if busca_codigo.isdigit():
            grupos_codigo = Cliente.objects.filter(
                filial__empresa_id=request.filial_ativa.empresa_id,
                pk=int(busca_codigo),
            ).values('grupo_replicacao')
            filtro_busca |= Q(pk=int(busca_codigo)) | Q(grupo_replicacao__in=grupos_codigo)
        qs = qs.filter(filtro_busca)
    if tipo:
        qs = qs.filter(tipo=tipo)

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


def _cliente_inline_display(cliente, field):
    if field == 'nome':
        return cliente.nome_display or '-'
    if field == 'cpf_cnpj':
        return _formatar_cpf_cnpj(cliente.cpf_cnpj)
    if field == 'telefone':
        return _formatar_telefone(cliente.telefone)
    if field == 'cidade':
        return f'{cliente.cidade}/{cliente.uf}' if cliente.uf else cliente.cidade or '-'
    if field == 'tipo':
        return cliente.get_tipo_display() or '-'
    return getattr(cliente, field) or '-'


def _cliente_csv_response(qs, filename):
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Codigo', 'Nome', 'Razao Social', 'CPF/CNPJ', 'Contato', 'Cidade', 'UF', 'Tipo',
        'Criado em', 'Ativo',
    ])
    for c in qs:
        writer.writerow([
            _codigo_cadastro(c),
            c.nome_display,
            c.razao_social,
            c.cpf_cnpj,
            c.telefone,
            c.cidade,
            c.uf,
            c.get_tipo_display(),
            timezone.localtime(c.created_at).strftime('%d/%m/%Y %H:%M') if c.created_at else '',
            'Sim' if c.ativo else 'Nao',
        ])
    return response


def _cliente_pdf_response(qs):
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="clientes_filtrados.pdf"'
    doc = SimpleDocTemplate(response, pagesize=landscape(A4), rightMargin=24, leftMargin=24, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    elementos = [
        Paragraph('Clientes filtrados', styles['Title']),
        Paragraph(f'Gerado em {timezone.localtime().strftime("%d/%m/%Y %H:%M")}', styles['Normal']),
        Spacer(1, 12),
    ]
    dados = [['Cod.', 'Nome', 'CPF/CNPJ', 'Contato', 'Cidade/UF', 'Tipo', 'Criado em', 'Ativo']]
    for c in qs:
        dados.append([
            _codigo_cadastro(c),
            c.nome_display,
            c.cpf_cnpj or '-',
            _formatar_telefone(c.telefone),
            f'{c.cidade}/{c.uf}' if c.uf else c.cidade or '-',
            c.get_tipo_display(),
            timezone.localtime(c.created_at).strftime('%d/%m/%Y %H:%M') if c.created_at else '-',
            'Sim' if c.ativo else 'Nao',
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


class ClienteListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'
    template_name = 'cadastros/cliente/list.html'

    def get(self, request):
        mostrar_inativos = request.GET.get('inativos') == '1'
        qs = _cliente_queryset_filtrado(request)
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
            'clientes': page_obj.object_list,
            'busca': busca,
            'tipo': tipo,
            'tipos': Cliente.Tipo.choices,
            'ordem': ordem,
            'data_inicio': data_inicio,
            'data_fim': data_fim,
            'mostrar_inativos': mostrar_inativos,
            'pode_exportar': _usuario_pode_exportar(request),
            'pode_editar': request.user.tem_permissao('cadastros', 'editar'),
        })


class ClienteInlineEditView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'
    campos_permitidos = {'nome', 'cpf_cnpj', 'cidade', 'telefone', 'tipo'}

    def post(self, request, pk):
        cliente = get_object_or_404(
            Cliente.objects.for_filial(request.filial_ativa), pk=pk,
        )
        field = request.POST.get('field', '').strip()
        value = request.POST.get('value', '').strip()
        if field not in self.campos_permitidos:
            return JsonResponse({'ok': False, 'error': 'Campo nao permitido.'}, status=400)

        try:
            dados = self._dados_limpos(cliente, field, value)
            ClienteService.atualizar(cliente, dados)
        except DomainError as exc:
            return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
        except ValueError as exc:
            return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

        return JsonResponse({
            'ok': True,
            'display': _cliente_inline_display(cliente, field),
            'value': cliente.nome_display if field == 'nome' else getattr(cliente, field) or '',
        })

    def _dados_limpos(self, cliente, field, value):
        if field == 'nome':
            value = value.strip()
            if not value:
                raise ValueError('Nome do cliente e obrigatorio.')
            campo_nome = 'nome_fantasia' if cliente.nome_fantasia else 'razao_social'
            limite = 100 if campo_nome == 'nome_fantasia' else 150
            return {campo_nome: value[:limite]}
        if field == 'cpf_cnpj':
            value = _apenas_digitos(value)
            if value and len(value) not in (11, 14):
                raise ValueError('CPF/CNPJ invalido.')
            return {field: value}
        if field == 'telefone':
            return {field: _apenas_digitos(value)[:20]}
        if field == 'cidade':
            return {field: value.strip()[:80]}
        if field == 'tipo':
            tipos_validos = {t.value for t in Cliente.Tipo}
            if value not in tipos_validos:
                raise ValueError(f'Tipo invalido. Use: {", ".join(tipos_validos)}.')
            return {field: value}
        return {field: value}


class ClienteCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'criar'
    template_name = 'cadastros/cliente/form.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': ClienteForm(filial=request.filial_ativa),
            'title': 'Novo Cliente',
            'cancel_url': reverse_lazy('cadastros:cliente-list'),
        })

    def post(self, request):
        form = ClienteForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            try:
                cliente = ClienteService.criar(
                    form.cleaned_data, request.user, request.filial_ativa,
                )
                messages.success(request, f'Cliente "{cliente.nome_display}" criado.')
                return redirect('cadastros:cliente-list')
            except DomainError as e:
                messages.error(request, str(e))
            except Exception as e:
                messages.error(request, f'Nao foi possivel criar o cliente: {e}')
        else:
            erros = []
            for campo, lista_erros in form.errors.items():
                erros.append(f'{campo}: {", ".join(lista_erros)}')
            messages.error(request, f'Corrija os erros antes de salvar: {" | ".join(erros) or "sem erros de campo"}')

        return render(request, self.template_name, {
            'form': form,
            'title': 'Novo Cliente',
            'cancel_url': reverse_lazy('cadastros:cliente-list'),
        })


class ClienteUpdateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'
    template_name = 'cadastros/cliente/form.html'

    def get_object(self, request, pk):
        return get_object_or_404(
            Cliente.objects.for_filial(request.filial_ativa), pk=pk,
        )

    def _next_url(self, request):
        next_url = request.POST.get('next') or request.GET.get('next', '')
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return next_url
        return None

    def get(self, request, pk):
        cliente = self.get_object(request, pk)
        next_url = self._next_url(request)
        return render(request, self.template_name, {
            'form': ClienteForm(instance=cliente, filial=request.filial_ativa),
            'cliente': cliente,
            'cadastro_log_pk': cliente.pk,
            **cadastro_log_context(cliente, 'clientes', 'Cliente', request.user),
            'title': f'Editar Cliente — {cliente.nome_display}',
            'cancel_url': next_url or reverse_lazy('cadastros:cliente-list'),
            'next_url': next_url or '',
        })

    def post(self, request, pk):
        cliente = self.get_object(request, pk)
        form = ClienteForm(
            request.POST,
            instance=cliente,
            filial=request.filial_ativa,
        )
        next_url = self._next_url(request)
        if form.is_valid():
            try:
                ClienteService.atualizar(cliente, form.cleaned_data)
                messages.success(request, 'Cliente atualizado.')
                return redirect(next_url or 'cadastros:cliente-list')
            except DomainError as e:
                messages.error(request, str(e))
        return render(request, self.template_name, {
            'form': form,
            'cliente': cliente,
            'cadastro_log_pk': cliente.pk,
            **cadastro_log_context(cliente, 'clientes', 'Cliente', request.user),
            'title': f'Editar Cliente — {cliente.nome_display}',
            'cancel_url': next_url or reverse_lazy('cadastros:cliente-list'),
            'next_url': next_url or '',
        })


class ClienteDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'excluir'

    def post(self, request, pk):
        cliente = get_object_or_404(
            Cliente.objects.for_filial(request.filial_ativa), pk=pk,
        )
        cliente.ativo = False
        cliente.save(update_fields=['ativo', 'updated_at'])
        messages.success(request, f'Cliente "{cliente.nome_display}" desativado.')
        return redirect('cadastros:cliente-list')


class ClienteToggleAtivoView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request, pk):
        cliente = get_object_or_404(
            Cliente.objects.for_filial(request.filial_ativa), pk=pk,
        )
        with transaction.atomic():
            cliente.ativo = not cliente.ativo
            cliente.save(update_fields=['ativo', 'updated_at'])
            ReplicacaoCadastrosService.sincronizar_cliente(cliente)
        status = 'ativado' if cliente.ativo else 'desativado'
        messages.success(request, f'Cliente "{cliente.nome_display}" {status}.')
        return redirect(request.META.get('HTTP_REFERER', 'cadastros:cliente-list'))


class ClienteExportCsvView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        if not _usuario_pode_exportar(request):
            messages.error(request, 'Apenas administradores podem exportar clientes.')
            return redirect('cadastros:cliente-list')
        return _cliente_csv_response(_cliente_queryset_filtrado(request), 'clientes_filtrados.csv')


class ClienteExportTodosCsvView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        if not _usuario_pode_exportar(request):
            messages.error(request, 'Apenas administradores podem exportar clientes.')
            return redirect('cadastros:cliente-list')
        qs = Cliente.objects.for_filial(request.filial_ativa).order_by('id')
        return _cliente_csv_response(qs, 'clientes_todos.csv')


class ClienteExportPdfView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        if not _usuario_pode_exportar(request):
            messages.error(request, 'Apenas administradores podem exportar clientes.')
            return redirect('cadastros:cliente-list')
        return _cliente_pdf_response(_cliente_queryset_filtrado(request))


class ClienteImportTemplateCsvView(PermissaoRequiredMixin, View):
    """Gera e faz download do arquivo CSV modelo para importação de clientes."""
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = 'attachment; filename="modelo_importacao_clientes.csv"'
        writer = csv.writer(response, delimiter=';')
        writer.writerow([
            'tipo_pessoa', 'tipo', 'razao_social', 'nome_fantasia', 'cpf_cnpj',
            'rg_ie', 'inscricao_estadual', 'inscricao_municipal',
            'data_nascimento', 'sexo',
            'cep', 'endereco', 'numero', 'complemento', 'bairro', 'cidade', 'uf',
            'telefone', 'celular', 'email', 'email_nfe', 'contato_nome',
            'limite_credito', 'prazo_pagamento_dias', 'grupo_desconto',
            'consumidor_final', 'contribuinte_icms', 'optante_simples',
            'bloqueado', 'ativo', 'observacao', 'id_externo',
        ])
        # Linha de exemplo PJ
        writer.writerow([
            'J', 'varejo', 'Supermercado Silva Ltda', 'Mercadinho Silva', '12345678000195',
            '', '123456789', '',
            '', '',
            '01310100', 'Rua das Flores', '123', 'Sala 2', 'Centro', 'Sao Paulo', 'SP',
            '(11) 3333-4444', '(11) 99999-0000', 'contato@silva.com', '', 'Joao',
            '5000.00', '30', '',
            '1', '0', '0',
            '0', '1', 'Cliente exemplo PJ', 'CLI-001',
        ])
        # Linha de exemplo PF
        writer.writerow([
            'F', 'varejo', 'Maria Oliveira', '', '12345678901',
            'RG1234567', '', '',
            '1990-05-15', 'F',
            '04567890', 'Av Paulista', '500', '', 'Bela Vista', 'Sao Paulo', 'SP',
            '', '(11) 91111-2222', 'maria@email.com', '', '',
            '0', '0', '',
            '1', '0', '0',
            '0', '1', 'Cliente exemplo PF', '',
        ])
        return response


class ClienteImportCsvView(PermissaoRequiredMixin, View):
    """Upload e importação em lote de clientes via arquivo CSV."""
    permissao_modulo = 'cadastros'
    permissao_acao = 'criar'
    template_name = 'cadastros/cliente/import_csv.html'

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        arquivo = request.FILES.get('arquivo_csv')
        if not arquivo:
            messages.error(request, 'Selecione um arquivo CSV antes de enviar.')
            return render(request, self.template_name)

        if not arquivo.name.lower().endswith('.csv'):
            messages.error(request, 'Apenas arquivos .csv são aceitos.')
            return render(request, self.template_name)

        from apps.cadastros.services.cliente_import_service import ClienteImportService
        try:
            resultado = ClienteImportService.importar_csv(arquivo, request.user, request.filial_ativa)
        except DomainError as e:
            messages.error(request, str(e))
            return render(request, self.template_name)

        return render(request, self.template_name, {'resultado': resultado})


def consultar_cep_ajax(request):
    cep = request.GET.get('cep', '')
    try:
        dados = CepService.consultar(cep)
    except DomainError as e:
        return JsonResponse({'erro': str(e)}, status=400)

    if not dados:
        return JsonResponse({'erro': 'CEP não encontrado.'}, status=404)
    return JsonResponse(dados)
