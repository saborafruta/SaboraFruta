import json
from datetime import timedelta
from pathlib import Path

from django.contrib import messages
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from apps.cadastros.services.replicacao_service import ReplicacaoCadastrosService
from apps.core.forms.admin_forms import (
    EmpresaAdminForm,
    FilialAdminForm,
    get_or_create_politica_filial,
    PerfilAcessoAdminForm,
    PermissaoMatrix,
    PoliticaReplicacaoForm,
    RailwayProjectPoolAdminForm,
    UsuarioAdminForm,
)
from apps.core.constants.segmentos import SEGMENTOS
from apps.core.constants.choices import UF
from apps.core.services.modulos import (
    modulos_de_verticais, modulos_disponiveis, modulos_para_admin,
)
from apps.core.models import (
    Empresa, EmpresaBanco, Filial, PerfilAcesso, Permissao, RailwayProjectPool,
    SeparacaoFilial, Usuario,
)
from apps.core.services.imagem_filial import preparar_imagem_filial
from apps.core.services.empresa_banco_service import EmpresaBancoService
from apps.core.services.separacao_filial_service import (
    SeparacaoFilialError, SeparacaoFilialService,
)
from apps.core.views.audit import core_log_context
from apps.core.views._admin import admin_area_required, superuser_required
from apps.produtos.services.replicacao_service import ReplicacaoProdutoService


PER_PAGE = 10

BANCO_STATUS_FILTERS = [('sem_banco', 'Sem banco')] + list(EmpresaBanco.Status.choices)


def _paginate(request, queryset):
    if request.GET.get('all') == '1':
        per_page = queryset.count() if hasattr(queryset, 'model') else len(queryset)
    else:
        per_page = PER_PAGE
    return Paginator(queryset, per_page or PER_PAGE).get_page(request.GET.get('page', 1))


def _page_querystring(request):
    params = request.GET.copy()
    params.pop('page', None)
    params.pop('all', None)
    return params.urlencode()


def _empresa_filtros(request):
    return {
        'nome': request.GET.get('nome', '').strip(),
        'cnpj': request.GET.get('cnpj', '').strip(),
        'banco': request.GET.get('banco', '').strip(),
        'cidade': request.GET.get('cidade', '').strip(),
        'uf': request.GET.get('uf', '').strip().upper(),
        'regime': request.GET.get('regime', '').strip(),
    }


def _aplicar_empresa_filtros(queryset, filtros):
    nome = filtros.get('nome')
    documento = ''.join(ch for ch in filtros.get('cnpj', '') if ch.isdigit())
    banco = filtros.get('banco')
    cidade = filtros.get('cidade')
    uf = filtros.get('uf')
    regime = filtros.get('regime')
    if nome:
        queryset = queryset.filter(
            Q(razao_social__icontains=nome)
            | Q(nome_fantasia__icontains=nome)
            | Q(banco_dedicado__railway_database_service_name__icontains=nome)
            | Q(banco_dedicado__db_alias__icontains=nome)
            | Q(banco_dedicado__slug__icontains=nome)
        )
    if documento:
        queryset = queryset.filter(cnpj__icontains=documento)
    if banco == 'sem_banco':
        queryset = queryset.filter(banco_dedicado__isnull=True)
    elif banco in EmpresaBanco.Status.values:
        queryset = queryset.filter(banco_dedicado__status=banco)
    if cidade:
        queryset = queryset.filter(cidade__icontains=cidade)
    if uf:
        queryset = queryset.filter(uf=uf)
    if regime:
        queryset = queryset.filter(regime_tributario=regime)
    return queryset


def _empresa_filtros_context(filtros):
    return {
        'filtros': filtros,
        'tem_filtro': any(filtros.values()),
        'banco_status_filters': BANCO_STATUS_FILTERS,
        'regime_choices': Empresa.RegimeTributario.choices,
        'uf_choices': UF.choices,
    }


def _senha_master_valida(request, senha):
    senha_master = getattr(settings, 'TENANT_DATABASE_DELETION_MASTER_PASSWORD', '')
    return (
        constant_time_compare(senha, senha_master)
        if senha_master else request.user.check_password(senha)
    )


def _file_download_response(path, content_type):
    file_path = Path(path)
    if not file_path.exists():
        if not default_storage.exists(str(path)):
            raise FileNotFoundError(str(path))
        return FileResponse(
            default_storage.open(str(path), 'rb'), as_attachment=True,
            filename=file_path.name, content_type=content_type,
        )
    return FileResponse(
        file_path.open('rb'), as_attachment=True,
        filename=file_path.name, content_type=content_type,
    )


def _empresa_banco_progress_payload(banco):
    EmpresaBancoService.validar_empresa_preparavel(banco)
    banco.refresh_from_db()
    progresso = {
        EmpresaBanco.Status.PENDENTE: 8,
        EmpresaBanco.Status.AGUARDANDO_CONFIGURACAO: 42,
        EmpresaBanco.Status.CONFIGURADO: 72,
        EmpresaBanco.Status.ATIVO: 100,
        EmpresaBanco.Status.ERRO: 0,
        EmpresaBanco.Status.INATIVO: 0,
    }
    etapas = {
        EmpresaBanco.Status.PENDENTE: 'Preparando solicitação',
        EmpresaBanco.Status.AGUARDANDO_CONFIGURACAO: 'Aguardando o Railway publicar o banco',
        EmpresaBanco.Status.CONFIGURADO: 'Aplicando estrutura e dados iniciais',
        EmpresaBanco.Status.ATIVO: 'Banco pronto para uso',
        EmpresaBanco.Status.ERRO: 'Precisa de atenção',
        EmpresaBanco.Status.INATIVO: 'Banco inativo',
    }
    return {
        'id': banco.pk, 'empresa': banco.empresa.razao_social,
        'alias': banco.db_alias, 'status': banco.status,
        'status_label': banco.get_status_display(),
        'stage_label': etapas.get(banco.status, banco.get_status_display()),
        'progress': progresso.get(banco.status, 15),
        'ready': banco.status == EmpresaBanco.Status.ATIVO,
        'error': banco.status == EmpresaBanco.Status.ERRO,
        'blocked': banco.status == EmpresaBanco.Status.INATIVO,
        'detail': banco.ultimo_erro,
        'requested_at': banco.provisionamento_solicitado_em.isoformat() if banco.provisionamento_solicitado_em else '',
        'provisioned_at': banco.provisionado_em.isoformat() if banco.provisionado_em else '',
        'migrated_at': banco.ultima_migracao_em.isoformat() if banco.ultima_migracao_em else '',
    }


def _toggle(request, model, pk, redirect_name):
    obj = get_object_or_404(model, pk=pk)
    obj.ativo = not obj.ativo
    obj.save(update_fields=['ativo'])
    name = getattr(obj, 'razao_social', None) or getattr(obj, 'nome', str(obj))
    state = 'ativado' if obj.ativo else 'desativado'
    messages.success(request, f'{name} {state}.')
    return redirect(redirect_name)


