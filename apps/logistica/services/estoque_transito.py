"""
Estoque em trânsito — venda fora do estabelecimento.

É UMA LOCALIZAÇÃO LÓGICA, e não uma conta paralela
==================================================

A mercadoria que sai por remessa deixa o estoque da filial de verdade: o
`quantidade_atual` baixa, amparado pela nota. Ela não fica disponível para
venda no estabelecimento — quem vende consulta `quantidade_disponivel`, e o
que saiu já não está lá.

Mas ela continua sendo da empresa. Some do estoque físico e aparece aqui, com
nome próprio, até ser vendida na rua, bonificada, devolvida ou baixada. Sem
esta tela a mercadoria "sumiria" do sistema entre a saída e o retorno, e a
única forma de saber onde ela está seria abrir viagem por viagem.

Exemplo: 1.000 no estoque, remessa de 300 numa viagem. Depois da saída o
estoque físico mostra 700 e o trânsito mostra 300. A soma continua 1.000, e é
essa soma que o inventário precisa fechar.
"""
from decimal import Decimal

from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce

from apps.logistica.models import SaldoCarga, Viagem

ZERO = Decimal('0')

# O trânsito existe enquanto a viagem existe. Cancelada nunca saiu; finalizada
# já prestou contas, e o saldo dela fechou em zero.
VIAGENS_NA_RUA = (
    Viagem.Status.AGUARDANDO_DOCUMENTOS,
    Viagem.Status.DOCUMENTOS_EMITIDOS,
    Viagem.Status.MDFE_AUTORIZADO,
    Viagem.Status.EM_TRANSITO,
    Viagem.Status.EM_VENDAS,
    Viagem.Status.RETORNANDO,
    Viagem.Status.AGUARDANDO_CONFERENCIA,
)

# `quantidade_em_poder` e' propriedade Python; para somar no banco ela precisa
# existir como expressao. Repetir a conta aqui e' o preco de nao trazer todas
# as linhas para a memoria so' para soma-las.
EM_PODER = (
    Coalesce(F('quantidade_remetida'), Value(ZERO))
    - Coalesce(F('quantidade_vendida'), Value(ZERO))
    - Coalesce(F('quantidade_bonificada'), Value(ZERO))
    - Coalesce(F('quantidade_retornada'), Value(ZERO))
    - Coalesce(F('quantidade_baixada'), Value(ZERO))
)


class EstoqueEmTransitoService:

    NOME = 'Estoque em trânsito — venda fora do estabelecimento'

    @classmethod
    def _saldos(cls, filial):
        return (
            SaldoCarga.objects
            .filter(viagem__filial=filial, viagem__status__in=VIAGENS_NA_RUA)
            .annotate(em_poder=EM_PODER)
            .filter(em_poder__gt=ZERO)
        )

    @classmethod
    def por_produto(cls, filial, busca: str = '') -> list[dict]:
        """
        O que está na rua, somado por produto.

        SOMADO ENTRE VIAGENS: o mesmo produto pode estar em dois caminhões, e
        quem olha o estoque quer saber quanto da empresa está fora, não quanto
        está em cada carga.
        """
        from apps.estoque.models import Estoque

        saldos = cls._saldos(filial).select_related('produto', 'viagem', 'lote')
        if busca:
            saldos = saldos.filter(produto__descricao__icontains=busca)

        agrupado: dict[int, dict] = {}
        for saldo in saldos:
            linha = agrupado.setdefault(saldo.produto_id, {
                'produto': saldo.produto,
                'em_transito': ZERO,
                'viagens': [],
            })
            linha['em_transito'] += saldo.em_poder
            linha['viagens'].append({
                'viagem': saldo.viagem,
                'lote': saldo.lote,
                'quantidade': saldo.em_poder,
                'remetida': saldo.quantidade_remetida or ZERO,
            })

        # O ESTOQUE FISICO AO LADO: e' a leitura que a pessoa faz de verdade --
        # "tenho 700 aqui e 300 na rua" -- e sem os dois numeros juntos ela tem
        # que abrir outra tela para fechar a conta.
        fisico = dict(
            Estoque.objects
            .filter(filial=filial, produto_id__in=agrupado.keys())
            .values_list('produto_id', 'quantidade_atual')
        )
        linhas = []
        for produto_id, linha in agrupado.items():
            linha['fisico'] = fisico.get(produto_id, ZERO) or ZERO
            linha['total'] = linha['fisico'] + linha['em_transito']
            linha['viagens'].sort(key=lambda v: v['viagem'].numero)
            linhas.append(linha)
        linhas.sort(key=lambda l: str(l['produto']))
        return linhas

    @classmethod
    def total(cls, filial) -> Decimal:
        """Quanto da empresa está na rua, em unidades."""
        return cls._saldos(filial).aggregate(
            total=Sum('em_poder', output_field=DecimalField()),
        )['total'] or ZERO

    @classmethod
    def do_produto(cls, filial, produto) -> Decimal:
        """Quanto deste produto está na rua."""
        return cls._saldos(filial).filter(produto=produto).aggregate(
            total=Sum('em_poder', output_field=DecimalField()),
        )['total'] or ZERO

    @classmethod
    def resumo(cls, filial) -> dict:
        linhas = cls.por_produto(filial)
        viagens = set()
        for linha in linhas:
            viagens.update(v['viagem'].pk for v in linha['viagens'])
        return {
            'produtos': len(linhas),
            'unidades': sum((l['em_transito'] for l in linhas), ZERO),
            'viagens': len(viagens),
        }
