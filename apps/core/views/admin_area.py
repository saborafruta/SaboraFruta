from django.contrib import messages
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.cadastros.services.replicacao_service import ReplicacaoCadastrosService
from apps.core.forms.admin_forms import (
    EmpresaAdminForm,
    FilialAdminForm,
    get_or_create_politica_filial,
    PerfilAcessoAdminForm,
    PermissaoMatrix,
    PoliticaReplicacaoForm,
    UsuarioAdminForm,
)
from apps.core.constants.segmentos import SEGMENTOS
from apps.core.services.modulos import modulos_disponiveis, modulos_para_admin
from apps.core.models import Empresa, Filial, PerfilAcesso, Permissao, Usuario
from apps.core.services.imagem_filial import preparar_imagem_filial
from apps.core.views.audit import core_log_context
from apps.core.views._admin import admin_area_required, superuser_required
from apps.produtos.services.replicacao_service import ReplicacaoProdutoService


PER_PAGE = 25


def _paginate(request, queryset):
    return Paginator(queryset, PER_PAGE).get_page(request.GET.get('page', 1))


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
    empresas = Empresa.objects.order_by('razao_social')
    filiais = Filial.objects.select_related('empresa').order_by('empresa__razao_social', 'razao_social')
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
    if empresa_contexto:
        try:
            politica_filial_origem = filial_selecionada
            if not politica_filial_origem:
                politica_filial_origem = empresa_contexto.filiais.filter(ativo=True, is_matriz=True).first()
            if not politica_filial_origem:
                politica_filial_origem = empresa_contexto.filiais.filter(ativo=True).order_by('razao_social').first()
            if politica_filial_origem:
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
        'filial_selecionada': filial_selecionada,
        'total_empresas': Empresa.objects.count(),
        'total_filiais': Filial.objects.count(),
        'total_super_admins': Usuario.objects.filter(is_superuser=True).count(),
        # Só os módulos que a empresa da filial pode ter -- um módulo de
        # outro vertical não aparece nem desmarcado, porque marcá-lo não
        # bastaria: quem concede é o segmento.
        'secoes_modulos': modulos_para_admin(
            filial_selecionada.empresa if filial_selecionada else None
        ),
        'segmentos': SEGMENTOS,
    })


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

    empresa.segmento = segmento
    empresa.save(update_fields=['segmento'])

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
    busca = request.GET.get('q', '').strip()
    queryset = Empresa.objects.order_by('razao_social')
    if busca:
        queryset = queryset.filter(
            Q(razao_social__icontains=busca)
            | Q(nome_fantasia__icontains=busca)
            | Q(cnpj__icontains=busca)
        )
    return render(request, 'core/admin/empresa_list.html', {
        'page_obj': _paginate(request, queryset),
        'busca': busca,
        'total': queryset.count(),
        'can_manage_structure': True,
        'central_url': reverse('core:admin_central'),
        'page_title': 'Central Administrativa',
    })


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
    busca = request.GET.get('q', '').strip()
    empresa_id = request.GET.get('empresa', '').strip()
    queryset = _filiais_scope(request.user).select_related('empresa').order_by(
        'empresa__razao_social',
        'razao_social',
    )
    if busca:
        queryset = queryset.filter(
            Q(razao_social__icontains=busca)
            | Q(nome_fantasia__icontains=busca)
            | Q(cnpj__icontains=busca)
            | Q(cidade__icontains=busca)
        )
    if empresa_id.isdigit():
        queryset = queryset.filter(empresa_id=int(empresa_id))

    return render(request, 'core/admin/filial_list.html', {
        'page_obj': _paginate(request, queryset),
        'busca': busca,
        'empresa_id': empresa_id,
        'empresas': Empresa.objects.order_by('razao_social'),
        'total': queryset.count(),
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
        with transaction.atomic():
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