@superuser_required
def media_diagnostico(request):
    media_root = settings.MEDIA_ROOT
    check_file = media_root / '.railway-media-check.txt'
    write_ok = False
    write_error = ''
    try:
        media_root.mkdir(parents=True, exist_ok=True)
        check_file.write_text('ok', encoding='utf-8')
        write_ok = check_file.exists()
    except Exception as exc:
        write_error = str(exc)

    return JsonResponse({
        'media_root': str(media_root),
        'media_url': settings.MEDIA_URL,
        'exists': media_root.exists(),
        'is_dir': media_root.is_dir(),
        'write_ok': write_ok,
        'write_error': write_error,
        'check_file': str(check_file),
    })


def _is_company_admin(user):
    perfil = getattr(user, 'perfil', None)
    return user.is_superuser or getattr(perfil, 'is_admin', False)


def _filiais_scope(user):
    return Filial.objects.all()


def _active_filial(request):
    if request.user.is_superuser:
        central_filial = request.GET.get('central_filial') or request.POST.get('central_filial')
        if central_filial and central_filial.isdigit():
            filial = Filial.objects.filter(pk=int(central_filial)).select_related('empresa').first()
            if filial:
                return filial
    return getattr(request, 'filial_ativa', None)


def _is_central_global(request):
    return request.user.is_superuser and request.GET.get('central') == '1'


def _central_redirect(request, route_name):
    url = reverse(route_name)
    if request.GET.get('central') or request.POST.get('central'):
        return f'{url}?central=1'
    if request.GET.get('super_admins') or request.POST.get('super_admins') or request.GET.get('super_admin') or request.POST.get('super_admin'):
        return f'{url}?super_admins=1'
    filial = _active_filial(request)
    if request.user.is_superuser and request.GET.get('central_filial') and filial:
        return f'{url}?central_filial={filial.pk}'
    return url


def _usuarios_scope(request):
    user = request.user
    queryset = Usuario.objects.select_related('empresa', 'filial', 'perfil').prefetch_related(
        'acessos_filiais__filial',
        'acessos_filiais__perfil',
    )
    if user.is_superuser:
        filial = _active_filial(request)
        if filial:
            return queryset.filter(
                Q(empresa_id=filial.empresa_id, filial_id=filial.id)
                | Q(acessos_filiais__filial_id=filial.id, acessos_filiais__ativo=True)
            ).distinct()
        return queryset
    queryset = queryset.filter(
        empresa_id=user.empresa_id,
        is_superuser=False,
    )
    if _is_company_admin(user):
        return queryset.distinct()
    queryset = queryset.filter(perfil__is_admin=False)
    filial_ativa = getattr(request, 'filial_ativa', None)
    filial_id = filial_ativa.pk if filial_ativa else user.filial_id
    if filial_id:
        queryset = queryset.filter(
            Q(filial_id=filial_id)
            | Q(acessos_filiais__filial_id=filial_id, acessos_filiais__ativo=True)
        ).distinct()
    return queryset


def _perfis_scope(request):
    user = request.user
    queryset = PerfilAcesso.objects.select_related('empresa')
    if user.is_superuser:
        filial = _active_filial(request)
        if filial:
            return queryset.filter(empresa_id=filial.empresa_id)
        return queryset
    queryset = queryset.filter(empresa_id=user.empresa_id)
    if not _is_company_admin(user):
        queryset = queryset.filter(is_admin=False)
    return queryset


def _require_object_in_scope(obj, queryset):
    if not queryset.filter(pk=obj.pk).exists():
        raise PermissionDenied('Registro fora do seu escopo de acesso.')
    return obj


@superuser_required
def central_administrativa(request):
    empresa_busca = request.GET.get('empresa', '').strip()
    filial_busca = request.GET.get('filial', '').strip()
    # Este bloco escolhe contexto de trabalho, portanto cadastros inativos nao
    # devem aparecer como uma segunda empresa selecionavel. Eles continuam
    # disponiveis em "Listar empresas" para consulta e eventual reativacao.
    empresas = Empresa.objects.filter(ativo=True).order_by('razao_social')
    filiais = (
        Filial.objects.select_related('empresa')
        .filter(ativo=True, empresa__ativo=True)
        .order_by('empresa__razao_social', 'razao_social')
    )
    empresa_selecionada = None

    if empresa_busca:
        empresas_filtradas = empresas.filter(
            Q(razao_social__icontains=empresa_busca)
            | Q(nome_fantasia__icontains=empresa_busca)
            | Q(cnpj__icontains=empresa_busca)
        )
        empresa_exata = empresas_filtradas.filter(
            Q(razao_social__iexact=empresa_busca)
            | Q(nome_fantasia__iexact=empresa_busca)
            | Q(cnpj__iexact=empresa_busca)
        ).first()
        if empresa_exata:
            empresa_selecionada = empresa_exata
        elif empresas_filtradas.count() == 1:
            empresa_selecionada = empresas_filtradas.first()
        empresas = empresas_filtradas

    if empresa_selecionada:
        filiais = filiais.filter(empresa_id=empresa_selecionada.pk)
    if filial_busca:
        filiais_filtradas = filiais.filter(
            Q(razao_social__icontains=filial_busca)
            | Q(nome_fantasia__icontains=filial_busca)
            | Q(cnpj__icontains=filial_busca)
            | Q(cidade__icontains=filial_busca)
            | Q(empresa__razao_social__icontains=filial_busca)
            | Q(empresa__nome_fantasia__icontains=filial_busca)
        )
        filiais = filiais_filtradas

    filial_selecionada = None
    if filial_busca:
        filial_selecionada = filiais.filter(
            Q(razao_social__iexact=filial_busca)
            | Q(nome_fantasia__iexact=filial_busca)
            | Q(cnpj__iexact=filial_busca)
        ).first()
        if not filial_selecionada and filiais.count() == 1:
            filial_selecionada = filiais.first()
    empresa_contexto = empresa_selecionada
    if filial_selecionada:
        empresa_contexto = filial_selecionada.empresa
    politica_form = None
    politica_filial_origem = None
    replicacao_bloqueada = False
    if empresa_contexto:
        try:
            politica_filial_origem = filial_selecionada
            if not politica_filial_origem:
                politica_filial_origem = empresa_contexto.filiais.filter(ativo=True, is_matriz=True).first()
            if not politica_filial_origem:
                politica_filial_origem = empresa_contexto.filiais.filter(ativo=True).order_by('razao_social').first()
            if politica_filial_origem:
                replicacao_bloqueada = SeparacaoFilialService.replicacao_bloqueada(
                    politica_filial_origem,
                )
                politica = get_or_create_politica_filial(politica_filial_origem)
                politica_form = PoliticaReplicacaoForm(instance=politica)
        except Exception:
            messages.warning(
                request,
                'Nao foi possivel carregar a politica de replicacao agora. '
                'Confirme se as migrations ja rodaram no Railway.',
            )

    return render(request, 'core/admin/central.html', {
        'empresas': empresas,
        'filiais': filiais,
        'empresa_busca': empresa_busca,
        'filial_busca': filial_busca,
        'empresa_selecionada': empresa_selecionada,
        'empresa_contexto': empresa_contexto,
        'politica_form': politica_form,
        'politica_filial_origem': politica_filial_origem,
        'replicacao_bloqueada': replicacao_bloqueada,
        'filial_selecionada': filial_selecionada,
        'total_empresas': Empresa.objects.filter(ativo=True).count(),
        'total_filiais': Filial.objects.filter(
            ativo=True, empresa__ativo=True,
        ).count(),
        'total_super_admins': Usuario.objects.filter(is_superuser=True).count(),
        # Só os módulos que a empresa da filial pode ter -- um módulo de
        # outro vertical não aparece nem desmarcado, porque marcá-lo não
        # bastaria: quem concede é o segmento.
        'secoes_modulos': modulos_para_admin(
            filial_selecionada.empresa if filial_selecionada else None
        ),
        'segmentos': SEGMENTOS,
        'modulos_verticais': modulos_de_verticais(
            filial_selecionada.empresa if filial_selecionada else None
        ),
    })


