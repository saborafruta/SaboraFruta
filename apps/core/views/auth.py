"""Views de autenticação: login, logout, seleção e troca de filial."""
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.decorators.http import require_POST

from apps.core.forms import LoginForm
from apps.core.models import Empresa, Filial, FilialFavorita, Usuario
from apps.core.services.auth_service import AuthService
from apps.core.services.exceptions import DomainError
from apps.core.services.home import nome_rota_inicial


class LoginView(View):
    template_name = 'core/auth/login.html'

    def get(self, request):
        if request.user.is_authenticated:
            return redirect(nome_rota_inicial(request.user, getattr(request, 'filial_ativa', None)))
        return render(request, self.template_name, {'form': LoginForm()})

    def post(self, request):
        form = LoginForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})

        try:
            user = AuthService.login(
                request,
                email=form.cleaned_data['email'],
                senha=form.cleaned_data['senha'],
            )
        except DomainError as e:
            messages.error(request, str(e))
            return render(request, self.template_name, {'form': form})

        login(request, user)
        filiais = _filiais_permitidas(user)
        if user.is_superuser:
            request.session.pop('filial_ativa_id', None)
            return redirect('core:selecionar-filial')
        if filiais.count() == 1:
            filial_central = filiais.first()
            rota_inicial = nome_rota_inicial(user, filial_central)
            AuthService.trocar_filial(request, filial_central.pk)
            return redirect(rota_inicial)
        return redirect('core:selecionar-filial')


def _filiais_permitidas(user):
    # A regra mora no usuario -- ver `Usuario.filiais_permitidas`. Quando ela
    # vivia aqui tambem, a tela de escolha oferecia unidade que o vinculo do
    # login nao dava, e clicar entrava.
    return user.filiais_permitidas().order_by('nome_fantasia', 'razao_social')


def _filiais_para_selecao(request):
    """Usa o diretorio central quando a sessao possui identidade central."""
    filiais = _filiais_permitidas(request.user)
    if request.user._state.db == 'default':
        return filiais.using('default')
    return filiais


def _usuario_central(request):
    """Resolve a identidade que pode ser referenciada pelo banco gerencial."""
    usuario = getattr(request, '_central_authenticated_user', None) or request.user
    if usuario._state.db == 'default':
        return usuario
    return (
        Usuario.objects.using('default')
        .filter(email__iexact=usuario.email, ativo=True)
        .first()
    )


@login_required
def logout_view(request):
    AuthService.logout_registro(request)
    logout(request)
    messages.info(request, 'Sessão encerrada.')
    return redirect('core:login')


@login_required
@require_POST
def atualizar_minha_foto(request):
    usuario_contextual = request.user
    user = _usuario_central(request) or usuario_contextual
    foto = request.FILES.get('foto')
    remover = request.POST.get('remover_foto') == '1'
    voltar_para = request.META.get('HTTP_REFERER') or reverse_lazy('core:dashboard')

    def sincronizar_usuario_contextual():
        banco_contextual = usuario_contextual._state.db or 'default'
        banco_central = user._state.db or 'default'
        if banco_contextual == banco_central:
            return
        usuario_contextual.foto = user.foto.name if user.foto else None
        usuario_contextual.save(
            using=banco_contextual,
            update_fields=['foto', 'updated_at'],
        )

    if remover:
        if user.foto:
            user.foto.delete(save=False)
            user.foto = None
            user.save(
                using=user._state.db or 'default',
                update_fields=['foto', 'updated_at'],
            )
            sincronizar_usuario_contextual()
            messages.success(request, 'Foto removida.')
        return redirect(voltar_para)

    if not foto:
        messages.error(request, 'Selecione uma imagem para atualizar sua foto.')
        return redirect(voltar_para)

    if foto.size > 5 * 1024 * 1024:
        messages.error(request, 'Envie uma imagem com ate 5 MB.')
        return redirect(voltar_para)

    if foto.content_type and not foto.content_type.startswith('image/'):
        messages.error(request, 'O arquivo selecionado precisa ser uma imagem.')
        return redirect(voltar_para)

    foto_antiga = user.foto.name if user.foto else ''
    user.foto = foto
    user.save(
        using=user._state.db or 'default',
        update_fields=['foto', 'updated_at'],
    )
    sincronizar_usuario_contextual()

    if foto_antiga and foto_antiga != user.foto.name:
        user.foto.storage.delete(foto_antiga)

    messages.success(request, 'Foto atualizada.')
    return redirect(voltar_para)


