import calendar
from collections import defaultdict
from datetime import date, time, timedelta

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
MODOS_AGENDA = {'dia', 'semana', 'mes'}
MESES = (
    'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
    'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
)


def _mover_mes(data_referencia, deslocamento):
    indice = data_referencia.year * 12 + data_referencia.month - 1 + deslocamento
    ano, mes_zero = divmod(indice, 12)
    mes = mes_zero + 1
    return date(ano, mes, min(data_referencia.day, calendar.monthrange(ano, mes)[1]))


def _intervalo_visualizacao(data_referencia, modo):
    if modo == 'semana':
        inicio = data_referencia - timedelta(days=data_referencia.weekday())
        return inicio, inicio + timedelta(days=6)
    if modo == 'mes':
        semanas = calendar.Calendar(firstweekday=0).monthdatescalendar(
            data_referencia.year, data_referencia.month,
        )
        return semanas[0][0], semanas[-1][-1]
    return data_referencia, data_referencia


def _navegacao(data_referencia, modo):
    if modo == 'mes':
        return _mover_mes(data_referencia, -1), _mover_mes(data_referencia, 1)
    deslocamento = 7 if modo == 'semana' else 1
    return data_referencia - timedelta(days=deslocamento), data_referencia + timedelta(days=deslocamento)


def _titulo_periodo(data_referencia, inicio, fim, modo):
    if modo == 'dia':
        return data_referencia.strftime('%d/%m/%Y')
    if modo == 'semana':
        return f'{inicio:%d/%m} a {fim:%d/%m/%Y}'
    return f'{MESES[data_referencia.month - 1]} de {data_referencia.year}'


def _limites_grade(agendamentos, jornadas):
    inicios = [8]
    finais = [19]
    for jornada in jornadas:
        inicios.append(jornada.inicio.hour)
        finais.append(jornada.fim.hour + (1 if jornada.fim.minute else 0))
    for item in agendamentos:
        inicio_local = timezone.localtime(item.inicio)
        fim_local = timezone.localtime(item.fim)
        inicios.append(inicio_local.hour)
        finais.append(fim_local.hour + (1 if fim_local.minute else 0))
    return min(inicios), max(finais)


def _dados_grade(agendamentos, datas, inicio_hora):
    por_data = defaultdict(list)
    for item in agendamentos:
        inicio_local = timezone.localtime(item.inicio)
        fim_local = timezone.localtime(item.fim)
        minutos_inicio = inicio_local.hour * 60 + inicio_local.minute
        minutos_fim = fim_local.hour * 60 + fim_local.minute
        por_data[inicio_local.date()].append({
            'item': item,
            'topo': round((minutos_inicio - inicio_hora * 60) * 64 / 60, 2),
            'altura': max(30, round((minutos_fim - minutos_inicio) * 64 / 60, 2)),
        })
    return [
        {
            'data': dia,
            'hoje': dia == timezone.localdate(),
            'eventos': por_data[dia],
        }
        for dia in datas
    ]


class AgendaView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'ver'

    def get(self, request):
        try:
            data_selecionada = date.fromisoformat(request.GET.get('data', ''))
        except ValueError:
            data_selecionada = timezone.localdate()
        modo = request.GET.get('visualizacao', 'semana')
        if modo not in MODOS_AGENDA:
            modo = 'semana'
        periodo_inicio, periodo_fim = _intervalo_visualizacao(data_selecionada, modo)
        data_anterior, data_seguinte = _navegacao(data_selecionada, modo)
        profissional_id = request.GET.get('profissional')
        profissionais = ProfissionalAgenda.objects.for_filial(request.filial_ativa).filter(ativo=True).select_related('funcionario')
        agendamentos = Agendamento.objects.for_filial(request.filial_ativa).filter(
            inicio__date__range=(periodo_inicio, periodo_fim),
        ).select_related('cliente', 'profissional__funcionario').prefetch_related('itens')
        if profissional_id:
            agendamentos = agendamentos.filter(profissional_id=profissional_id)
        agendamentos = list(agendamentos)

        contexto_calendario = {}
        if modo == 'mes':
            semanas = calendar.Calendar(firstweekday=0).monthdatescalendar(
                data_selecionada.year, data_selecionada.month,
            )
            por_data = defaultdict(list)
            for item in agendamentos:
                por_data[timezone.localtime(item.inicio).date()].append(item)
            contexto_calendario['semanas_mes'] = [
                [
                    {
                        'data': dia,
                        'mes_atual': dia.month == data_selecionada.month,
                        'hoje': dia == timezone.localdate(),
                        'eventos': por_data[dia],
                    }
                    for dia in semana
                ]
                for semana in semanas
            ]
        else:
            datas = [
                periodo_inicio + timedelta(days=indice)
                for indice in range((periodo_fim - periodo_inicio).days + 1)
            ]
            jornadas = JornadaTrabalho.objects.for_filial(request.filial_ativa).filter(
                ativo=True,
            )
            if profissional_id:
                jornadas = jornadas.filter(profissional_id=profissional_id)
            inicio_hora, fim_hora = _limites_grade(agendamentos, jornadas)
            horas = list(range(inicio_hora, fim_hora + 1))
            contexto_calendario.update({
                'dias_grade': _dados_grade(agendamentos, datas, inicio_hora),
                'horas_grade': [
                    {'rotulo': f'{hora:02d}:00', 'topo': (hora - inicio_hora) * 64}
                    for hora in horas
                ],
                'altura_grade': (fim_hora - inicio_hora) * 64,
                'quantidade_dias': len(datas),
            })
        return render(request, 'agenda/agenda.html', {
            'data_selecionada': data_selecionada,
            'data_anterior': data_anterior,
            'data_seguinte': data_seguinte,
            'visualizacao': modo,
            'titulo_periodo': _titulo_periodo(data_selecionada, periodo_inicio, periodo_fim, modo),
            'profissionais': profissionais,
            'profissional_id': profissional_id or '',
            'agendamentos': agendamentos,
            **contexto_calendario,
        })