@superuser_required
def railway_pool_list(request):
    """Painel sem segredos para capacidade dos projetos de bancos."""
    if request.method == 'POST':
        pool = get_object_or_404(RailwayProjectPool, pk=request.POST.get('pool_id'))
        from apps.core.services.railway_pool_service import RailwayPoolService

        ok, message = RailwayPoolService.validate_and_activate(pool)
        (messages.success if ok else messages.error)(request, message)
        return redirect('core:admin_railway_pool_list')

    pools = RailwayProjectPool.objects.annotate(
        total_bancos=Count('bancos'),
    ).order_by('prioridade', 'nome')
    return render(request, 'core/admin/railway_pool_list.html', {
        'pools': pools,
        'multi_project_enabled': settings.RAILWAY_MULTI_PROJECT_ENABLED,
        'total_projetos': pools.count(),
        'total_ativos': pools.filter(ativo=True, status=RailwayProjectPool.Status.ATIVO).count(),
        'total_bancos': EmpresaBanco.objects.count(),
        'page_title': 'Gestão Railway',
        'central_url': reverse('core:admin_central'),
    })


@superuser_required
def railway_pool_form(request, pk=None):
    pool = get_object_or_404(RailwayProjectPool, pk=pk) if pk else None
    form = RailwayProjectPoolAdminForm(request.POST or None, instance=pool)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        if pool:
            messages.success(request, f'Projeto Railway atualizado: {obj.nome}.')
        else:
            messages.success(
                request,
                f'Projeto Railway cadastrado: {obj.nome}. Valide antes de ativá-lo.',
            )
        return redirect('core:admin_railway_pool_list')

    return render(request, 'core/admin/railway_pool_form.html', {
        'form': form,
        'pool': pool,
        'page_title': 'Gestão Railway',
        'central_url': reverse('core:admin_central'),
        'cancel_url': reverse('core:admin_railway_pool_list'),
    })


@superuser_required
def empresa_banco_list(request):
    filtros = _empresa_filtros(request)
    ocultar_sem_banco = request.GET.get('ocultar_sem_banco', '1') != '0'
    if filtros.get('banco') == 'sem_banco':
        ocultar_sem_banco = False
    empresas = _aplicar_empresa_filtros(
        Empresa.objects.select_related(
            'banco_dedicado', 'banco_dedicado__railway_project_pool',
        ).prefetch_related('filiais').order_by('id'),
        filtros,
    )
    if ocultar_sem_banco:
        empresas = empresas.filter(banco_dedicado__isnull=False)
    bancos = {banco.empresa_id: banco for banco in EmpresaBanco.objects.select_related('empresa')}
    rows = [{'empresa': empresa, 'banco': bancos.get(empresa.pk)} for empresa in empresas]
    context = {
        'page_obj': _paginate(request, rows),
        'total': len(rows),
        'central_url': reverse('core:admin_central'),
        'ocultar_sem_banco': ocultar_sem_banco,
        'page_title': 'Bancos das empresas',
        'page_querystring': _page_querystring(request),
    }
    context.update(_empresa_filtros_context(filtros))
    return render(request, 'core/admin/empresa_banco_list.html', context)


@superuser_required
def empresa_banco_detail(request, pk):
    banco = get_object_or_404(
        EmpresaBanco.objects.select_related('empresa', 'railway_project_pool'),
        pk=pk,
    )
    return render(request, 'core/admin/empresa_banco_detail.html', {
        'banco': banco,
        'filiais': banco.empresa.filiais.order_by('-is_matriz', 'razao_social'),
        'page_title': 'Banco da empresa',
        'central_url': reverse('core:admin_central'),
    })


@superuser_required
@require_POST
def empresa_banco_create(request, empresa_id):
    """Registra o banco e leva o administrador ao fluxo seguro de detalhes.

    O provisionamento físico continua sob controle da Gestão Railway, que
    escolhe o projeto com capacidade antes de criar qualquer volume.
    """
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    banco, created = EmpresaBancoService.ensure_for_empresa(empresa)
    if created:
        messages.success(request, f'Banco dedicado preparado para {empresa}.')
    else:
        messages.info(request, f'{empresa} já possui banco dedicado registrado.')
    return redirect('core:admin_empresa_banco_progress', pk=banco.pk)


@superuser_required
def empresa_banco_progress(request, pk):
    banco = get_object_or_404(EmpresaBanco.objects.select_related('empresa'), pk=pk)
    payload = _empresa_banco_progress_payload(banco)
    return render(request, 'core/admin/empresa_banco_progress.html', {
        'banco': banco, 'payload': payload, 'payload_json': json.dumps(payload),
        'status_url': reverse('core:admin_empresa_banco_status', args=[banco.pk]),
        'start_url': reverse('core:admin_empresa_banco_start', args=[banco.pk]),
        'list_url': reverse('core:admin_empresa_banco_list'),
        'next_url': f"{reverse('core:admin_filial_list')}?empresa={banco.empresa_id}",
        'next_label': 'Ver filiais',
        'ready_status_text': 'Banco pronto. Abrindo as filiais...',
        'page_title': 'Central Administrativa',
    })


@superuser_required
def empresa_banco_status(request, pk):
    banco = get_object_or_404(EmpresaBanco.objects.select_related('empresa'), pk=pk)
    return JsonResponse(_empresa_banco_progress_payload(banco))


@superuser_required
@require_POST
def empresa_banco_start(request, pk):
    banco = get_object_or_404(EmpresaBanco.objects.select_related('empresa'), pk=pk)
    ok, _ = EmpresaBancoService.validar_empresa_preparavel(banco)
    if ok and banco.status != EmpresaBanco.Status.ATIVO:
        if banco.status == EmpresaBanco.Status.CONFIGURADO:
            EmpresaBancoService.migrar_banco(banco)
        elif banco.status == EmpresaBanco.Status.AGUARDANDO_CONFIGURACAO:
            EmpresaBancoService.testar_conexao(banco)
        elif banco.status == EmpresaBanco.Status.ERRO and EmpresaBancoService.tem_banco_provisionado(banco):
            EmpresaBancoService.testar_conexao(banco)
        else:
            EmpresaBancoService.solicitar_provisionamento(banco)
    return JsonResponse(_empresa_banco_progress_payload(banco))