class SelecionarFilialView(View):
    """Tela de seleção de filial após login quando usuário tem múltiplas filiais."""

    template_name = 'core/auth/selecionar_filial.html'

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect('core:login')

        filiais = _filiais_para_selecao(request)

        if not request.user.is_superuser and filiais.count() == 1:
            filial_central = filiais.first()
            rota_inicial = nome_rota_inicial(request.user, filial_central)
            AuthService.trocar_filial(request, filial_central.pk)
            return redirect(rota_inicial)

        # Ao voltar de uma empresa, o contexto operacional ainda pode apontar
        # para o tenant anterior. A selecao pertence ao diretorio gerencial e
        # inclui todas as empresas explicitamente liberadas para a identidade.
        empresas = Empresa.objects.using('default').filter(
            filiais__in=filiais,
            ativo=True,
        ).distinct().order_by('nome_fantasia', 'razao_social')

        filiais = filiais.select_related('empresa')
        usuario_central = _usuario_central(request)

        contexto_global = {}
        if request.user.is_superuser:
            contexto_global = self._contexto_global(
                request=request,
                empresas=empresas,
                filiais=filiais,
                usuario_central=usuario_central,
            )

        favoritas_cnpjs = set()
        if usuario_central:
            # Querysets de tenant e do gerencial nao podem compor uma subquery
            # entre bancos. CNPJ e o identificador estavel da filial nos dois
            # diretorios; materializamos apenas esses valores para reconciliar.
            filiais_para_favoritas = contexto_global.get(
                'filiais_exibidas', filiais,
            )
            filiais_cnpjs = list(
                filiais_para_favoritas.values_list('cnpj', flat=True)
                if hasattr(filiais_para_favoritas, 'values_list')
                else (filial.cnpj for filial in filiais_para_favoritas)
            )
            favoritas_cnpjs = set(
                FilialFavorita.objects.using('default').filter(
                    usuario_id=usuario_central.pk,
                    filial__cnpj__in=filiais_cnpjs,
                ).values_list('filial__cnpj', flat=True)
            )

        contexto = {
            'filiais': filiais,
            'empresas': empresas,
            'is_global_selection': request.user.is_superuser,
            'show_empresa_filter': empresas.count() > 1,
            'filiais_favoritas_ids': list(
                filiais.filter(cnpj__in=favoritas_cnpjs).values_list('pk', flat=True)
            ),
        }
        contexto.update(contexto_global)
        return render(request, self.template_name, contexto)

    @staticmethod
    def _contexto_global(*, request, empresas, filiais, usuario_central):
        """Monta o master-detail global sem materializar todo o diretorio."""
        busca_empresa = request.GET.get('empresa_busca', '').strip()
        busca_filial = request.GET.get('filial_busca', '').strip()
        ordenacao = request.GET.get('ordenacao', 'nome')
        somente_favoritas = request.GET.get('favoritas') == '1'

        empresas_listagem = empresas.annotate(
            total_filiais=Count(
                'filiais',
                filter=Q(filiais__in=filiais.values('pk')),
                distinct=True,
            ),
        )
        if busca_empresa:
            empresas_listagem = empresas_listagem.filter(
                Q(nome_fantasia__icontains=busca_empresa)
                | Q(razao_social__icontains=busca_empresa)
                | Q(cnpj__icontains=busca_empresa)
            )

        empresas_page = Paginator(empresas_listagem, 8).get_page(
            request.GET.get('empresas_page', 1),
        )

        empresa_id = request.GET.get('empresa')
        if not empresa_id or not empresa_id.isdigit():
            empresa_id = None
        empresa_selecionada = empresas_listagem.filter(pk=empresa_id).first()
        if empresa_selecionada is None and empresas_page.object_list:
            empresa_selecionada = empresas_page.object_list[0]

        filiais_empresa = filiais.none()
        total_filiais_empresa = 0
        if empresa_selecionada is not None:
            filiais_empresa = filiais.filter(
                empresa_id=empresa_selecionada.pk,
            )
            total_filiais_empresa = filiais_empresa.count()

        if busca_filial:
            filiais_empresa = filiais_empresa.filter(
                Q(nome_fantasia__icontains=busca_filial)
                | Q(razao_social__icontains=busca_filial)
                | Q(cidade__icontains=busca_filial)
                | Q(uf__icontains=busca_filial)
                | Q(cnpj__icontains=busca_filial)
            )

        if somente_favoritas:
            if usuario_central is None:
                filiais_empresa = filiais_empresa.none()
            else:
                cnpjs_favoritos = FilialFavorita.objects.using('default').filter(
                    usuario_id=usuario_central.pk,
                ).values('filial__cnpj')
                filiais_empresa = filiais_empresa.filter(
                    cnpj__in=cnpjs_favoritos,
                )

        if ordenacao == 'cidade':
            filiais_empresa = filiais_empresa.order_by(
                'cidade', 'uf', 'nome_fantasia', 'razao_social',
            )
        else:
            ordenacao = 'nome'
            filiais_empresa = filiais_empresa.order_by(
                'nome_fantasia', 'razao_social',
            )

        filiais_page = Paginator(filiais_empresa, 6).get_page(
            request.GET.get('filiais_page', 1),
        )

        return {
            'busca_empresa': busca_empresa,
            'busca_filial': busca_filial,
            'empresas_page': empresas_page,
            'empresa_selecionada': empresa_selecionada,
            'empresas_total': empresas.count(),
            'filiais_page': filiais_page,
            'filiais_exibidas': filiais_page.object_list,
            'total_filiais_empresa': total_filiais_empresa,
            'ordenacao': ordenacao,
            'somente_favoritas': somente_favoritas,
        }


