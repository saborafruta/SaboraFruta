"""Tela CRM → Alertas de Recompra."""
from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from apps.core.models import Filial
from apps.core.services.permissions import PermissaoRequiredMixin
from apps.crm.models import RecompraCliente
from apps.crm.services import RecompraService


class AlertasRecompraView(PermissaoRequiredMixin, View):
    permissao_modulo = 'crm'
    permissao_acao = 'ver'
    template_name = 'crm/alertas_recompra.html'

    def get(self, request):
        filial = request.filial_ativa

        # Revalida em lote se os dados estiverem velhos. É barato no caso
        # comum (só lê a linha de controle) e mantém a tela correta mesmo
        # sem um agendador rodando.
        RecompraService.recalcular_se_obsoleto(filial)

        filiais_escopo = (
            Filial.objects.filter(empresa=filial.empresa)
            if filial.is_matriz
            else Filial.objects.filter(pk=filial.pk)
        )

        # Cliente inativo fica fora: ele nao aparece na tela de Clientes nem
        # no mapa, entao listar aqui produzia alerta para quem foi desativado.
        qs = (
            RecompraCliente.objects
            .filter(filial__in=filiais_escopo, cliente__ativo=True)
            .select_related('cliente', 'representante', 'filial')
        )

        # --- Filtros ---
        f = {
            'representante': request.GET.get('representante', '').strip(),
            'cidade': request.GET.get('cidade', '').strip(),
            'uf': request.GET.get('uf', '').strip(),
            'grupo': request.GET.get('grupo', '').strip(),
            'filial': request.GET.get('filial', '').strip(),
            'frequencia': request.GET.get('frequencia', '').strip(),
            'status': request.GET.get('status', '').strip(),
            'q': request.GET.get('q', '').strip(),
        }
        if f['representante']:
            qs = qs.filter(representante_id=f['representante'])
        if f['cidade']:
            qs = qs.filter(cliente__cidade__iexact=f['cidade'])
        if f['uf']:
            qs = qs.filter(cliente__uf=f['uf'])
        if f['grupo']:
            qs = qs.filter(cliente__grupo_desconto=f['grupo'])
        if f['filial']:
            qs = qs.filter(filial_id=f['filial'])
        if f['frequencia']:
            qs = qs.filter(frequencia=f['frequencia'])
        if f['status']:
            qs = qs.filter(status=f['status'])
        if f['q']:
            qs = qs.filter(
                Q(cliente__razao_social__icontains=f['q'])
                | Q(cliente__nome_fantasia__icontains=f['q'])
                | Q(cliente__cpf_cnpj__icontains=f['q'])
            )

        # --- KPIs (sobre o conjunto filtrado) ---
        F = RecompraCliente.Frequencia
        S = RecompraCliente.Status
        agg = qs.aggregate(
            semanais=Count('id', filter=Q(frequencia=F.SEMANAL)),
            quinzenais=Count('id', filter=Q(frequencia=F.QUINZENAL)),
            mensais=Count('id', filter=Q(frequencia=F.MENSAL)),
            personalizados=Count('id', filter=Q(frequencia=F.PERSONALIZADA)),
            sem_padrao=Count('id', filter=Q(frequencia=F.SEM_PADRAO)),
            em_atraso=Count('id', filter=Q(status=S.VERMELHO)),
            proximos=Count('id', filter=Q(status=S.AMARELO)),
            # Quanto de faturamento está "parado" nos clientes atrasados,
            # estimado pelo ticket médio de cada um.
            valor_potencial=Sum('valor_medio', filter=Q(status=S.VERMELHO)),
        )
        kpis = {k: (v or 0) for k, v in agg.items()}
        kpis['valor_potencial'] = agg['valor_potencial'] or Decimal('0')

        # A ordenação por score já entrega a prioridade pedida: quem está
        # mais atrasado em relação ao próprio ritmo sobe, e entre iguais
        # vence quem compra mais alto e com mais regularidade.
        qs = qs.order_by('-score', 'dias_restantes')

        faixas, faixas_personalizadas = self._montar_faixas(filial, qs)

        paginator = Paginator(qs, 50)
        page_obj = paginator.get_page(request.GET.get('page', 1))

        querystring = request.GET.copy()
        querystring.pop('page', None)

        # --- Opções dos filtros ---
        from apps.cadastros.models import Cliente, Representante

        clientes_no_escopo = Cliente.objects.filter(
            recompras__filial__in=filiais_escopo,
        ).distinct()

        return render(request, self.template_name, {
            'title': 'Alertas de Recompra',
            'page_obj': page_obj,
            'kpis': kpis,
            'faixas': faixas,
            'faixas_personalizadas': faixas_personalizadas,
            'filtros': f,
            'total_resultados': paginator.count,
            'querystring': querystring.urlencode(),
            'representantes': Representante.objects.filter(
                filial__in=filiais_escopo, ativo=True,
            ).distinct().order_by('nome'),
            'cidades': clientes_no_escopo.exclude(cidade='').values_list(
                'cidade', flat=True,
            ).distinct().order_by('cidade'),
            'ufs': clientes_no_escopo.exclude(uf='').values_list(
                'uf', flat=True,
            ).distinct().order_by('uf'),
            'grupos': clientes_no_escopo.exclude(grupo_desconto='').values_list(
                'grupo_desconto', flat=True,
            ).distinct().order_by('grupo_desconto'),
            'filiais': filiais_escopo.order_by('nome_fantasia'),
            'frequencias': RecompraCliente.Frequencia.choices,
            'status_choices': RecompraCliente.Status.choices,
            'hoje': timezone.localdate(),
        })


    @staticmethod
    def _montar_faixas(filial, qs):
        """
        Distribui os clientes nos 7 cards de padrão de recompra.

        Cada card tem um alvo em dias (7, 14, 21, 30 + os três que o usuário
        configura) e o cliente cai no card cujo alvo está mais próximo da sua
        média de intervalo — assim os grupos se reajustam sozinhos quando os
        valores personalizados mudam, sem faixas fixas que deixariam buracos.
        Clientes sem padrão suficiente ficam de fora dos cards (aparecem só
        na tabela abaixo).
        """
        from apps.crm import constants as c
        from apps.crm.models import ConfiguracaoFaixasRecompra, RecompraCliente

        config, _ = ConfiguracaoFaixasRecompra.objects.get_or_create(filial=filial)
        personalizadas = config.personalizadas
        alvos = c.FAIXAS_CARD_FIXAS + personalizadas

        faixas = [
            {
                'indice': i,
                'dias': alvo,
                'editavel': i >= len(c.FAIXAS_CARD_FIXAS),
                'clientes': [],
                'total': 0,
                'em_atraso': 0,
                'valor_medio_total': Decimal('0'),
            }
            for i, alvo in enumerate(alvos)
        ]

        com_padrao = qs.exclude(frequencia=RecompraCliente.Frequencia.SEM_PADRAO)
        for r in com_padrao:
            media = float(r.media_intervalo_dias or 0)
            if media <= 0:
                continue
            # Índice do alvo mais próximo da média do cliente.
            idx = min(range(len(alvos)), key=lambda i: abs(alvos[i] - media))
            faixa = faixas[idx]
            faixa['total'] += 1
            faixa['valor_medio_total'] += r.valor_medio or Decimal('0')
            if r.status == RecompraCliente.Status.VERMELHO:
                faixa['em_atraso'] += 1
            # A lista do modal mostra os mais prioritários primeiro; o qs já
            # vem ordenado por score.
            if len(faixa['clientes']) < 100:
                faixa['clientes'].append(r)

        return faixas, personalizadas


