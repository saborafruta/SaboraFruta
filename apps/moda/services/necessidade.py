"""
Necessidade de materiais: o que as ordens abertas vão consumir, contra o
que existe no estoque.

A conta é a da especificação, com um cuidado que ela não menciona e que
muda o número na tela:

    Necessário  = consumo da ficha × quantidade de cada ordem aberta
    Livre       = estoque físico − reservas de OUTROS
    Déficit     = Necessário − Livre   (nunca negativo)

O "de outros" é o ponto. `Estoque.quantidade_disponivel` já desconta TODAS
as reservas, inclusive as que este próprio painel criou. Se o déficit fosse
calculado contra ele, reservar material AUMENTARIA o déficit — a ação que
resolve o problema pioraria o indicador, e o usuário reservaria de novo até
o estoque acabar. Aqui a reserva feita para estas ordens volta para o
"livre", e reservar não mexe no déficit. É isso que se quer: reservar
separa o material, não cria necessidade nova.

SEM CONVERSÃO DE UNIDADE. A ficha diz "1,296 m" e o produto de estoque tem
a unidade dele; o sistema compara os números como estão. Ligar um material
medido em metros a um produto controlado em quilos daria um déficit sem
sentido — a tela mostra as duas unidades lado a lado justamente para isso
aparecer.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction

from apps.core.services.exceptions import DomainError
from apps.moda.models import (
    ItemRequisicao, OrdemProducao, RequisicaoMaterial, ReservaMaterial,
)

ZERO = Decimal('0')


@dataclass
class Necessidade:
    """Uma linha do painel: um material e a situação dele."""
    chave: str
    descricao: str
    codigo: str
    unidade: str
    tipo: str
    produto: object = None          # produtos.Produto, quando ligado
    material_id: int | None = None  # linha da ficha, para a reserva apontar
    previsto: Decimal = ZERO
    estoque_fisico: Decimal = ZERO
    reservado_total: Decimal = ZERO
    reservado_nosso: Decimal = ZERO
    ordens: list = field(default_factory=list)

    @property
    def ligado(self) -> bool:
        return self.produto is not None

    @property
    def livre(self) -> Decimal:
        """
        Estoque disponível para ESTAS ordens.

        Soma de volta o que já reservamos para elas: essa quantidade está
        fisicamente separada para este trabalho, não é falta.
        """
        if not self.ligado:
            return ZERO
        outros = self.reservado_total - self.reservado_nosso
        livre = self.estoque_fisico - max(outros, ZERO)
        return livre if livre > ZERO else ZERO

    @property
    def deficit(self) -> Decimal:
        if not self.ligado:
            return ZERO
        falta = self.previsto - self.livre
        return falta.quantize(Decimal('0.0001')) if falta > ZERO else ZERO

    @property
    def insuficiente(self) -> bool:
        return self.deficit > ZERO

    @property
    def a_reservar(self) -> Decimal:
        """Quanto ainda dá para reservar agora: o que falta, limitado ao livre."""
        if not self.ligado:
            return ZERO
        falta = self.previsto - self.reservado_nosso
        if falta <= ZERO:
            return ZERO
        disponivel = self.livre - self.reservado_nosso
        return min(falta, disponivel) if disponivel > ZERO else ZERO

    @property
    def cobertura(self) -> Decimal:
        """Percentual do necessário que o estoque cobre."""
        if not self.previsto:
            return Decimal('100')
        return min((self.livre / self.previsto * 100), Decimal('999')).quantize(Decimal('0.1'))


class NecessidadeService:

    # ── Cálculo ──────────────────────────────────────────────────────────

    @staticmethod
    def ordens_abertas(filial):
        return (
            OrdemProducao.objects.for_filial(filial)
            .exclude(status__in=OrdemProducao.STATUS_ENCERRADOS)
            .select_related('pedido', 'pedido__cliente', 'item', 'item__produto')
            .prefetch_related('item__produto__ficha__materiais__produto_estoque')
        )

    @classmethod
    def calcular(cls, filial, ordens=None) -> list[Necessidade]:
        from apps.estoque.models.estoque import Estoque

        ordens = list(ordens if ordens is not None else cls.ordens_abertas(filial))
        linhas: dict[str, Necessidade] = {}

        for ordem in ordens:
            produto = ordem.item.produto
            ficha = getattr(produto, 'ficha', None) if produto else None
            if ficha is None:
                continue

            for material in ficha.materiais.all():
                consumo = material.consumo_bruto * ordem.quantidade
                if not consumo:
                    continue

                # Agrupa pelo produto de estoque quando ligado; senão pelo
                # texto. Sem isso, o mesmo tecido em duas fichas viraria duas
                # linhas e o comprador pediria duas vezes.
                chave = (
                    f'p{material.produto_estoque_id}' if material.produto_estoque_id
                    else f't{(material.codigo or material.descricao).strip().lower()}'
                )
                linha = linhas.get(chave)
                if linha is None:
                    linha = Necessidade(
                        chave=chave,
                        descricao=material.descricao,
                        codigo=material.codigo,
                        unidade=material.get_unidade_display(),
                        tipo=material.get_tipo_display(),
                        produto=material.produto_estoque,
                        material_id=material.pk,
                    )
                    linhas[chave] = linha

                linha.previsto += consumo
                if ordem not in linha.ordens:
                    linha.ordens.append(ordem)

        cls._preencher_estoque(filial, linhas, ordens, Estoque)

        # Insuficientes primeiro, e dentro deles o maior déficit no topo: a
        # tela existe para responder "o que falta comprar", e a resposta tem
        # de estar na primeira linha.
        return sorted(
            linhas.values(),
            key=lambda l: (not l.insuficiente, -l.deficit, l.descricao),
        )

    @staticmethod
    def _preencher_estoque(filial, linhas, ordens, Estoque) -> None:
        ids = [l.produto.pk for l in linhas.values() if l.ligado]
        if not ids:
            return

        saldos = {
            e.produto_id: e for e in
            Estoque.objects.filter(produto_id__in=ids, filial=filial)
        }
        reservas = defaultdict(Decimal)
        for r in ReservaMaterial.objects.for_filial(filial).filter(
            produto_id__in=ids,
            status=ReservaMaterial.Status.ATIVA,
            ordem__in=[o.pk for o in ordens],
        ):
            reservas[r.produto_id] += r.quantidade

        for linha in linhas.values():
            if not linha.ligado:
                continue
            saldo = saldos.get(linha.produto.pk)
            if saldo:
                linha.estoque_fisico = saldo.quantidade_atual
                linha.reservado_total = saldo.quantidade_reservada
            linha.reservado_nosso = reservas[linha.produto.pk]

    @staticmethod
    def resumo(linhas: list[Necessidade]) -> dict:
        faltando = [l for l in linhas if l.insuficiente]
        return {
            'materiais': len(linhas),
            'insuficientes': len(faltando),
            'sem_ligacao': sum(1 for l in linhas if not l.ligado),
            'deficit_total': sum((l.deficit for l in faltando), ZERO),
        }

    # ── Reserva ──────────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def reservar(cls, filial, linha: Necessidade, usuario) -> list[ReservaMaterial]:
        """
        Separa o material que dá para separar, rateado entre as ordens.

        Rateado, e não tudo numa ordem só: a reserva serve para saber o que
        já está garantido em cada OP, e concentrar num único registro
        esconderia que as outras ficaram descobertas.

        Quem mexe em `quantidade_reservada` é o `MovimentacaoService` — este
        serviço só decide quanto e para quem.
        """
        from apps.estoque.services.movimentacao_service import MovimentacaoService

        if not linha.ligado:
            raise DomainError(
                'Este material não está ligado a um produto de estoque. '
                'Ligue-o na ficha técnica para poder reservar.'
            )

        total = linha.a_reservar
        if total <= ZERO:
            raise DomainError('Não há saldo livre para reservar deste material.')

        criadas = []
        restante = total
        for i, ordem in enumerate(linha.ordens):
            if restante <= ZERO:
                break
            # A última ordem leva o resto, para a soma das reservas fechar
            # exatamente com o que foi reservado no estoque.
            ultima = i == len(linha.ordens) - 1
            fatia = restante if ultima else (
                total / len(linha.ordens)
            ).quantize(Decimal('0.0001'))
            fatia = min(fatia, restante)
            if fatia <= ZERO:
                continue

            criadas.append(ReservaMaterial(
                filial=filial, ordem=ordem, produto=linha.produto,
                material_id=linha.material_id, quantidade=fatia,
                criado_por=usuario,
            ))
            restante -= fatia

        MovimentacaoService.reservar_estoque(
            produto_id=linha.produto.pk, filial_id=filial.pk, quantidade=total,
        )
        ReservaMaterial.objects.bulk_create(criadas)
        return criadas

    @classmethod
    @transaction.atomic
    def cancelar_reserva(cls, reserva: ReservaMaterial, usuario) -> None:
        from apps.estoque.services.movimentacao_service import MovimentacaoService

        if reserva.status != ReservaMaterial.Status.ATIVA:
            raise DomainError('Esta reserva já não está ativa.')

        MovimentacaoService.liberar_reserva(
            produto_id=reserva.produto_id, filial_id=reserva.filial_id,
            quantidade=reserva.quantidade, tolerar_ausente=True,
        )
        reserva.status = ReservaMaterial.Status.CANCELADA
        reserva.save(update_fields=['status'])

    # ── Requisição ───────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def gerar_requisicao(cls, filial, linhas: list[Necessidade], usuario) -> RequisicaoMaterial:
        """
        Cria a requisição com tudo que está em déficit.

        NÃO cria pedido de compra: fornecedor, preço e condição não saem da
        ficha técnica, e inventá-los produziria um documento que o comprador
        teria de refazer inteiro. A requisição diz o que falta; compras
        decide de quem e por quanto.
        """
        faltando = [l for l in linhas if l.insuficiente]
        if not faltando:
            raise DomainError('Nenhum material em déficit — não há o que requisitar.')

        requisicao = RequisicaoMaterial.objects.create(
            filial=filial, criado_por=usuario,
            observacao='Gerada a partir da necessidade das ordens em aberto.',
        )
        ItemRequisicao.objects.bulk_create([
            ItemRequisicao(
                requisicao=requisicao,
                produto=l.produto,
                descricao=l.descricao,
                codigo=l.codigo,
                unidade=l.unidade,
                quantidade=l.deficit,
                observacao=f'{l.tipo} · {len(l.ordens)} ordem(ns)',
            )
            for l in faltando
        ])
        return requisicao
