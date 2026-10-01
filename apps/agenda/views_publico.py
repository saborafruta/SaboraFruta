import re
from datetime import date, datetime, timedelta

from django.core.exceptions import ValidationError
from django.db.models import Prefetch, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views import View

from apps.cadastros.models import Cliente
from apps.core.models import EmpresaBanco, Filial
from apps.core.tenant_context import get_current_database_alias, tenant_atomic
from apps.whatsapp_agent.notifications import enviar_notificacao_agendamento

from .models import AgendaLinkPublico, Agendamento, ProfissionalAgenda, ProfissionalServico
from .services import criar_agendamento, listar_horarios


def _link_do_token(token):
    queryset = AgendaLinkPublico.objects.select_related('filial', 'filial__empresa')
    link = queryset.filter(
        token=token,
        ativo=True,
        filial__ativo=True,
    ).first()
    if link:
        return link

    # Compatibilidade com os primeiros links enviados pelo agente: o token
    # foi salvo no gerencial, mas a agenda vive no banco operacional. Quando
    # a filial já possui seu link definitivo, o token antigo funciona como um
    # alias sem alterar ou invalidar o endereço atual.
    alias = get_current_database_alias()
    if alias != 'default':
        legacy = (
            AgendaLinkPublico.objects.using('default')
            .select_related('filial')
            .filter(token=token, ativo=True)
            .first()
        )
        if legacy and EmpresaBanco.objects.using('default').filter(
            db_alias=alias,
            empresa_id=legacy.filial.empresa_id,
            ativo=True,
            status=EmpresaBanco.Status.ATIVO,
        ).exists():
            filial = Filial.objects.using(alias).filter(
                cnpj=legacy.filial.cnpj,
                ativo=True,
            ).first()
            if filial:
                link = queryset.filter(
                    filial_id=filial.pk,
                    ativo=True,
                    filial__ativo=True,
                ).first()
                if link:
                    return link
    raise Http404


def _profissionais_publicos(filial):
    vinculos = ProfissionalServico.objects.filter(
        ativo=True,
        servico__ativo=True,
        servico__agendavel=True,
        servico__filiais_vinculo__filial=filial,
        servico__filiais_vinculo__ativo=True,
    ).select_related('servico').order_by('servico__descricao')
    return list(
        ProfissionalAgenda.objects.filter(
            filial=filial,
            ativo=True,
            servicos_vinculados__ativo=True,
            servicos_vinculados__servico__ativo=True,
            servicos_vinculados__servico__agendavel=True,
            servicos_vinculados__servico__filiais_vinculo__filial=filial,
            servicos_vinculados__servico__filiais_vinculo__ativo=True,
        )
        .select_related('funcionario')
        .prefetch_related(Prefetch('servicos_vinculados', queryset=vinculos, to_attr='servicos_publicos'))
        .distinct()
        .order_by('funcionario__nome')
    )


def _catalogo(profissionais):
    return [
        {
            'id': profissional.pk,
            'nome': profissional.funcionario.nome,
            'servicos': [
                {
                    'id': vinculo.servico_id,
                    'nome': vinculo.servico.descricao,
                    'duracao': vinculo.servico.duracao_servico_minutos,
                    'preco': f'{vinculo.servico.preco_venda:.2f}',
                }
                for vinculo in profissional.servicos_publicos
            ],
        }
        for profissional in profissionais
    ]


def _vinculo(filial, profissional_id, servico_id):
    return get_object_or_404(
        ProfissionalServico.objects.select_related('profissional__filial', 'servico'),
        profissional_id=profissional_id,
        profissional__filial=filial,
        profissional__ativo=True,
        servico_id=servico_id,
        servico__ativo=True,
        servico__agendavel=True,
        servico__filiais_vinculo__filial=filial,
        servico__filiais_vinculo__ativo=True,
        ativo=True,
    )


def _somente_digitos(valor):
    return re.sub(r'\D', '', valor or '')


def _localizar_cliente(filial, telefone):
    sufixo = _somente_digitos(telefone)[-8:]
    if len(sufixo) < 8:
        return None
    candidatos = Cliente.objects.for_filial(filial).filter(ativo=True).filter(
        Q(celular__icontains=sufixo) | Q(telefone__icontains=sufixo),
    )[:20]
    return next(
        (
            cliente for cliente in candidatos
            if _somente_digitos(cliente.celular or cliente.telefone).endswith(sufixo)
        ),
        None,
    )


