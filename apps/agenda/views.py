from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator

from apps.core.models import LogSistema
from apps.core.services.permissions import PermissaoRequiredMixin

from .forms import AgendamentoForm, BloqueioAgendaForm, ProfissionalAgendaForm
from .models import Agendamento, BloqueioAgenda, JornadaTrabalho, ProfissionalAgenda, ProfissionalServico
from .services import criar_agendamento, listar_horarios


DIAS = list(JornadaTrabalho.DiaSemana.choices)


class AgendaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        try:
            data_selecionada = date.fromisoformat(request.GET.get('data', ''))
        except ValueError:
            data_selecionada = timezone.localdate()
        profissional_id = request.GET.get('profissional')
        profissionais = ProfissionalAgenda.objects.for_filial(request.filial_ativa).filter(ativo=True).select_related('funcionario')
        agendamentos = Agendamento.objects.for_filial(request.filial_ativa).filter(
            inicio__date=data_selecionada,
        ).select_related('cliente', 'profissional__funcionario').prefetch_related('itens')
        if profissional_id:
            agendamentos = agendamentos.filter(profissional_id=profissional_id)
        return render(request, 'agenda/agenda.html', {
            'data_selecionada': data_selecionada,
            'data_anterior': data_selecionada - timedelta(days=1),
            'data_seguinte': data_selecionada + timedelta(days=1),
            'profissionais': profissionais,
            'profissional_id': profissional_id or '',
            'agendamentos': agendamentos,
        })


class AgendamentoCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'criar'

    def get(self, request):
        form = AgendamentoForm(filial=request.filial_ativa, initial={'inicio': request.GET.get('inicio')})
        return render(request, 'agenda/agendamento_form.html', {'form': form})


class AgendamentoDetailView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request, pk):
        agendamento = get_object_or_404(
            Agendamento.objects.for_filial(request.filial_ativa)
            .select_related('cliente', 'profissional__funcionario')
            .prefetch_related('itens'),
            pk=pk,
        )
        return render(request, 'agenda/agendamento_detail.html', {
            'agendamento': agendamento,
            'status_opcoes': Agendamento.Status.choices,
        })


