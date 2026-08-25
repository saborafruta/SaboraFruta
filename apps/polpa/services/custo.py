"""
Custo industrial: o previsto da receita contra o realizado da batida.

O PREVISTO JÁ EXISTIA em `ReceitaService.custos` — matéria-prima com a perda
por dentro, embalagem separada, processo, e as divisões por unidade, quilo e
caixa. O que faltava era o outro lado e a comparação.

O REALIZADO NÃO É O PREVISTO COM OUTRO NOME. Ele sai de onde o dinheiro de
fato saiu:

  · MATÉRIA-PRIMA e EMBALAGEM pelo custo dos LOTES que o FEFO consumiu, no
    razão. Não pelo custo médio do produto: o lote de manga da semana passada
    custou o que custou, e recalcular pela média de hoje reescreveria o custo
    de um produto que já foi vendido;

  · a SEPARAÇÃO entre os dois usa a mesma `FichaProduto.classe` que a receita
    usa. `op_service` soma tudo em `custo_materia_prima` -- o pote entra junto
    com a fruta. Comparar esse total com o previsto, que separa, seria
    comparar coisas diferentes e concluir errado sobre qual dos dois subiu;

  · PERDA entra no custo realizado, e não no previsto. Perda prevista já está
    dentro da matéria-prima (a receita compra 1.000 kg para render 600); a
    perda REGISTRADA é o que passou disso, e é dinheiro que saiu sem virar
    produto;

  · ENERGIA E OUTROS vêm de `CustoReceita`, cadastrados. A ficha do ERP não
    tem campo para eles, e numa fábrica de congelados a energia da câmara não
    é detalhe.

O DESVIO É POR CATEGORIA, não só no total. "Custo 8% acima" não diz o que
fazer; "matéria-prima 22% acima e o resto igual" manda olhar a compra de
fruta. É a diferença entre um número e uma pista.
"""
from __future__ import annotations

from decimal import Decimal

from apps.estoque.models import MovimentacaoEstoque
from apps.polpa.models import CustoReceita, FichaProduto, OrdemPolpa

ZERO = Decimal('0')
CENTAVO = Decimal('0.0001')
REAL = Decimal('0.01')

DOC_OP = MovimentacaoEstoque.DocumentoTipo.ORDEM_PRODUCAO
SAIDA_PRODUCAO = MovimentacaoEstoque.TipoOperacao.PRODUCAO_SAIDA

# As categorias da especificação, na ordem em que a fábrica pensa nelas.
CATEGORIAS = (
    ('materia_prima', 'Matéria-prima'),
    ('embalagem', 'Embalagem'),
    ('mao_de_obra', 'Mão de obra'),
    ('indireto', 'Custos indiretos'),
    ('extras', 'Energia e outros'),
    ('perdas', 'Perdas'),
)