class AgendaPublicaView(View):
    template_name = 'agenda/publico/agendar.html'

    def _contexto(self, link, *, dados=None, erros=None, agendamento=None):
        profissionais = _profissionais_publicos(link.filial)
        return {
            'link': link,
            'filial': link.filial,
            'profissionais': profissionais,
            'catalogo': _catalogo(profissionais),
            'data_minima': timezone.localdate().isoformat(),
            'data_maxima': (timezone.localdate() + timedelta(days=365)).isoformat(),
            'dados': dados or {},
            'erros': erros or [],
            'agendamento': agendamento,
        }

    def get(self, request, token):
        link = _link_do_token(token)
        return render(request, self.template_name, self._contexto(link))

    def post(self, request, token):
        link = _link_do_token(token)
        dados = {
            chave: (request.POST.get(chave) or '').strip()
            for chave in ('profissional', 'servico', 'data', 'horario', 'nome', 'telefone')
        }
        erros = []
        nome = dados['nome'][:150]
        telefone = _somente_digitos(dados['telefone'])
        if len(nome) < 2:
            erros.append('Informe o nome do cliente.')
        if not 10 <= len(telefone) <= 15:
            erros.append('Informe um WhatsApp válido com DDD.')

        vinculo = None
        try:
            vinculo = _vinculo(link.filial, dados['profissional'], dados['servico'])
        except (TypeError, ValueError):
            erros.append('Selecione o profissional e o serviço novamente.')

        inicio = None
        try:
            data_escolhida = date.fromisoformat(dados['data'])
            hora_escolhida = datetime.strptime(dados['horario'], '%H:%M').time()
            if not timezone.localdate() <= data_escolhida <= timezone.localdate() + timedelta(days=365):
                raise ValueError
            inicio = timezone.make_aware(
                datetime.combine(data_escolhida, hora_escolhida),
                timezone.get_current_timezone(),
            )
        except ValueError:
            erros.append('Escolha uma data e um horário disponíveis.')

        if erros:
            return render(request, self.template_name, self._contexto(link, dados=dados, erros=erros), status=400)

        try:
            with tenant_atomic():
                cliente = _localizar_cliente(link.filial, telefone)
                if cliente:
                    if cliente.nome_display.casefold() != nome.casefold():
                        cliente.razao_social = nome
                        cliente.save(update_fields=['razao_social', 'updated_at'])
                else:
                    cliente = Cliente.objects.create(
                        filial=link.filial,
                        tipo_pessoa='F',
                        razao_social=nome,
                        celular=telefone,
                        consumidor_final=True,
                    )
                agendamento = criar_agendamento(
                    filial=link.filial,
                    profissional=vinculo.profissional,
                    servicos=[vinculo.servico],
                    inicio=inicio,
                    pessoa_atendida_nome=nome,
                    cliente=cliente,
                    telefone=telefone,
                    origem=Agendamento.Origem.LINK,
                )
        except ValidationError as exc:
            erros.extend(exc.messages)
            return render(request, self.template_name, self._contexto(link, dados=dados, erros=erros), status=409)

        enviar_notificacao_agendamento(
            agendamento,
            db_alias=get_current_database_alias(),
        )
        return render(request, self.template_name, self._contexto(link, agendamento=agendamento))


class HorariosPublicosView(View):
    def get(self, request, token):
        link = _link_do_token(token)
        try:
            vinculo = _vinculo(
                link.filial,
                request.GET.get('profissional'),
                request.GET.get('servico'),
            )
            data_escolhida = date.fromisoformat(request.GET.get('data', ''))
            hoje = timezone.localdate()
            if not hoje <= data_escolhida <= hoje + timedelta(days=365):
                raise ValueError
            horarios = listar_horarios(vinculo.profissional, [vinculo.servico], data_escolhida)
        except (TypeError, ValueError, ValidationError):
            return JsonResponse({'erro': 'Seleção inválida.'}, status=400)
        return JsonResponse({
            'horarios': [
                {
                    'valor': timezone.localtime(item).strftime('%H:%M'),
                    'rotulo': timezone.localtime(item).strftime('%H:%M'),
                }
                for item in horarios
            ],
        })