class RecompraFaixasSalvarView(PermissaoRequiredMixin, View):
    """Salva os dias dos três cards personalizáveis (editados no próprio card)."""

    permissao_modulo = 'crm'
    permissao_acao = 'ver'

    def post(self, request):
        from apps.crm.models import ConfiguracaoFaixasRecompra

        config, _ = ConfiguracaoFaixasRecompra.objects.get_or_create(filial=request.filial_ativa)
        campos = {'5': 'faixa_5_dias', '6': 'faixa_6_dias', '7': 'faixa_7_dias'}

        alterados = []
        for chave, campo in campos.items():
            bruto = request.POST.get(f'faixa_{chave}', '').strip()
            if not bruto:
                continue
            try:
                dias = int(bruto)
            except ValueError:
                messages.error(request, 'Informe a quantidade de dias em números inteiros.')
                return redirect(reverse('crm:recompra'))
            if dias < 1 or dias > 3650:
                messages.error(request, 'A quantidade de dias deve ficar entre 1 e 3650.')
                return redirect(reverse('crm:recompra'))
            setattr(config, campo, dias)
            alterados.append(campo)

        if alterados:
            config.save(update_fields=alterados + ['updated_at'])
            messages.success(request, 'Faixas de recompra atualizadas.')
        return redirect(reverse('crm:recompra'))