class CustoService:

    # ── Realizado ────────────────────────────────────────────────────────

    @classmethod
    def realizado(cls, op: OrdemPolpa) -> dict:
        """
        O que a batida custou de verdade.

        `None` no total enquanto a ordem não foi concluída: zero seria lido
        como "não custou nada", e uma ordem em andamento apareceria como a
        mais barata da lista.
        """
        ordem = op.ordem
        produzida = ordem.quantidade_produzida or ZERO
        peso = cls._peso_produzido(op, produzida)

        mp, embalagem = cls._insumos_consumidos(ordem)
        mao_de_obra = ordem.custo_mao_obra or ZERO
        indireto = ordem.custo_indireto or ZERO
        extras = cls._extras(op, produzida, peso)
        perdas = cls._perdas(ordem)

        total = mp + embalagem + mao_de_obra + indireto + extras + perdas
        concluida = op.situacao == OrdemPolpa.Situacao.PRODUZIDA

        return {
            'materia_prima': mp.quantize(REAL),
            'embalagem': embalagem.quantize(REAL),
            'mao_de_obra': mao_de_obra.quantize(REAL),
            'indireto': indireto.quantize(REAL),
            'extras': extras.quantize(REAL),
            'perdas': perdas.quantize(REAL),
            'total': total.quantize(REAL) if concluida else None,
            **cls._divisoes(total if concluida else None, produzida, peso, op),
        }

    # ── Previsto ─────────────────────────────────────────────────────────

    @classmethod
    def previsto(cls, op: OrdemPolpa) -> dict:
        """
        O que a receita dizia que ia custar, na escala DESTA batida.

        A receita é escrita para uma quantidade base (1.000 kg); a ordem pode
        ser de 300. Sem o fator, comparar previsto com realizado compararia
        batidas de tamanhos diferentes e acusaria desvio onde não houve.
        """
        from apps.polpa.services.receita import ReceitaService

        receita = op.receita
        base = op.ordem.ficha_tecnica.quantidade_produzida or ZERO
        planejada = op.quantidade_planejada or ZERO
        fator = (planejada / base) if base > ZERO else ZERO

        da_receita = ReceitaService.custos(receita)
        mp = (da_receita['materia_prima'] or ZERO) * fator
        embalagem = (da_receita['embalagem'] or ZERO) * fator
        ficha = op.ordem.ficha_tecnica
        mao_de_obra = (ficha.custo_mao_obra_padrao or ZERO) * fator
        indireto = (ficha.custo_indireto_padrao or ZERO) * fator

        peso = cls._peso_produzido(op, planejada)
        extras = cls._extras(op, planejada, peso)

        # PERDA NÃO ENTRA NO PREVISTO. A perda esperada já está dentro da
        # matéria-prima -- a receita compra 1.000 kg para render 600. Somá-la
        # de novo aqui contaria a mesma fruta duas vezes.
        total = mp + embalagem + mao_de_obra + indireto + extras

        return {
            'materia_prima': mp.quantize(REAL),
            'embalagem': embalagem.quantize(REAL),
            'mao_de_obra': mao_de_obra.quantize(REAL),
            'indireto': indireto.quantize(REAL),
            'extras': extras.quantize(REAL),
            'perdas': ZERO,
            'total': total.quantize(REAL),
            **cls._divisoes(total, planejada, peso, op),
        }

    # ── Comparação ───────────────────────────────────────────────────────

    @classmethod
    def comparar(cls, op: OrdemPolpa) -> dict:
        """
        Previsto e realizado lado a lado, categoria por categoria.

        O desvio percentual é `None` quando o previsto é zero: dividir por
        zero não é "infinito por cento", é uma conta que não existe -- e
        mostrar um número ali faria alguém explicar um desvio inventado.
        """
        previsto = cls.previsto(op)
        realizado = cls.realizado(op)

        linhas = []
        for chave, rotulo in CATEGORIAS:
            p = previsto.get(chave) or ZERO
            r = realizado.get(chave) or ZERO
            linhas.append({
                'chave': chave,
                'rotulo': rotulo,
                'previsto': p,
                'realizado': r,
                'desvio': (r - p).quantize(REAL),
                'desvio_percentual': (
                    ((r - p) / p * 100).quantize(REAL) if p > ZERO else None
                ),
                'acima': r > p,
            })

        total_p = previsto['total'] or ZERO
        total_r = realizado['total']

        return {
            'previsto': previsto,
            'realizado': realizado,
            'linhas': linhas,
            'concluida': total_r is not None,
            'desvio_total': (
                (total_r - total_p).quantize(REAL) if total_r is not None else None
            ),
            'desvio_total_percentual': (
                ((total_r - total_p) / total_p * 100).quantize(REAL)
                if total_r is not None and total_p > ZERO else None
            ),
        }

    # ── As peças da conta ────────────────────────────────────────────────

    @staticmethod
    def _insumos_consumidos(ordem) -> tuple[Decimal, Decimal]:
        """
        O custo do que saiu do estoque, separado em fruta e embalagem.

        Lê o RAZÃO, com o valor do lote que o FEFO escolheu. `op_service`
        soma os dois em `custo_materia_prima`, e essa soma não serve para
        comparar com um previsto que separa.
        """
        movimentos = (
            MovimentacaoEstoque.objects
            .filter(
                documento_tipo=DOC_OP,
                documento_id=ordem.pk,
                tipo_operacao=SAIDA_PRODUCAO,
            )
            .select_related('produto', 'produto__ficha_polpa')
        )
        mp = embalagem = ZERO
        for movimento in movimentos:
            valor = movimento.valor_total or ZERO
            ficha = getattr(movimento.produto, 'ficha_polpa', None)
            if ficha and ficha.classe == FichaProduto.Classe.EMBALAGEM:
                embalagem += valor
            else:
                mp += valor
        return mp, embalagem

    @staticmethod
    def _extras(op: OrdemPolpa, quantidade, peso) -> Decimal:
        return sum(
            (
                custo.total_para(quantidade, peso)
                for custo in CustoReceita.all_objects.filter(
                    receita=op.receita, ativo=True,
                )
            ),
            ZERO,
        )

    @staticmethod
    def _perdas(ordem) -> Decimal:
        """
        O que as perdas registradas custaram.

        Só o que TEM custo apurado: perda sem `impacto_custo` entra como zero
        em vez de ser estimada aqui. Estimar produziria um número que ninguém
        consegue conferir contra nota nenhuma.
        """
        from apps.producao.models import PerdaProducao

        return sum(
            (p.impacto_custo or ZERO for p in PerdaProducao.objects.filter(
                ordem_producao=ordem,
            )),
            ZERO,
        )

    @staticmethod
    def _peso_produzido(op: OrdemPolpa, quantidade) -> Decimal:
        """
        Quilos que a batida gera. O produto é vendido em unidade, mas o custo
        da indústria se compara em quilo.
        """
        produto = op.receita.produto
        unitario = getattr(produto, 'peso_liquido', None) or ZERO
        return (quantidade or ZERO) * unitario

    @staticmethod
    def _divisoes(total, quantidade, peso, op: OrdemPolpa) -> dict:
        """
        As quatro divisões que a fábrica pergunta.

        `None` onde a conta não é possível: zero seria lido como "de graça", e
        é assim que um produto entra na tabela de preço abaixo do custo.
        """
        por_caixa_qtd = getattr(op.receita.produto, 'quantidade_por_embalagem', None) or ZERO
        if total is None:
            return {'por_unidade': None, 'por_kg': None, 'por_caixa': None}

        por_unidade = (
            (total / quantidade).quantize(CENTAVO) if quantidade > ZERO else None
        )
        return {
            'por_unidade': por_unidade,
            'por_kg': (total / peso).quantize(CENTAVO) if peso > ZERO else None,
            'por_caixa': (
                (por_unidade * por_caixa_qtd).quantize(REAL)
                if por_unidade is not None and por_caixa_qtd > ZERO else None
            ),
        }