@superuser_required
@require_POST
def empresa_banco_backup(request, pk):
    banco = get_object_or_404(EmpresaBanco.objects.select_related('empresa'), pk=pk)
    try:
        _, backup_path = EmpresaBancoService.gerar_backup_completo_persistente(banco)
    except Exception as exc:
        messages.error(request, f'Backup não foi gerado: {exc}')
        return redirect('core:admin_empresa_banco_list')
    return _file_download_response(backup_path, 'application/zip')


@superuser_required
@require_POST
def empresa_banco_excluir(request, pk):
    banco = get_object_or_404(EmpresaBanco.objects.select_related('empresa'), pk=pk)
    confirmacao = (request.POST.get('confirmacao') or '').strip()
    senha = request.POST.get('senha_master') or ''
    if not constant_time_compare(confirmacao.casefold(), banco.empresa.razao_social.strip().casefold()):
        messages.error(request, 'Digite a razão social completa para confirmar a exclusão.')
        return redirect('core:admin_empresa_banco_list')
    if not _senha_master_valida(request, senha):
        messages.error(request, 'Senha master inválida. Banco não excluído.')
        return redirect('core:admin_empresa_banco_list')
    try:
        _, backup_path, _ = EmpresaBancoService.excluir_banco_com_backup(banco)
    except Exception as exc:
        messages.error(request, f'Banco não foi excluído: {exc}')
        return redirect('core:admin_empresa_banco_list')
    return _file_download_response(backup_path, 'application/zip')


@superuser_required
@require_POST
def politica_replicacao_update(request, empresa_id):
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    filial_id = request.POST.get('filial_id')
    if not filial_id:
        messages.error(request, 'Selecione uma filial para alterar a politica de replicacao.')
        return redirect(request.META.get('HTTP_REFERER') or 'core:admin_central')
    filial = get_object_or_404(Filial, pk=filial_id, empresa=empresa)
    politica = get_or_create_politica_filial(filial)

    dados = request.POST.copy()
    dados['ativo'] = 'on'
    form = PoliticaReplicacaoForm(dados, instance=politica)
    if form.is_valid():
        form.save()
        participa_replicacao = request.POST.get('participa_replicacao') == 'on'
        if participa_replicacao and SeparacaoFilialService.replicacao_bloqueada(filial):
            participa_replicacao = False
            messages.warning(
                request,
                'Uma filial separada nao pode reativar replicacao automaticamente.',
            )
        if filial.participa_replicacao != participa_replicacao:
            filial.participa_replicacao = participa_replicacao
            filial.save(update_fields=['participa_replicacao', 'updated_at'])
        resultado_produtos = {
            'categorias': 0,
            'marcas': 0,
            'unidades': 0,
            'fiscal': 0,
            'produtos': 0,
            'tabelas': 0,
            'fichas': 0,
            'qualidade': 0,
            'erros': [],
        }
        try:
            resultado_produtos = ReplicacaoProdutoService.sincronizar_produtos_da_filial(filial)
        except Exception:
            messages.warning(
                request,
                'Politica salva, mas a sincronizacao imediata de produtos/fabricantes falhou. '
                'Fornecedores continuam independentes.',
            )
        try:
            ReplicacaoCadastrosService.sincronizar_fornecedores_da_filial(filial)
        except Exception:
            messages.warning(
                request,
                'Politica salva, mas a sincronizacao imediata de fornecedores falhou. '
                'Os demais cadastros continuam independentes.',
            )
        if resultado_produtos.get('erros'):
            messages.warning(
                request,
                'Politica salva, mas alguns grupos nao sincronizaram agora: '
                f'{", ".join(resultado_produtos["erros"][:2])}.',
            )
        messages.success(request, f'Politica de replicacao atualizada para {filial}.')
    else:
        messages.error(request, 'Nao foi possivel salvar a politica de replicacao.')
    return redirect(request.META.get('HTTP_REFERER') or 'core:admin_central')


@superuser_required
@require_POST
def filial_imagem_update(request, filial_id):
    filial = get_object_or_404(Filial, pk=filial_id)
    imagem = request.FILES.get('imagem')
    remover = request.POST.get('remover_imagem') == '1'
    voltar_para = request.META.get('HTTP_REFERER') or reverse('core:admin_central')

    if remover:
        if filial.imagem:
            filial.imagem.delete(save=False)
            filial.imagem = None
            filial.save(update_fields=['imagem'])
            messages.success(request, 'Imagem da filial removida.')
        return redirect(voltar_para)

    if not imagem:
        messages.error(request, 'Selecione uma imagem para atualizar a filial.')
        return redirect(voltar_para)

    if imagem.size > 5 * 1024 * 1024:
        messages.error(request, 'Envie uma imagem com ate 5 MB.')
        return redirect(voltar_para)

    if imagem.content_type and not imagem.content_type.startswith('image/'):
        messages.error(request, 'O arquivo selecionado precisa ser uma imagem.')
        return redirect(voltar_para)

    imagem_antiga = filial.imagem.name if filial.imagem else ''
    try:
        filial.imagem = preparar_imagem_filial(imagem)
    except Exception:
        messages.error(request, 'Nao foi possivel processar essa imagem.')
        return redirect(voltar_para)

    filial.save(update_fields=['imagem'])
    if imagem_antiga and imagem_antiga != filial.imagem.name:
        filial.imagem.storage.delete(imagem_antiga)

    messages.success(request, 'Imagem da filial atualizada.')
    return redirect(voltar_para)


@superuser_required
@require_POST
def segmento_update(request, empresa_id):
    """Define o vertical da empresa — é o que libera os módulos especializados."""
    from apps.core.constants.segmentos import LABEL_POR_SEGMENTO

    empresa = get_object_or_404(Empresa, pk=empresa_id)
    voltar_para = request.META.get('HTTP_REFERER') or reverse('core:admin_central')

    segmento = (request.POST.get('segmento') or '').strip()
    if segmento and segmento not in LABEL_POR_SEGMENTO:
        messages.error(request, 'Segmento inválido.')
        return redirect(voltar_para)

    # Habilitação manual: módulos de vertical marcados que o segmento não
    # concede. Só as chaves conhecidas entram, para um valor antigo de
    # módulo renomeado não ficar preso na lista.
    from apps.core.constants.modulos import MODULOS_POR_CHAVE
    marcados = set(request.POST.getlist('modulo_extra'))
    empresa.modulos_extras = sorted(
        c for c in marcados
        if c in MODULOS_POR_CHAVE and segmento not in MODULOS_POR_CHAVE[c].segmentos
    )

    empresa.segmento = segmento
    empresa.save(update_fields=['segmento', 'modulos_extras'])

    nome = empresa.nome_fantasia or empresa.razao_social
    if segmento:
        messages.success(
            request,
            f'{nome} agora é {LABEL_POR_SEGMENTO[segmento]}. '
            f'Os módulos desse vertical já estão liberados para as filiais dela.',
        )
    else:
        messages.success(
            request,
            f'{nome} ficou sem vertical — só os módulos universais seguem disponíveis.',
        )
    return redirect(voltar_para)