class AgendamentoCreateView(PermissaoRequiredMixin, View):
    permissao_modulo = 'cadastros'
    permissao_acao = 'criar'

    def get(self, request):
        form = AgendamentoForm(filial=request.filial_ativa, initial={'inicio': request.GET.get('inicio')})
        return render(request, 'agenda/agendamento_form.html', {'form': form})

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

    def contexto(self, form, profissional, dados=None):
        jornadas = {item.dia_semana: item for item in profissional.jornadas.all()} if profissional.pk else {}
        dias = []
        for numero, nome in DIAS:
            jornada = jornadas.get(numero)
            if dados is not None:
                ativo = dados.get(f'dia_{numero}_ativo') == 'on'
                inicio = dados.get(f'dia_{numero}_inicio', '')
                fim = dados.get(f'dia_{numero}_fim', '')
                pausa_inicio = dados.get(f'dia_{numero}_pausa_inicio', '')
                pausa_fim = dados.get(f'dia_{numero}_pausa_fim', '')
            else:
                ativo = bool(jornada)
                inicio = jornada.inicio.strftime('%H:%M') if jornada else ''
                fim = jornada.fim.strftime('%H:%M') if jornada else ''
                pausa_inicio = jornada.pausa_inicio.strftime('%H:%M') if jornada and jornada.pausa_inicio else ''
                pausa_fim = jornada.pausa_fim.strftime('%H:%M') if jornada and jornada.pausa_fim else ''
            dias.append({
                'numero': numero, 'nome': nome, 'ativo': ativo, 'inicio': inicio, 'fim': fim,
                'pausa_inicio': pausa_inicio, 'pausa_fim': pausa_fim,
                'tem_pausa': bool(pausa_inicio or pausa_fim),
            })
        return {'form': form, 'profissional': profissional if profissional.pk else None, 'dias': dias}

    def get(self, request, pk=None):
        profissional = self.objeto(request, pk)
        form = ProfissionalAgendaForm(instance=profissional, filial=request.filial_ativa)
        return render(request, 'agenda/profissional_form.html', self.contexto(form, profissional))

    def _jornadas_enviadas(self, request, form):
        jornadas = []
        marcou_dia = False
        for numero, nome in DIAS:
            if request.POST.get(f'dia_{numero}_ativo') != 'on':
                continue
            marcou_dia = True
            valores = {
                campo: request.POST.get(f'dia_{numero}_{campo}') or None
                for campo in ('inicio', 'fim', 'pausa_inicio', 'pausa_fim')
            }
            if not valores['inicio'] or not valores['fim']:
                form.add_error(None, f'Informe o início e o fim de {nome}.')
                continue
            try:
                valores = {chave: time.fromisoformat(valor) if valor else None for chave, valor in valores.items()}
            except ValueError:
                form.add_error(None, f'Existe um horário inválido em {nome}.')
                continue
            if valores['inicio'] >= valores['fim']:
                form.add_error(None, f'Em {nome}, o fim deve ser posterior ao início.')
                continue
            if bool(valores['pausa_inicio']) != bool(valores['pausa_fim']):
                form.add_error(None, f'Informe o início e o fim do intervalo de {nome}.')
                continue
            if valores['pausa_inicio'] and not (
                valores['inicio'] < valores['pausa_inicio'] < valores['pausa_fim'] < valores['fim']
            ):
                form.add_error(None, f'Em {nome}, o intervalo precisa estar dentro do expediente.')
                continue
            jornadas.append((numero, valores))
        if not marcou_dia:
            form.add_error(None, 'Marque pelo menos um dia de atendimento.')
        return jornadas

    def post(self, request, pk=None):
        profissional = self.objeto(request, pk)
        form = ProfissionalAgendaForm(request.POST, instance=profissional, filial=request.filial_ativa)
        jornadas = self._jornadas_enviadas(request, form)
        if form.is_valid() and not form.non_field_errors():
            with transaction.atomic():
                profissional = form.save(commit=False)
                profissional.filial = request.filial_ativa
                profissional.save()
                selecionados = set(form.cleaned_data['servicos'].values_list('pk', flat=True))
                ProfissionalServico.objects.filter(profissional=profissional).exclude(
                    servico_id__in=selecionados,
                ).update(ativo=False)
                for servico in form.cleaned_data['servicos']:
                    ProfissionalServico.objects.update_or_create(
                        profissional=profissional, servico=servico, defaults={'ativo': True},
                    )
                dias_ativos = {numero for numero, _ in jornadas}
                JornadaTrabalho.objects.filter(profissional=profissional).exclude(
                    dia_semana__in=dias_ativos,
                ).delete()
                for numero, valores in jornadas:
                    JornadaTrabalho.objects.update_or_create(
                        profissional=profissional, dia_semana=numero,
                        defaults={'filial': request.filial_ativa, 'ativo': True, **valores},
                    )
            messages.success(request, 'Configuração do profissional salva.')
            return redirect('agenda:profissional-list')
        return render(
            request, 'agenda/profissional_form.html',
            self.contexto(form, profissional, request.POST),
        )


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