@login_required
@require_POST
def alternar_filial_favorita(request, filial_id):
    """Alterna uma filial acessivel, persistindo a preferencia no gerencial."""
    filial = (
        _filiais_para_selecao(request)
        .filter(pk=filial_id)
        .only('pk', 'cnpj')
        .first()
    )
    if filial is None:
        return JsonResponse(
            {'detail': 'Voce nao possui acesso a esta filial.'},
            status=403,
        )

    usuario_central = _usuario_central(request)
    filial_central = (
        Filial.objects.using('default')
        .filter(cnpj=filial.cnpj, ativo=True)
        .first()
    )
    if usuario_central is None or filial_central is None:
        return JsonResponse(
            {'detail': 'A filial nao foi localizada no diretorio central.'},
            status=409,
        )

    favorita, criada = FilialFavorita.objects.using('default').get_or_create(
        usuario_id=usuario_central.pk,
        filial_id=filial_central.pk,
    )
    if not criada:
        favorita.delete(using='default')

    return JsonResponse({'favorita': criada})


class TrocarFilialView(View):
    """Troca de filial na sessão sem reload completo (chamado via AJAX ou GET)."""

    def get(self, request, filial_id):
        if not request.user.is_authenticated:
            return redirect('core:login')
        try:
            filial = AuthService.trocar_filial(request, filial_id)
            messages.success(request, f'Filial alterada para {filial}.')
        except DomainError as e:
            messages.error(request, str(e))
            referer = request.META.get('HTTP_REFERER', '')
            if 'selecionar-filial' in referer:
                return redirect('core:selecionar-filial')
            return redirect(referer or reverse_lazy('core:selecionar-filial'))

        referer = request.META.get('HTTP_REFERER', '')
        if 'selecionar-filial' in referer:
            return redirect(nome_rota_inicial(request.user, filial))
        return redirect(referer or reverse_lazy(nome_rota_inicial(request.user, filial)))