@superuser_required
@require_POST
def modulos_update(request, filial_id):
    """Liga/desliga secoes inteiras do menu (Cadastros, Operacoes,
    Financeiro, Logistica, Avancado, Food Service) para uma filial --
    escondidas no sidebar e bloqueadas no FilialMiddleware."""
    filial = get_object_or_404(Filial, pk=filial_id)
    voltar_para = request.META.get('HTTP_REFERER') or reverse('core:admin_central')

    # Só desliga o que a empresa poderia ter: assim um módulo indisponível
    # não entra na lista de desativados sem necessidade, e se o segmento
    # mudar depois ele já nasce ligado.
    disponiveis = modulos_disponiveis(filial.empresa)
    ativos = set(request.POST.getlist('modulo'))
    filial.modulos_desativados = sorted(disponiveis - ativos)
    filial.save(update_fields=['modulos_desativados'])
    messages.success(request, f'Modulos atualizados para {filial.nome_fantasia or filial.razao_social}.')
    return redirect(voltar_para)


@superuser_required
def empresa_list(request):
    filtros = _empresa_filtros(request)
    ocultar_inativas = request.GET.get('ocultar_inativas', '1') != '0'
    queryset = _aplicar_empresa_filtros(
        Empresa.objects.select_related('banco_dedicado').order_by('id'), filtros,
    )
    if ocultar_inativas:
        queryset = queryset.filter(ativo=True)
    total = queryset.count()
    page_obj = _paginate(request, queryset)
    bancos = {
        banco.empresa_id: banco
        for banco in EmpresaBanco.objects.filter(
            empresa_id__in=[empresa.pk for empresa in page_obj.object_list]
        )
    }
    page_obj.object_list = [
        {'empresa': empresa, 'banco': bancos.get(empresa.pk)}
        for empresa in page_obj.object_list
    ]
    context = {
        'page_obj': page_obj,
        'total': total,
        'page_querystring': _page_querystring(request),
        'can_manage_structure': True,
        'central_url': reverse('core:admin_central'),
        'page_title': 'Central Administrativa',
        'ocultar_inativas': ocultar_inativas,
    }
    context.update(_empresa_filtros_context(filtros))
    return render(request, 'core/admin/empresa_list.html', context)


@superuser_required
def empresa_form(request, pk=None):
    empresa = get_object_or_404(Empresa, pk=pk) if pk else None
    form = EmpresaAdminForm(request.POST or None, instance=empresa)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        action = 'atualizada' if empresa else 'cadastrada'
        messages.success(request, f'Empresa {action}: {obj.razao_social}.')
        return redirect('core:admin_empresa_list')

    context = {
        'title': 'Editar empresa' if empresa else 'Nova empresa',
        'form': form,
        'cancel_url': reverse('core:admin_empresa_list'),
        'cancel_label': 'Listar empresas',
        'submit_label': 'Salvar empresa',
        'central_url': reverse('core:admin_central'),
        'page_title': 'Central Administrativa',
        'form_layout': 'empresa',
        'is_edit': bool(empresa),
    }
    if empresa:
        context.update(core_log_context(empresa, 'empresas', 'Empresa', request.user))
    return render(request, 'core/admin/form.html', context)


@superuser_required
def empresa_toggle(request, pk):
    return _toggle(request, Empresa, pk, 'core:admin_empresa_list')


@superuser_required
def filial_list(request):
    filtros = {
        'nome': request.GET.get('nome', '').strip(),
        'cnpj': request.GET.get('cnpj', '').strip(),
        'empresa': request.GET.get('empresa', '').strip(),
        'cidade': request.GET.get('cidade', '').strip(),
        'uf': request.GET.get('uf', '').strip().upper(),
        'tipo': request.GET.get('tipo', '').strip(),
    }
    busca_legada = request.GET.get('q', '').strip()
    ocultar_inativas = request.GET.get('ocultar_inativas', '1') != '0'
    queryset = _filiais_scope(request.user).select_related('empresa').order_by('id')
    if filtros['nome']:
        queryset = queryset.filter(
            Q(razao_social__icontains=filtros['nome'])
            | Q(nome_fantasia__icontains=filtros['nome'])
        )
    elif busca_legada:
        queryset = queryset.filter(
            Q(razao_social__icontains=busca_legada)
            | Q(nome_fantasia__icontains=busca_legada)
            | Q(cnpj__icontains=busca_legada)
            | Q(cidade__icontains=busca_legada)
        )
    documento = ''.join(ch for ch in filtros['cnpj'] if ch.isdigit())
    if documento:
        queryset = queryset.filter(cnpj__icontains=documento)
    if filtros['empresa'].isdigit():
        queryset = queryset.filter(empresa_id=int(filtros['empresa']))
    if filtros['cidade']:
        queryset = queryset.filter(cidade__icontains=filtros['cidade'])
    if filtros['uf']:
        queryset = queryset.filter(uf=filtros['uf'])
    if filtros['tipo'] == 'matriz':
        queryset = queryset.filter(is_matriz=True)
    elif filtros['tipo'] == 'filial':
        queryset = queryset.filter(is_matriz=False)
    if ocultar_inativas:
        queryset = queryset.filter(ativo=True)

    return render(request, 'core/admin/filial_list.html', {
        'page_obj': _paginate(request, queryset),
        'filtros': filtros,
        'ocultar_inativas': ocultar_inativas,
        'tem_filtro': bool(busca_legada or any(filtros.values())),
        'empresas': Empresa.objects.order_by('id'),
        'uf_choices': UF.choices,
        'total': queryset.count(),
        'page_querystring': _page_querystring(request),
        'can_manage_structure': True,
        'central_url': reverse('core:admin_central'),
        'page_title': 'Central Administrativa',
    })


@superuser_required
def filial_form(request, pk=None):
    filial = get_object_or_404(Filial, pk=pk) if pk else None
    form = FilialAdminForm(request.POST or None, request.FILES or None, instance=filial)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        try:
            form.salvar_politica_replicacao(obj)
        except Exception:
            messages.warning(
                request,
                'Filial salva, mas nao foi possivel salvar a politica de replicacao agora. '
                'Confirme se as migrations ja rodaram no Railway.',
            )
        resultado_produtos = {
            'categorias': 0,
            'marcas': 0,
            'unidades': 0,
            'fiscal': 0,
            'produtos': 0,
            'tabelas': 0,
            'fichas': 0,
            'erros': [],
        }
        try:
            resultado_produtos = ReplicacaoProdutoService.sincronizar_produtos_da_filial(obj)
        except Exception:
            messages.warning(
                request,
                'Filial salva, mas a sincronizacao imediata de produtos/fabricantes falhou. '
                'Fornecedores continuam independentes.',
            )
        try:
            ReplicacaoCadastrosService.sincronizar_fornecedores_da_filial(obj)
        except Exception:
            messages.warning(
                request,
                'Filial salva, mas a sincronizacao imediata de fornecedores falhou. '
                'Os demais cadastros continuam independentes.',
            )
        if resultado_produtos.get('erros'):
            messages.warning(
                request,
                'Filial salva, mas alguns grupos nao sincronizaram agora: '
                f'{", ".join(resultado_produtos["erros"][:2])}.',
            )
        action = 'atualizada' if filial else 'cadastrada'
        messages.success(request, f'Filial {action}: {obj.razao_social}.')
        return redirect('core:admin_filial_list')

    context = {
        'title': 'Editar filial' if filial else 'Nova filial',
        'form': form,
        'cancel_url': reverse('core:admin_filial_list'),
        'cancel_label': 'Listar filiais',
        'submit_label': 'Salvar filial',
        'central_url': reverse('core:admin_central'),
        'page_title': 'Central Administrativa',
        'form_layout': 'filial',
        'is_edit': bool(filial),
    }
    if filial:
        context.update(core_log_context(filial, 'filiais', 'Filial', request.user))
    return render(request, 'core/admin/form.html', context)


