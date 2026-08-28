"""
Relatório de acerto de viagem.

O DOCUMENTO QUE FECHA A VIAGEM
==============================

É o que o escritório e quem viajou olham juntos no fim: o que saiu, o que
virou dinheiro, o que foi dado, o que voltou — e quais documentos amparam cada
uma dessas coisas. Sem ele, responder "essa viagem fechou?" exige abrir cinco
telas e somar à mão.

NADA É RECALCULADO AQUI
=======================

As quantidades vêm do quadro de acerto, que já é a fonte da conciliação. Os
documentos vêm de cada serviço de emissão. Os valores são a única coisa que
este módulo soma, e ele soma a partir dos mesmos registros — nunca de uma
segunda contagem paralela, que é como dois relatórios do mesmo mês passam a
discordar.

VALOR RETORNADO É PELO VALOR DE SAÍDA
=====================================

A mercadoria voltou como saiu: o que ela vale no acerto é o que a remessa
declarou, e não um preço de venda que nunca aconteceu. Avaliar o retorno por
preço de tabela faria a viagem parecer ter perdido dinheiro só por ter
voltado com carga.
"""
from decimal import Decimal

from django.db.models import Sum

from apps.fiscal.models import NaturezaOperacao
from apps.logistica.models import ItemCarga, SaldoCarga, VendaViagem

ZERO = Decimal('0')
E = NaturezaOperacao.Especie