@method_decorator(require_POST, name='dispatch')
class AgendamentoStatusView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request, pk):
        agendamento = get_object_or_404(
            Agendamento.objects.for_filial(request.filial_ativa), pk=pk,
        )
        status = request.POST.get('status')
        if status not in Agendamento.Status.values:
            messages.error(request, 'Status inválido.')
        else:
            anterior = agendamento.status
            agendamento.status = status
            agendamento.save(update_fields=['status', 'updated_at'])
            LogSistema.objects.create(
                filial=request.filial_ativa, usuario=request.user, modulo='agenda',
                acao=LogSistema.Acao.EDITAR, tabela_afetada='agenda_agendamentos',
                registro_id=agendamento.pk,
                dados_anteriores={'status': anterior}, dados_novos={'status': status},
            )
            messages.success(request, 'Status do agendamento atualizado.')
        return redirect('agenda:agendamento-detail', pk=pk)

    def post(self, request):
        form = AgendamentoForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            try:
                agendamento = criar_agendamento(
                    filial=request.filial_ativa, profissional=form.cleaned_data['profissional'],
                    servicos=form.cleaned_data['servicos'], inicio=form.cleaned_data['inicio'],
                    pessoa_atendida_nome=form.cleaned_data['pessoa_atendida_nome'],
                    cliente=form.cleaned_data.get('cliente'), telefone=form.cleaned_data.get('telefone_contato', ''),
                    observacao=form.cleaned_data.get('observacao', ''), usuario=request.user,
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                LogSistema.objects.create(
                    filial=request.filial_ativa, usuario=request.user, modulo='agenda',
                    acao=LogSistema.Acao.CRIAR, tabela_afetada='agenda_agendamentos',
                    registro_id=agendamento.pk, dados_novos={'cliente': agendamento.pessoa_atendida_nome, 'inicio': agendamento.inicio.isoformat()},
                )
                messages.success(request, 'Agendamento criado com sucesso.')
                return redirect('agenda:agenda')
        return render(request, 'agenda/agendamento_form.html', {'form': form})


class ProfissionalListView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        profissionais = ProfissionalAgenda.objects.for_filial(request.filial_ativa).select_related('funcionario').prefetch_related('servicos_vinculados__servico')
        return render(request, 'agenda/profissional_list.html', {'profissionais': profissionais})


class ProfissionalConfigView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def objeto(self, request, pk):
        if not pk:
            return ProfissionalAgenda(filial=request.filial_ativa)
        return get_object_or_404(ProfissionalAgenda.objects.for_filial(request.filial_ativa), pk=pk)

    def contexto(self, form, profissional):
        jornadas = {item.dia_semana: item for item in profissional.jornadas.all()} if profissional.pk else {}
        return {'form': form, 'profissional': profissional if profissional.pk else None, 'dias': [(numero, nome, jornadas.get(numero)) for numero, nome in DIAS]}

    def get(self, request, pk=None):
        profissional = self.objeto(request, pk)
        form = ProfissionalAgendaForm(instance=profissional, filial=request.filial_ativa)
        return render(request, 'agenda/profissional_form.html', self.contexto(form, profissional))


class BloqueioListCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def contexto(self, request, form):
        bloqueios = (
            BloqueioAgenda.objects.for_filial(request.filial_ativa)
            .filter(ativo=True, fim__gte=timezone.now())
            .select_related('profissional__funcionario')
        )
        return {'form': form, 'bloqueios': bloqueios}

    def get(self, request):
        form = BloqueioAgendaForm(filial=request.filial_ativa)
        return render(request, 'agenda/bloqueio_list.html', self.contexto(request, form))

    def post(self, request):
        form = BloqueioAgendaForm(request.POST, filial=request.filial_ativa)
        if form.is_valid():
            bloqueio = form.save(commit=False)
            bloqueio.filial = request.filial_ativa
            bloqueio.save()
            messages.success(request, 'Bloqueio criado com sucesso.')
            return redirect('agenda:bloqueio-list')
        return render(request, 'agenda/bloqueio_list.html', self.contexto(request, form))


@method_decorator(require_POST, name='dispatch')
class BloqueioDeleteView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'editar'

    def post(self, request, pk):
        bloqueio = get_object_or_404(
            BloqueioAgenda.objects.for_filial(request.filial_ativa), pk=pk,
        )
        bloqueio.ativo = False
        bloqueio.save(update_fields=['ativo', 'updated_at'])
        messages.success(request, 'Bloqueio removido.')
        return redirect('agenda:bloqueio-list')

    def post(self, request, pk=None):
        profissional = self.objeto(request, pk)
        form = ProfissionalAgendaForm(request.POST, instance=profissional, filial=request.filial_ativa)
        if form.is_valid():
            with transaction.atomic():
                profissional = form.save(commit=False)
                profissional.filial = request.filial_ativa
                profissional.save()
                selecionados = set(form.cleaned_data['servicos'].values_list('pk', flat=True))
                ProfissionalServico.objects.filter(profissional=profissional).exclude(servico_id__in=selecionados).update(ativo=False)
                for servico in form.cleaned_data['servicos']:
                    ProfissionalServico.objects.update_or_create(profissional=profissional, servico=servico, defaults={'ativo': True})
                for numero, _ in DIAS:
                    ativo = request.POST.get(f'dia_{numero}_ativo') == 'on'
                    if not ativo:
                        JornadaTrabalho.objects.filter(profissional=profissional, dia_semana=numero).delete()
                        continue
                    valores = {campo: request.POST.get(f'dia_{numero}_{campo}') or None for campo in ('inicio', 'fim', 'pausa_inicio', 'pausa_fim')}
                    if not valores['inicio'] or not valores['fim']:
                        form.add_error(None, f'Informe início e fim para {JornadaTrabalho.DiaSemana(numero).label}.')
                        transaction.set_rollback(True)
                        break
                    jornada, _ = JornadaTrabalho.objects.update_or_create(
                        profissional=profissional, dia_semana=numero,
                        defaults={'filial': request.filial_ativa, 'ativo': True, **valores},
                    )
                    try:
                        jornada.full_clean()
                    except ValidationError as exc:
                        form.add_error(None, exc)
                        transaction.set_rollback(True)
                        break
            if not form.errors:
                messages.success(request, 'Configuração do profissional salva.')
                return redirect('agenda:profissional-list')
        return render(request, 'agenda/profissional_form.html', self.contexto(form, profissional))


class DisponibilidadeApiView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        profissional = get_object_or_404(ProfissionalAgenda.objects.for_filial(request.filial_ativa), pk=request.GET.get('profissional'))
        servico_ids = request.GET.getlist('servico')
        servicos = profissional.servicos.filter(pk__in=servico_ids, agendavel=True)
        try:
            data = date.fromisoformat(request.GET.get('data', ''))
            horarios = listar_horarios(profissional, servicos, data)
        except (ValueError, ValidationError) as exc:
            return JsonResponse({'erro': str(exc)}, status=400)
        return JsonResponse({'horarios': [timezone.localtime(item).strftime('%Y-%m-%dT%H:%M') for item in horarios]})