@superuser_required
def filial_toggle(request, pk):
    return _toggle(request, Filial, pk, 'core:admin_filial_list')


@superuser_required
def filial_separar(request, pk):
    filial = get_object_or_404(Filial.objects.select_related('empresa'), pk=pk)
    processo = SeparacaoFilialService.obter_ou_criar(filial, request.user)
    simulacao = SeparacaoFilialService.simular(processo)
    if request.method == 'POST':
        if request.POST.get('acao') == 'executar':
            confirmacao = (request.POST.get('confirmacao') or '').strip().upper()
            senha_master = request.POST.get('senha_master') or ''
            if confirmacao != 'SEPARAR':
                messages.error(request, 'Digite SEPARAR para confirmar a separação da filial.')
            elif not _senha_master_valida(request, senha_master):
                messages.error(request, 'Senha master inválida.')
            else:
                try:
                    processo = SeparacaoFilialService.confirmar_execucao(processo, request.user)
                except SeparacaoFilialError as exc:
                    messages.error(request, f'Não foi possível separar a filial: {exc}')
                else:
                    return redirect('core:admin_filial_separar_progress', pk=processo.pk)
        processo.refresh_from_db()
        simulacao = SeparacaoFilialService.simular(processo)
    return render(request, 'core/admin/filial_separar.html', {
        'filial': filial, 'processo': processo, 'simulacao': simulacao,
        'bloqueios': simulacao.get('bloqueios', []),
        'avisos': simulacao.get('avisos', []),
        'usuarios': simulacao.get('usuarios', {}),
        'contagens': simulacao.get('contagens', []),
        'operacoes_abertas': simulacao.get('operacoes_abertas', []),
        'plano': simulacao.get('plano', {}),
        'central_url': reverse('core:admin_central'),
        'page_title': 'Central Administrativa',
    })


@superuser_required
def filial_separar_progress(request, pk):
    processo = get_object_or_404(
        SeparacaoFilial.objects.select_related(
            'filial_origem', 'empresa_origem', 'empresa_destino', 'banco_destino',
        ), pk=pk,
    )
    payload = SeparacaoFilialService.payload_progresso(processo)
    return render(request, 'core/admin/filial_separar_progress.html', {
        'processo': processo, 'payload': payload, 'payload_json': json.dumps(payload),
        'status_url': reverse('core:admin_filial_separar_status', args=[processo.pk]),
        'start_url': reverse('core:admin_filial_separar_start', args=[processo.pk]),
        'list_url': reverse('core:admin_filial_list'),
        'backup_restauracao_url': reverse(
            'core:admin_filial_separar_download', args=[processo.pk, 'restauracao'],
        ),
        'backup_exportacao_url': reverse(
            'core:admin_filial_separar_download', args=[processo.pk, 'exportacao'],
        ),
        'usuarios_url': reverse('core:admin_usuario_list') + '?central=1',
        'page_title': 'Central Administrativa',
    })


@superuser_required
def filial_separar_status(request, pk):
    processo = get_object_or_404(SeparacaoFilial, pk=pk)
    return JsonResponse(SeparacaoFilialService.payload_progresso(processo))


@superuser_required
@require_POST
def filial_separar_start(request, pk):
    processo = get_object_or_404(SeparacaoFilial, pk=pk)
    if processo.status == SeparacaoFilial.Status.EXECUTANDO:
        if processo.updated_at and processo.updated_at < timezone.now() - timedelta(minutes=15):
            processo.status = SeparacaoFilial.Status.ERRO
            processo.ultimo_erro = 'A execução anterior ficou sem atualização por mais de 15 minutos.'
            processo.etapa_atual = 'Execução interrompida'
            processo.save(update_fields=['status', 'ultimo_erro', 'etapa_atual', 'updated_at'])
        return JsonResponse(SeparacaoFilialService.payload_progresso(processo))
    if processo.status != SeparacaoFilial.Status.CONCLUIDO:
        try:
            SeparacaoFilialService.enfileirar_execucao(processo, request.user)
        except SeparacaoFilialError:
            pass
    return JsonResponse(SeparacaoFilialService.payload_progresso(processo))


@superuser_required
def filial_separar_download(request, pk, tipo):
    processo = get_object_or_404(SeparacaoFilial, pk=pk)
    if tipo == 'restauracao':
        path, content_type = processo.backup_path, 'application/zip'
    elif tipo == 'exportacao':
        path, content_type = processo.backup_exportacao_path, 'application/json; charset=utf-8'
    else:
        raise PermissionDenied('Arquivo inválido.')
    if not path or (not Path(path).exists() and not default_storage.exists(path)):
        messages.error(request, 'Arquivo ainda não foi gerado ou não está disponível.')
        return redirect('core:admin_filial_separar_progress', pk=processo.pk)
    return _file_download_response(path, content_type)