class RelatorioAcertoService:

    # ── O relatório inteiro ──────────────────────────────────────────────

    @classmethod
    def relatorio(cls, viagem) -> dict:
        from apps.logistica.services.estoque_viagem import EstoqueViagemService

        quadro = EstoqueViagemService.quadro(viagem)
        valores = cls.valores(viagem, quadro)
        quantidades = cls.quantidades(quadro)
        return {
            'viagem': viagem,
            'identificacao': cls.identificacao(viagem),
            'quantidades': quantidades,
            'valores': valores,
            'indicadores': cls.indicadores(quadro, valores, quantidades),
            'clientes': cls.clientes_atendidos(viagem),
            'documentos': cls.documentos(viagem),
            'quadro': quadro,
            'acerto': EstoqueViagemService.acerto(quadro),
            'fecha': quadro['fecha'],
            'diferenca': quadro['diferenca'],
        }

    # ── Cabeçalho ────────────────────────────────────────────────────────

    @staticmethod
    def identificacao(viagem) -> dict:
        return {
            'numero': viagem.numero,
            'motorista': viagem.motorista_nome or '—',
            'documento_motorista': viagem.motorista_documento or '',
            'veiculo': viagem.veiculo_descricao or '—',
            'placa': viagem.veiculo_placa or '—',
            'data_saida': viagem.data_saida,
            'hora_saida': viagem.hora_saida,
            'previsao_retorno': viagem.previsao_retorno,
            'data_retorno': viagem.data_retorno,
            'rota': viagem.rota or '—',
            'vendedor': viagem.vendedor,
            'responsavel': viagem.responsavel,
            'status': viagem.get_status_display(),
            'observacoes': viagem.observacao or '',
        }

    # ── Quantidades ──────────────────────────────────────────────────────

    @staticmethod
    def quantidades(quadro: dict) -> dict:
        """As quatro colunas, do quadro que já concilia a viagem."""
        return {
            'carregada': quadro['carga_inicial'],
            'vendida': quadro['vendas_realizadas'] + quadro['venda_na_rua'],
            'vendida_previa': quadro['vendas_realizadas'],
            'vendida_na_rua': quadro['venda_na_rua'],
            'bonificada': quadro['bonificacao'],
            'retornada': quadro['retorno'],
            'em_poder': quadro['em_poder'],
            'baixada': quadro['baixado'],
        }

    # ── Valores ──────────────────────────────────────────────────────────

    @classmethod
    def valores(cls, viagem, quadro: dict) -> dict:
        carga = cls._valor_da_carga(viagem)
        entregas = cls._valor_das_entregas(viagem)
        return {
            'carga': carga['total'],
            'carga_venda': carga.get(E.VENDA, ZERO),
            'carga_remessa': carga.get(E.REMESSA_VENDA_FORA, ZERO),
            'carga_bonificacao': carga.get(E.BONIFICACAO, ZERO),
            # VENDIDO SOMA AS DUAS ORIGENS: a mercadoria que já tinha comprador
            # quando subiu no caminhão e a que foi vendida na rua. Separar as
            # duas na leitura é útil; somar só uma delas seria mentir sobre o
            # que a viagem levou de dinheiro.
            'vendido': carga.get(E.VENDA, ZERO) + entregas[VendaViagem.Tipo.VENDA],
            'vendido_previa': carga.get(E.VENDA, ZERO),
            'vendido_na_rua': entregas[VendaViagem.Tipo.VENDA],
            'bonificado': (
                carga.get(E.BONIFICACAO, ZERO)
                + entregas[VendaViagem.Tipo.BONIFICACAO]
            ),
            'retornado': cls._valor_retornado(viagem),
        }

    @staticmethod
    def _valor_da_carga(viagem) -> dict:
        """O valor do que subiu no caminhão, por natureza."""
        por_especie = {'total': ZERO}
        linhas = (
            ItemCarga.objects.filter(viagem=viagem)
            .values('natureza__especie')
            .annotate(valor=Sum('valor_total'))
        )
        for linha in linhas:
            valor = linha['valor'] or ZERO
            por_especie[linha['natureza__especie']] = valor
            por_especie['total'] += valor
        return por_especie

    @staticmethod
    def _valor_das_entregas(viagem) -> dict:
        """
        O valor do que foi entregue na rua, por tipo.

        SÓ AS REGISTRADAS: venda cancelada devolveu a mercadoria ao saldo, e
        contá-la aqui faria o acerto cobrar do vendedor um dinheiro que nunca
        entrou.
        """
        totais = {tipo: ZERO for tipo in VendaViagem.Tipo.values}
        linhas = (
            VendaViagem.objects
            .filter(viagem=viagem, status=VendaViagem.Status.REGISTRADA)
            .values('tipo').annotate(valor=Sum('valor_total'))
        )
        for linha in linhas:
            totais[linha['tipo']] = linha['valor'] or ZERO
        return totais

    @staticmethod
    def _valor_retornado(viagem) -> Decimal:
        """
        O que voltou, pelo valor com que saiu.

        A mercadoria voltou como saiu: avaliar o retorno por preço de venda
        faria a viagem parecer ter perdido dinheiro só por ter voltado com
        carga.
        """
        # O valor unitario da remessa e' o que a nota declarou; e' ele que
        # responde quanto vale a mercadoria que voltou.
        unitarios = {}
        for item in ItemCarga.objects.filter(
            viagem=viagem,
            natureza__especie=E.REMESSA_VENDA_FORA,
        ).select_related('produto'):
            unitarios.setdefault(item.produto_id, item.valor_unitario or ZERO)

        total = ZERO
        for saldo in SaldoCarga.objects.filter(viagem=viagem):
            devolvido = saldo.quantidade_retornada or ZERO
            if devolvido <= ZERO:
                continue
            unitario = unitarios.get(saldo.produto_id, saldo.custo_unitario or ZERO)
            total += devolvido * unitario
        return total.quantize(Decimal('0.01'))

    # ── Indicadores ──────────────────────────────────────────────────────

    @classmethod
    def indicadores(cls, quadro: dict, valores: dict, quantidades: dict) -> dict:
        """
        Os números que resumem a viagem.

        DUAS LEITURAS DE APROVEITAMENTO, e não uma
        =========================================

        Sobre a carga inteira o número é o que a especificação pede, mas ele
        engana: a mercadoria que já subiu vendida sempre "aproveita", e a de
        bonificação nunca — então uma viagem com muita venda prévia mostra 90%
        mesmo que o vendedor não tenha vendido nada na rua.

        Sobre a remessa, o número diz o que se quer saber de fato: do que saiu
        SEM comprador, quanto virou venda. É esse que mede a rota e o vendedor,
        e por isso ele aparece ao lado do outro em vez de ficar escondido.

        PERCENTUAL DE RETORNO é sobre o que foi remetido, e não sobre a carga:
        só a mercadoria sem comprador podia voltar. Dividir pela carga inteira
        diluiria o número justamente com a parte que nunca teve chance de
        retornar, e uma rota ruim pareceria aceitável.
        """
        carga_valor = valores['carga']
        carga_qtd = quantidades['carregada']
        remetido = quadro['remetido']

        return {
            # ── Valores ──────────────────────────────────────────────────
            'valor_carregado': carga_valor,
            'valor_vendido': valores['vendido'],
            'valor_bonificado': valores['bonificado'],
            'valor_retornado': valores['retornado'],
            # ── Quantidades ──────────────────────────────────────────────
            'quantidade_carregada': carga_qtd,
            'quantidade_vendida': quantidades['vendida'],
            'quantidade_bonificada': quantidades['bonificada'],
            'quantidade_retornada': quantidades['retornada'],
            # ── Percentuais ──────────────────────────────────────────────
            'aproveitamento': cls._percentual(quantidades['vendida'], carga_qtd),
            'aproveitamento_valor': cls._percentual(valores['vendido'], carga_valor),
            # O que mede a rota: do que saiu sem comprador, quanto vendeu.
            'aproveitamento_remessa': cls._percentual(
                quadro['venda_na_rua'], remetido,
            ),
            'percentual_retorno': cls._percentual(
                quantidades['retornada'], remetido,
            ),
            'remetido': remetido,
            'tem_remessa': remetido > ZERO,
        }

    @staticmethod
    def _percentual(parte, total) -> Decimal | None:
        """
        A fração, em pontos percentuais.

        DEVOLVE `None` QUANDO NÃO HÁ BASE, e não zero: viagem sem remessa não
        teve 0% de aproveitamento na rua — ela não teve rua. Mostrar zero faria
        a média de várias viagens despencar por causa das que nem tentaram.
        """
        total = Decimal(str(total or 0))
        if total <= ZERO:
            return None
        return (Decimal(str(parte or 0)) / total * Decimal('100')).quantize(
            Decimal('0.1')
        )

    # ── Clientes atendidos ───────────────────────────────────────────────

    @staticmethod
    def clientes_atendidos(viagem) -> dict:
        """
        Quem recebeu mercadoria nesta viagem, por venda ou bonificação.

        CONTA POR PESSOA, e não por entrega: o mesmo cliente pode ter comprado
        duas vezes na mesma rota, e dizer que a viagem atendeu dois clientes
        quando atendeu um é inflar o número que o comercial usa para medir a
        rota.
        """
        linhas = {}
        entregas = (
            VendaViagem.objects
            .filter(viagem=viagem, status=VendaViagem.Status.REGISTRADA)
            .select_related('cliente')
        )
        for entrega in entregas:
            # Cliente sem cadastro e' identificado pelo documento, e sem ele
            # pelo nome -- e' o maximo que da' para agrupar com honestidade.
            chave = (
                f'c{entrega.cliente_id}' if entrega.cliente_id
                else (entrega.cliente_documento or entrega.cliente_nome.lower())
            )
            linha = linhas.setdefault(chave, {
                'nome': entrega.cliente_nome,
                'documento': entrega.cliente_documento,
                'cliente': entrega.cliente,
                'vendas': 0, 'bonificacoes': 0,
                'valor_vendido': ZERO, 'valor_bonificado': ZERO,
            })
            if entrega.tipo == VendaViagem.Tipo.BONIFICACAO:
                linha['bonificacoes'] += 1
                linha['valor_bonificado'] += entrega.valor_total or ZERO
            else:
                linha['vendas'] += 1
                linha['valor_vendido'] += entrega.valor_total or ZERO

        # A carga ja' vendida tambem atendeu cliente: ele recebeu mercadoria
        # nesta viagem, mesmo sem ter havido venda na rua.
        for item in (
            ItemCarga.objects.filter(viagem=viagem, cliente__isnull=False)
            .select_related('cliente', 'natureza')
        ):
            chave = f'c{item.cliente_id}'
            linha = linhas.setdefault(chave, {
                'nome': str(item.cliente),
                'documento': getattr(item.cliente, 'cpf_cnpj', '') or '',
                'cliente': item.cliente,
                'vendas': 0, 'bonificacoes': 0,
                'valor_vendido': ZERO, 'valor_bonificado': ZERO,
            })
            if item.natureza.especie == E.BONIFICACAO:
                linha['valor_bonificado'] += item.valor_total or ZERO
            else:
                linha['valor_vendido'] += item.valor_total or ZERO

        lista = sorted(linhas.values(), key=lambda l: l['nome'].lower())
        return {'total': len(lista), 'lista': lista}

    # ── Documentos ───────────────────────────────────────────────────────

    @classmethod
    def documentos(cls, viagem) -> dict:
        """
        Os documentos que amparam a viagem, cada um do seu serviço.

        A NOTA AUSENTE APARECE COMO AUSENTE. Uma viagem com remessa não
        emitida é diferente de uma viagem que não precisava de remessa, e o
        relatório precisa distinguir as duas — senão o acerto passa em branco
        por cima da pendência fiscal.
        """
        from apps.logistica.services.bonificacao_nfe import BonificacaoNFeService
        from apps.logistica.services.mdfe_viagem import MDFeViagemService
        from apps.logistica.services.remessa_nfe import RemessaVendaForaService
        from apps.logistica.services.retorno_nfe import RetornoVendaForaService
        from apps.logistica.services.venda_fora_nfe import VendaForaNFeService

        entregas = list(
            VendaViagem.objects
            .filter(viagem=viagem, status=VendaViagem.Status.REGISTRADA)
            .select_related('cliente')
        )
        vendas, bonificacoes = [], []
        for entrega in entregas:
            if entrega.tipo == VendaViagem.Tipo.BONIFICACAO:
                nota = BonificacaoNFeService.nota_da_bonificacao(entrega)
                bonificacoes.append({'entrega': entrega, 'nota': nota})
            else:
                nota = VendaForaNFeService.nota_da_venda(entrega)
                vendas.append({'entrega': entrega, 'nota': nota})

        remessa = RemessaVendaForaService.nota_da_viagem(viagem)
        retorno = RetornoVendaForaService.nota_da_viagem(viagem)
        mdfe = MDFeViagemService.mdfe_da_viagem(viagem)

        pendentes = []
        if RemessaVendaForaService.itens_da_viagem(viagem) and remessa is None:
            pendentes.append('NF-e de remessa não emitida.')
        sem_nota = [v for v in vendas if v['nota'] is None]
        if sem_nota:
            pendentes.append(f'{len(sem_nota)} venda(s) na rua sem NF-e.')
        sem_bonificacao = [b for b in bonificacoes if b['nota'] is None]
        if sem_bonificacao:
            pendentes.append(f'{len(sem_bonificacao)} bonificação(ões) sem NF-e.')

        return {
            'remessa': remessa,
            'vendas': vendas,
            'bonificacoes': bonificacoes,
            'retorno': retorno,
            'mdfe': mdfe,
            'pendentes': pendentes,
            'total': (
                (1 if remessa else 0)
                + sum(1 for v in vendas if v['nota'])
                + sum(1 for b in bonificacoes if b['nota'])
                + (1 if retorno else 0)
            ),
        }
