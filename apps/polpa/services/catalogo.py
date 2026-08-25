"""
O cadastro de itens da fábrica — matéria-prima, embalagem e acabado.

CADASTRAR PELO VERTICAL É UM ATO SÓ. O item vive em dois lugares: o
`produtos.Produto` (que o estoque, a nota e o PDV leem) e a `FichaProduto`
(o vocabulário da fábrica). Fazer a pessoa cadastrar em duas telas
significaria, na prática, produtos sem ficha — porque a segunda tela é a que
se esquece. Por isso este serviço grava os dois juntos, na mesma transação.

O QUE ELE DEFINE SOZINHO, pela classe do item:

  · ACABADO controla lote e validade, e sai por FEFO. Não é preferência: é
    o que a legislação de alimento exige e o que faz o produto mais velho
    sair primeiro. Deixar como caixinha para marcar seria transformar em
    esquecimento aquilo que o processo não admite esquecer.

  · CONGELADO recebe a condição de armazenamento congelada. Quem cadastra
    um picolé não deveria ter de lembrar de marcar isso — e é dessa marca
    que a cadeia de frio depende para saber o que cobrar temperatura.

O QUE ELE NÃO DECIDE: preço, NCM e alíquota. Chutar um NCM "provisório" é
como nasce nota rejeitada meses depois; a ficha prefere apontar que falta.
"""
from __future__ import annotations

from django.db import transaction

from apps.core.services.exceptions import DomainError
from apps.polpa.models import FichaProduto
from apps.produtos.models import Produto, ProdutoFilial

C = FichaProduto.Classe


class CatalogoService:

    # ── Gravação ─────────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def salvar(filial, dados: dict, ficha: FichaProduto | None = None) -> FichaProduto:
        """
        Cria ou atualiza o item — produto do ERP e ficha da fábrica juntos.

        `dados` traz os campos dos dois: o formulário é um só na tela, e
        separá-los aqui é o que evita duas telas de cadastro.
        """
        produto = ficha.produto if ficha else Produto(filial=filial)

        descricao = (dados.get('descricao') or '').strip()
        if not descricao:
            raise DomainError('O item precisa de uma descrição.')

        unidade = dados.get('unidade_medida')
        if unidade is None:
            raise DomainError(
                'Escolha a unidade de medida. Sem ela o item não entra no '
                'estoque — cadastre em Cadastros › Unidades se a lista '
                'estiver vazia.'
            )

        for campo in (
            'descricao', 'codigo', 'codigo_barras', 'ncm', 'cest',
            'unidade_medida', 'peso_liquido', 'peso_bruto',
            'quantidade_por_embalagem', 'tipo_embalagem',
            'temperatura_minima', 'temperatura_maxima', 'preco_custo',
            'preco_venda', 'estoque_minimo',
        ):
            if campo in dados and dados[campo] is not None:
                setattr(produto, campo, dados[campo])

        classe = dados.get('classe') or FichaProduto.CLASSE_DO_TIPO.get(dados.get('tipo'))
        CatalogoService._aplicar_regras(produto, classe, dados)
        produto.save()

        # O VÍNCULO COM A FILIAL é o que faz o produto existir para ela:
        # `Produto.objects.for_filial` lê por ele, e sem isto o item some do
        # próprio catálogo que acabou de cadastrá-lo.
        ProdutoFilial.objects.get_or_create(produto=produto, filial=filial)

        if ficha is None:
            ficha = FichaProduto(filial=filial, produto=produto)
        for campo in (
            'classe', 'tipo', 'sabor', 'fruta', 'volume_ml', 'validade_dias',
            'caixas_por_pallet', 'registro_mapa', 'observacao',
        ):
            if campo in dados:
                setattr(ficha, campo, dados[campo])
        ficha.classe = classe or ficha.classe
        ficha.full_clean(exclude=['produto', 'filial'])
        ficha.save()
        return ficha

    @staticmethod
    def _aplicar_regras(produto: Produto, classe: str | None, dados: dict) -> None:
        """As decisões que a classe do item já responde."""
        if classe == C.ACABADO:
            # Lote, validade e FEFO não são opção em alimento congelado.
            produto.controla_lote = True
            produto.controla_validade = True
            produto.saida_fefo = True
            produto.metodo_saida = Produto.MetodoSaida.FEFO
        elif classe == C.MATERIA_PRIMA:
            # Matéria-prima também é rastreada: é dela que o recall parte.
            produto.controla_lote = True

        if dados.get('congelado'):
            produto.condicao_armazenamento = Produto.CondicaoArmazenamento.CONGELADO
        elif dados.get('refrigerado'):
            produto.condicao_armazenamento = Produto.CondicaoArmazenamento.REFRIGERADO

    # ── Leitura ──────────────────────────────────────────────────────────

    @staticmethod
    def listar(filial, classe: str = '', busca: str = '', tipo: str = ''):
        qs = (
            FichaProduto.objects.for_filial(filial)
            .select_related('produto', 'produto__unidade_medida', 'fruta')
        )
        if classe:
            qs = qs.filter(classe=classe)
        if tipo:
            qs = qs.filter(tipo=tipo)
        if busca:
            from django.db.models import Q
            qs = qs.filter(
                Q(produto__descricao__icontains=busca)
                | Q(produto__codigo__icontains=busca)
                | Q(produto__codigo_barras__icontains=busca)
                | Q(sabor__icontains=busca)
            )
        return qs

    @staticmethod
    def resumo(filial) -> dict:
        """
        Quantos itens de cada classe, e quantos ainda estão incompletos.

        O INCOMPLETO É O NÚMERO QUE IMPORTA: catálogo cheio não diz nada se
        metade dos acabados não tem validade — e é justamente esse item que
        gera lote sem vencimento quando a produção rodar.
        """
        fichas = list(
            FichaProduto.objects.for_filial(filial).select_related('produto')
        )
        por_classe = {}
        for classe, rotulo in FichaProduto.Classe.choices:
            do_grupo = [f for f in fichas if f.classe == classe]
            por_classe[classe] = {
                'label': rotulo,
                'total': len(do_grupo),
                'pendentes': sum(1 for f in do_grupo if f.pendencias()),
            }
        return {
            'total': len(fichas),
            'por_classe': por_classe,
            'pendentes': sum(1 for f in fichas if f.pendencias()),
        }

    @staticmethod
    def sem_ficha(filial):
        """
        Produtos do catálogo do ERP que ainda não têm ficha da fábrica.

        Existem de verdade: entram por XML de compra, por importação ou
        foram cadastrados antes do vertical. Mostrá-los é o que evita a
        conclusão de que "o produto sumiu" — ele está no ERP, só não foi
        classificado para a fábrica ainda.
        """
        return (
            Produto.objects.for_filial(filial)
            .filter(ficha_polpa__isnull=True, ativo=True)
            .order_by('descricao')
        )