@admin_area_required
def usuario_list(request):
    busca = request.GET.get('q', '').strip()
    perfil_id = request.GET.get('perfil', '').strip()
    situacao = request.GET.get('situacao', '').strip()
    empresa_id = request.GET.get('empresa', '').strip()
    filial_id = request.GET.get('filial', '').strip()
    super_admins = request.user.is_superuser and request.GET.get('super_admins') == '1'
    central_global = _is_central_global(request)
    if super_admins:
        queryset = Usuario.objects.select_related('empresa', 'filial', 'perfil').filter(is_superuser=True).order_by('nome')
    elif central_global:
        queryset = Usuario.objects.select_related('empresa', 'filial', 'perfil').prefetch_related(
            'acessos_filiais__filial',
            'acessos_filiais__perfil',
        ).order_by('nome')
    else:
        queryset = _usuarios_scope(request).order_by('nome')
    has_scope_filter = bool(busca or perfil_id or situacao or empresa_id or filial_id)
    if central_global and not has_scope_filter:
        queryset = queryset.none()
    if busca:
        queryset = queryset.filter(
            Q(nome__icontains=busca)
            | Q(email__icontains=busca)
            | Q(cpf__icontains=busca)
        )
    if perfil_id.isdigit() and not super_admins:
        queryset = queryset.filter(
            Q(perfil_id=int(perfil_id))
            | Q(acessos_filiais__perfil_id=int(perfil_id), acessos_filiais__ativo=True)
        ).distinct()
    if central_global and empresa_id.isdigit():
        queryset = queryset.filter(empresa_id=int(empresa_id))
    if central_global and filial_id.isdigit():
        queryset = queryset.filter(
            Q(filial_id=int(filial_id))
            | Q(acessos_filiais__filial_id=int(filial_id), acessos_filiais__ativo=True)
        ).distinct()
    if situacao == 'ativo':
        queryset = queryset.filter(ativo=True)
    elif situacao == 'inativo':
        queryset = queryset.filter(ativo=False)

    if super_admins:
        perfis = PerfilAcesso.objects.none()
    elif central_global:
        perfis = PerfilAcesso.objects.select_related('empresa').order_by('empresa__razao_social', 'nome')
    else:
        perfis = _perfis_scope(request).order_by('nome')
    return render(request, 'core/admin/usuario_list.html', {
        'page_obj': _paginate(request, queryset),
        'busca': busca,
        'perfil_id': perfil_id,
        'situacao': situacao,
        'empresa_id': empresa_id,
        'filial_id': filial_id,
        'empresas': Empresa.objects.order_by('razao_social') if central_global else Empresa.objects.none(),
        'filiais': Filial.objects.select_related('empresa').order_by('empresa__razao_social', 'razao_social') if central_global else Filial.objects.none(),
        'perfis': perfis,
        'total': queryset.count(),
        'super_admins': super_admins,
        'central_global': central_global,
        'central_filial': _active_filial(request) if request.user.is_superuser and request.GET.get('central_filial') else None,
        'central_url': reverse('core:admin_central'),
    })


@admin_area_required
def usuario_form(request, pk=None):
    usuario = None
    super_admin_context = request.user.is_superuser and (request.GET.get('super_admin') == '1' or request.POST.get('super_admin') == '1')
    central_global = _is_central_global(request) or (request.user.is_superuser and request.POST.get('central') == '1')
    if pk:
        if super_admin_context:
            usuario = get_object_or_404(Usuario, pk=pk, is_superuser=True)
        elif central_global:
            usuario = get_object_or_404(Usuario, pk=pk)
        else:
            usuario = _require_object_in_scope(
                get_object_or_404(Usuario, pk=pk),
                _usuarios_scope(request),
            )
        if request.user.is_superuser and usuario.is_superuser and not request.GET.get('central_filial') and not request.POST.get('central_filial'):
            super_admin_context = True
    form = UsuarioAdminForm(
        request.POST or None,
        request.FILES or None,
        instance=usuario,
        actor=request.user,
        scope_filial=None if central_global else _active_filial(request),
        super_admin_context=super_admin_context,
    )
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        action = 'atualizado' if usuario else 'cadastrado'
        messages.success(request, f'Usuario {action}: {obj.nome}.')
        return redirect(_central_redirect(request, 'core:admin_usuario_list'))

    context = {
        'title': ('Editar super administrador' if usuario else 'Novo super administrador') if super_admin_context else ('Editar usuario' if usuario else 'Novo usuario'),
        'form': form,
        'cancel_url': _central_redirect(request, 'core:admin_usuario_list'),
        'cancel_label': 'Listar super admins' if super_admin_context else 'Voltar',
        'submit_label': 'Salvar usuario',
        'central_url': reverse('core:admin_central') if request.user.is_superuser and (request.GET.get('central_filial') or super_admin_context or central_global) else '',
        'hidden_fields': {'super_admin': '1'} if super_admin_context else ({'central': '1'} if central_global else {}),
        'usuario_layout': True,
        'is_edit': bool(usuario),
    }
    if usuario:
        context.update(core_log_context(usuario, 'usuarios', 'Usuario', request.user))
    return render(request, 'core/admin/form.html', context)


@admin_area_required
def usuario_toggle(request, pk):
    if request.user.is_superuser and request.GET.get('super_admins') == '1':
        usuario = get_object_or_404(Usuario, pk=pk, is_superuser=True)
    elif _is_central_global(request):
        usuario = get_object_or_404(Usuario, pk=pk)
    else:
        usuario = _require_object_in_scope(
            get_object_or_404(Usuario, pk=pk),
            _usuarios_scope(request),
        )
    if usuario.pk == request.user.pk:
        raise PermissionDenied('Voce nao pode alterar o proprio status.')
    usuario.ativo = not usuario.ativo
    usuario.save(update_fields=['ativo'])
    state = 'ativado' if usuario.ativo else 'desativado'
    messages.success(request, f'{usuario.nome} {state}.')
    return redirect(_central_redirect(request, 'core:admin_usuario_list'))


@admin_area_required
@require_POST
def usuario_encerrar_sessoes(request, pk):
    """
    Derruba o acesso do usuário em todos os aparelhos, sem mexer na senha.

    Para celular perdido, trocado ou funcionário desligado. Trocar a senha
    também derrubaria, mas obriga a combinar uma senha nova com quem está na
    rua — exatamente o que não dá para fazer quando o aparelho sumiu.
    """
    from apps.core.services.sessoes import encerrar_sessoes

    if request.user.is_superuser and request.GET.get('super_admins') == '1':
        usuario = get_object_or_404(Usuario, pk=pk, is_superuser=True)
    elif _is_central_global(request):
        usuario = get_object_or_404(Usuario, pk=pk)
    else:
        usuario = _require_object_in_scope(
            get_object_or_404(Usuario, pk=pk),
            _usuarios_scope(request),
        )

    # Encerrar as próprias sessões preserva a atual: um botão numa lista não
    # deveria deslogar quem clicou nele.
    quantas = encerrar_sessoes(usuario, preservar=request.session.session_key)

    if quantas:
        messages.success(
            request,
            f'{quantas} sessao(oes) de {usuario.nome} encerrada(s). '
            'Ele precisara entrar de novo.',
        )
    else:
        messages.info(request, f'{usuario.nome} nao tem sessao ativa.')
    return redirect(_central_redirect(request, 'core:admin_usuario_list'))


@admin_area_required
def perfil_list(request):
    busca = request.GET.get('q', '').strip()
    empresa_id = request.GET.get('empresa', '').strip()
    filial_id = request.GET.get('filial', '').strip()
    central_global = _is_central_global(request)
    queryset = PerfilAcesso.objects.select_related('empresa').order_by('empresa__razao_social', 'nome') if central_global else _perfis_scope(request).order_by('nome')
    has_scope_filter = bool(busca or empresa_id or filial_id)
    if central_global and not has_scope_filter:
        queryset = queryset.none()
    if busca:
        queryset = queryset.filter(
            Q(nome__icontains=busca)
            | Q(empresa__razao_social__icontains=busca)
            | Q(empresa__nome_fantasia__icontains=busca)
        )
    if central_global and empresa_id.isdigit():
        queryset = queryset.filter(empresa_id=int(empresa_id))
    if central_global and filial_id.isdigit():
        filial = Filial.objects.filter(pk=int(filial_id)).first()
        if filial:
            queryset = queryset.filter(empresa_id=filial.empresa_id)
        else:
            queryset = queryset.none()
    return render(request, 'core/admin/perfil_list.html', {
        'page_obj': _paginate(request, queryset),
        'busca': busca,
        'empresa_id': empresa_id,
        'filial_id': filial_id,
        'empresas': Empresa.objects.order_by('razao_social') if central_global else Empresa.objects.none(),
        'filiais': Filial.objects.select_related('empresa').order_by('empresa__razao_social', 'razao_social') if central_global else Filial.objects.none(),
        'total': queryset.count(),
        'central_global': central_global,
        'central_filial': _active_filial(request) if request.user.is_superuser and request.GET.get('central_filial') else None,
        'central_url': reverse('core:admin_central'),
    })