class RecompraBuscarClienteView(PermissaoRequiredMixin, View):
    """Busca AJAX de clientes para a inclusão manual no padrão de recompra."""

    permissao_modulo = 'crm'
    permissao_acao = 'ver'

    def get(self, request):
        from django.http import JsonResponse

        from apps.cadastros.models import Cliente

        q = request.GET.get('q', '').strip()
        if len(q) < 2:
            return JsonResponse({'resultados': []})

        clientes = (
            Cliente.objects.for_filial(request.filial_ativa)
            .filter(
                Q(razao_social__icontains=q)
                | Q(nome_fantasia__icontains=q)
                | Q(cpf_cnpj__icontains=q),
                ativo=True,
            )[:15]
        )
        return JsonResponse({'resultados': [
            {
                'id': cl.pk,
                'nome': cl.nome_display,
                'cpf_cnpj': cl.cpf_cnpj or '',
            }
            for cl in clientes
        ]})


class RecompraDefinirManualView(PermissaoRequiredMixin, View):
    """Inclui/atualiza à mão o padrão de recompra de um cliente."""

    permissao_modulo = 'crm'
    permissao_acao = 'editar'

    def post(self, request):
        from apps.cadastros.models import Cliente
        from apps.core.services.exceptions import DomainError

        cliente_id = request.POST.get('cliente_id', '').strip()
        intervalo = request.POST.get('intervalo_dias', '').strip()

        if not cliente_id:
            messages.error(request, 'Selecione um cliente.')
            return redirect(reverse('crm:recompra'))
        try:
            intervalo_dias = int(intervalo)
        except ValueError:
            messages.error(request, 'Informe o intervalo em dias (número inteiro).')
            return redirect(reverse('crm:recompra'))

        cliente = Cliente.objects.for_filial(request.filial_ativa).filter(pk=cliente_id).first()
        if not cliente:
            messages.error(request, 'Cliente não encontrado nesta filial.')
            return redirect(reverse('crm:recompra'))

        try:
            RecompraService.definir_manual(
                filial=request.filial_ativa, cliente=cliente, intervalo_dias=intervalo_dias,
            )
        except DomainError as exc:
            messages.error(request, str(exc))
            return redirect(reverse('crm:recompra'))

        messages.success(
            request, f'{cliente.nome_display} definido como compra a cada {intervalo_dias} dias.',
        )
        return redirect(reverse('crm:recompra'))


class RecompraRemoverManualView(PermissaoRequiredMixin, View):
    """Devolve o cliente ao cálculo automático."""

    permissao_modulo = 'crm'
    permissao_acao = 'editar'

    def post(self, request, cliente_id):
        from apps.cadastros.models import Cliente

        cliente = Cliente.objects.for_filial(request.filial_ativa).filter(pk=cliente_id).first()
        if not cliente:
            messages.error(request, 'Cliente não encontrado nesta filial.')
            return redirect(reverse('crm:recompra'))

        RecompraService.remover_manual(filial=request.filial_ativa, cliente=cliente)
        messages.success(request, f'{cliente.nome_display} voltou ao cálculo automático.')
        return redirect(reverse('crm:recompra'))


class RecompraRecalcularView(PermissaoRequiredMixin, View):
    """Força o recálculo agora, sem esperar a janela de obsolescência."""

    permissao_modulo = 'crm'
    permissao_acao = 'ver'

    def post(self, request):
        try:
            qtd = RecompraService.recalcular(request.filial_ativa)
            messages.success(request, f'Padrão de recompra recalculado: {qtd} cliente(s).')
        except Exception as exc:
            messages.error(request, f'Falha ao recalcular: {exc}')
        return redirect(reverse('crm:recompra'))
