"""Views de autenticação: login, logout, seleção e troca de filial."""
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.decorators.http import require_POST

from apps.core.forms import LoginForm
from apps.core.models import Empresa, Filial, FilialFavorita, Usuario
from apps.core.services.auth_service import AuthService
from apps.core.services.exceptions import DomainError


class LoginView(View):
    template_name = 'core/auth/login.html'

    def get(self, request):
        if request.user.is_authenticated:
            return redirect('core:dashboard')
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
        operational_user = getattr(request, '_tenant_authenticated_user', user)
        filiais = _filiais_permitidas(operational_user)
        if user.is_superuser:
            request.session.pop('filial_ativa_id', None)
            return redirect('core:selecionar-filial')
        if filiais.count() == 1:
            request.session['filial_ativa_id'] = filiais.first().pk
            return redirect('core:dashboard')
        return redirect('core:selecionar-filial')


def _filiais_permitidas(user):
    # A regra mora no usuario -- ver `Usuario.filiais_permitidas`. Quando ela
    # vivia aqui tambem, a tela de escolha oferecia unidade que o vinculo do
    # login nao dava, e clicar entrava.
    return user.filiais_permitidas().order_by('nome_fantasia', 'razao_social')


def _filiais_para_selecao(request):
    """Usa o diretorio central para o Super Admin e o tenant para operadores."""
    filiais = _filiais_permitidas(request.user)
    if request.user.is_superuser:
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
    user = request.user
    foto = request.FILES.get('foto')
    remover = request.POST.get('remover_foto') == '1'
    voltar_para = request.META.get('HTTP_REFERER') or reverse_lazy('core:dashboard')

    if remover:
        if user.foto:
            user.foto.delete(save=False)
            user.foto = None
            user.save(
                using=user._state.db or 'default',
                update_fields=['foto', 'updated_at'],
            )
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
            request.session['filial_ativa_id'] = filiais.first().pk
            return redirect('core:dashboard')

        empresas = []
        if request.user.is_superuser:
            # Ao voltar de uma empresa, o contexto operacional ainda aponta
            # para o banco daquele tenant. A seleção global do Super Admin,
            # porém, pertence ao diretório gerencial e deve listar todas as
            # empresas e filiais contratadas.
            empresas = Empresa.objects.using('default').filter(
                filiais__in=filiais,
                ativo=True,
            ).distinct().order_by('nome_fantasia', 'razao_social')

        filiais = filiais.select_related('empresa')
        usuario_central = _usuario_central(request)
        favoritas_cnpjs = set()
        if usuario_central:
            # Querysets de tenant e do gerencial nao podem compor uma subquery
            # entre bancos. CNPJ e o identificador estavel da filial nos dois
            # diretorios; materializamos apenas esses valores para reconciliar.
            filiais_cnpjs = list(filiais.values_list('cnpj', flat=True))
            favoritas_cnpjs = set(
                FilialFavorita.objects.using('default').filter(
                    usuario_id=usuario_central.pk,
                    filial__cnpj__in=filiais_cnpjs,
                ).values_list('filial__cnpj', flat=True)
            )

        return render(request, self.template_name, {
            'filiais': filiais,
            'empresas': empresas,
            'is_global_selection': request.user.is_superuser,
            'filiais_favoritas_ids': list(
                filiais.filter(cnpj__in=favoritas_cnpjs).values_list('pk', flat=True)
            ),
        })


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
            return redirect('core:dashboard')
        return redirect(referer or reverse_lazy('core:dashboard'))