@admin_area_required
def perfil_form(request, pk=None):
    perfil = None
    central_global = _is_central_global(request) or (request.user.is_superuser and request.POST.get('central') == '1')
    if pk:
        perfil = get_object_or_404(PerfilAcesso, pk=pk) if central_global else _require_object_in_scope(
            get_object_or_404(PerfilAcesso, pk=pk),
            _perfis_scope(request),
        )
    form = PerfilAcessoAdminForm(
        request.POST or None,
        instance=perfil,
        actor=request.user,
        scope_filial=None if central_global else (_active_filial(request) if request.user.is_superuser else None),
    )
    matrix = PermissaoMatrix(perfil=perfil, data=request.POST if request.method == 'POST' else None)

    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        matrix.save(obj)
        action = 'atualizado' if perfil else 'cadastrado'
        messages.success(request, f'Perfil {action}: {obj.nome}.')
        return redirect(_central_redirect(request, 'core:admin_perfil_list'))

    context = {
        'title': 'Editar perfil' if perfil else 'Novo perfil',
        'form': form,
        'perfil': perfil,
        'cancel_url': _central_redirect(request, 'core:admin_perfil_list'),
        'matrix_rows': matrix.rows(),
        'matrix_labels': matrix.field_labels,
        'central_filial': _active_filial(request) if request.user.is_superuser and request.GET.get('central_filial') else None,
        'central_url': reverse('core:admin_central') if request.user.is_superuser and (request.GET.get('central_filial') or central_global) else '',
        'hidden_fields': {'central': '1'} if central_global else {},
    }
    if perfil:
        context.update(core_log_context(perfil, 'perfis', 'Perfil', request.user))
    return render(request, 'core/admin/perfil_form.html', context)


@admin_area_required
def perfil_toggle(request, pk):
    perfil = get_object_or_404(PerfilAcesso, pk=pk) if _is_central_global(request) else _require_object_in_scope(
        get_object_or_404(PerfilAcesso, pk=pk),
        _perfis_scope(request),
    )
    perfil.ativo = not perfil.ativo
    perfil.save(update_fields=['ativo'])
    state = 'ativado' if perfil.ativo else 'desativado'
    messages.success(request, f'{perfil.nome} {state}.')
    return redirect(_central_redirect(request, 'core:admin_perfil_list'))


@admin_area_required
@require_POST
def perfil_duplicar(request, pk):
    perfil = get_object_or_404(PerfilAcesso, pk=pk) if _is_central_global(request) else _require_object_in_scope(
        get_object_or_404(PerfilAcesso, pk=pk),
        _perfis_scope(request),
    )
    base_name = f'{perfil.nome} - copia'
    new_name = base_name
    counter = 2
    while PerfilAcesso.objects.filter(empresa=perfil.empresa, nome=new_name).exists():
        new_name = f'{base_name} {counter}'
        counter += 1

    novo = PerfilAcesso.objects.create(
        empresa=perfil.empresa,
        nome=new_name,
        descricao=perfil.descricao,
        is_admin=perfil.is_admin if _is_company_admin(request.user) else False,
        ativo=True,
    )
    permissoes = [
        Permissao(
            perfil=novo,
            modulo=perm.modulo,
            pode_ver=perm.pode_ver,
            pode_criar=perm.pode_criar,
            pode_editar=perm.pode_editar,
            pode_excluir=perm.pode_excluir,
            pode_cancelar=perm.pode_cancelar,
            pode_aprovar=perm.pode_aprovar,
            pode_exportar=perm.pode_exportar,
        )
        for perm in perfil.permissoes.all()
    ]
    Permissao.objects.bulk_create(permissoes)
    messages.success(request, f'Perfil duplicado com permissoes: {novo.nome}.')
    url = reverse('core:admin_perfil_edit', args=[novo.pk])
    filial = _active_filial(request)
    if request.user.is_superuser and request.GET.get('central_filial') and filial:
        url = f'{url}?central_filial={filial.pk}'
    return redirect(url)


# ---------------------------------------------------------------------------
# MANUTENCAO TEMPORARIA — remover apos uso
# ---------------------------------------------------------------------------
from apps.core.views._admin import superuser_required


@superuser_required
def limpar_documentos_fiscais(request):
    """
    GET  → mostra contagens (dry-run)
    POST → apaga DocumentoFiscal NFC-e/NF-e, LogIntegracaoFiscal,
           reseta proximo_numero_nfce para 1.

    Protegido por superuser_required. Remover apos uso.
    """
    from apps.financeiro.models.fiscal import DocumentoFiscal, LogIntegracaoFiscal
    from apps.core.models.empresa import Filial

    docs_qs = DocumentoFiscal.objects.filter(tipo_documento__in=["nfce", "nfe"])
    logs_qs = LogIntegracaoFiscal.objects.all()
    filiais_qs = Filial.objects.filter(proximo_numero_nfce__gt=1)

    n_docs = docs_qs.count()
    n_logs = logs_qs.count()
    n_filiais = filiais_qs.count()

    if request.method == "POST":
        with transaction.atomic(using='default'):
            log_del, _ = logs_qs.delete()
            doc_del, _ = docs_qs.delete()
            for f in filiais_qs:
                f.proximo_numero_nfce = 1
                f.save(update_fields=["proximo_numero_nfce"])
        return JsonResponse({
            "ok": True,
            "documentos_apagados": doc_del,
            "logs_apagados": log_del,
            "filiais_resetadas": n_filiais,
        })

    # GET — exibe contagens
    from django.http import HttpResponse
    from django.middleware.csrf import get_token
    csrf = get_token(request)
    html = f"""
    <html><body style="font-family:sans-serif;padding:2rem">
    <h2>Limpeza de Documentos Fiscais</h2>
    <p>Esta acao e <strong>irreversivel</strong>.</p>
    <ul>
      <li>DocumentoFiscal (NFC-e / NF-e): <strong>{n_docs}</strong></li>
      <li>LogIntegracaoFiscal: <strong>{n_logs}</strong></li>
      <li>Filiais com numero &gt; 1: <strong>{n_filiais}</strong></li>
    </ul>
    <form method="POST">
      <input type="hidden" name="csrfmiddlewaretoken" value="{csrf}">
      <button type="submit"
        style="background:red;color:white;padding:1rem 2rem;font-size:1.1rem;border:none;cursor:pointer;border-radius:6px">
        CONFIRMAR — Apagar tudo e resetar numeracao
      </button>
    </form>
    </body></html>
    """
    return HttpResponse(html)
