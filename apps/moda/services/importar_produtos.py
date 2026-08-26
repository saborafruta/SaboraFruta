"""
Trazer produtos do catálogo do ERP para a confecção.

POR QUE OS DOIS CATÁLOGOS EXISTEM. O produto do ERP é o que se vende, se
estoca e se põe na nota. O produto de moda é o que se PRODUZ: tem modelo,
coleção, tecido e grade — coisas que só a confecção precisa e que não cabem
no cadastro geral sem enchê-lo de campo vazio para todo mundo.

O QUE ESTE MÓDULO EVITA é a redigitação entre os dois. Quem já cadastrou
"Camisa Polo Dry" no ERP não deveria digitar o nome, o código e a descrição
de novo aqui — traz, e completa com o que é da produção.

NÃO É CÓPIA CEGA: o vínculo `produto_erp` fica gravado, então o mesmo
produto não entra duas vezes e, mais tarde, dá para saber que os dois são a
mesma coisa quando o estoque precisar bater.

O QUE NÃO VEM JUNTO — modelo, coleção, tecido e grade — é justamente o que
o ERP não tem. Ficam em branco e são preenchidos na tela do produto de
moda; a ficha técnica lê de lá.
"""
from __future__ import annotations

from django.db import transaction

from apps.core.services.exceptions import DomainError
from apps.core.services.search import filter_queryset_by_terms, normalize_search_text


class ImportarProdutosService:

    @staticmethod
    def disponiveis(filial):
        """
        Produtos do ERP que ainda não têm correspondente na confecção.

        Compara pelo VÍNCULO e também pelo código: produto trazido antes de
        o vínculo existir, ou cadastrado à mão com o mesmo código, não pode
        aparecer como se fosse novo.
        """
        from apps.moda.models import ProdutoModa
        from apps.produtos.models import Produto

        ja_vieram = set(
            ProdutoModa.all_objects
            .filter(filial=filial)
            .exclude(produto_erp__isnull=True)
            .values_list('produto_erp_id', flat=True)
        )
        codigos = {
            (c or '').strip().upper()
            for c in ProdutoModa.all_objects
            .filter(filial=filial).values_list('codigo', flat=True)
        }

        candidatos = (
            Produto.objects.for_filial(filial)
            .filter(ativo=True)
            .order_by('descricao')
        )
        return [
            p for p in candidatos
            if p.pk not in ja_vieram
            and (getattr(p, 'codigo', '') or '').strip().upper() not in codigos
        ]

    @classmethod
    @transaction.atomic
    def importar(cls, filial, ids: list[int], usuario=None) -> list:
        """
        Cria um produto de moda para cada produto do ERP escolhido.

        Em transação: importar dez e falhar no sétimo deixaria seis criados
        e a tela dizendo que deu erro — o pior dos dois mundos.
        """
        from apps.moda.models import ProdutoModa
        from apps.produtos.models import Produto

        if not ids:
            raise DomainError('Escolha ao menos um produto para trazer.')

        disponiveis = {p.pk: p for p in cls.disponiveis(filial)}
        escolhidos = [disponiveis[i] for i in ids if i in disponiveis]
        if not escolhidos:
            raise DomainError(
                'Nenhum dos produtos escolhidos está disponível — eles já '
                'podem ter sido trazidos por outra pessoa.'
            )

        criados = []
        for produto in escolhidos:
            criados.append(ProdutoModa(
                filial=filial,
                produto_erp=produto,
                codigo=cls._codigo(produto),
                nome=(produto.descricao or '')[:120],
                # A observação do produto do ERP vira a descrição da moda:
                # é onde o cadastro geral costuma guardar o detalhe que a
                # produção também quer ler.
                descricao=(produto.observacao or ''),
            ))
        ProdutoModa.objects.bulk_create(criados)
        return criados

    @staticmethod
    def _codigo(produto) -> str:
        """
        O código do ERP quando existe; senão, um derivado do id.

        Código é chave por filial no produto de moda: deixá-lo em branco
        faria a segunda importação sem código colidir com a primeira.
        """
        codigo = (getattr(produto, 'codigo', '') or '').strip().upper()
        return codigo[:30] if codigo else f'ERP{produto.pk}'


class BuscaProdutos:
    """
    A busca que alimenta o campo de produto — os DOIS catálogos.

    O produto da confecção e o produto do ERP são cadastros diferentes de
    propósito (ver o topo deste arquivo). Mas quem está preenchendo uma
    ficha ou um pedido não quer saber disso: quer achar a camisa que já está
    cadastrada em Cadastros › Produtos.

    Então o campo lista os dois, em grupos separados, e escolher um do ERP
    TRAZ o produto para a confecção na hora — mesma importação da tela de
    produtos, com o vínculo gravado. Não é catálogo duplicado: é o mesmo
    produto, agora também conhecido pela produção.
    """

    LIMITE = 20

    @classmethod
    def procurar(cls, filial, termo: str = '', sem_ficha: bool = False,
                 limite: int | None = None) -> list[dict]:
        from apps.moda.models import ProdutoModa

        termo = (termo or '').strip()
        limite = limite or cls.LIMITE

        da_moda = (
            ProdutoModa.objects.for_filial(filial)
            .filter(ativo=True)
            .select_related('modelo', 'colecao', 'tecido', 'grade')
            .order_by('codigo')
        )
        if sem_ficha:
            # Ficha é OneToOne: oferecer produto que já tem ficha só renderia
            # erro de integridade depois de a pessoa preencher tudo.
            da_moda = da_moda.filter(ficha__isnull=True)
        if termo:
            da_moda = filter_queryset_by_terms(
                da_moda, termo,
                fields=('nome', 'codigo', 'referencia', 'descricao'),
            )

        achados = [cls.como_dicionario(p) for p in da_moda[:limite]]

        # Os do ERP que ainda não vieram. Nenhum deles tem ficha -- não
        # existem como produto de moda ainda --, então `sem_ficha` não muda
        # nada aqui.
        do_erp = ImportarProdutosService.disponiveis(filial)
        if termo:
            termos = normalize_search_text(termo).split()
            do_erp = [
                p for p in do_erp
                if all(
                    token in normalize_search_text(
                        f'{p.descricao or ""} {getattr(p, "codigo", "") or ""} '
                        f'{getattr(p, "codigo_barras", "") or ""}'
                    )
                    for token in termos
                )
            ]
        achados += [cls.do_erp(p) for p in do_erp[:limite]]

        return achados

    @staticmethod
    def como_dicionario(produto) -> dict:
        """Produto da confecção, com o que a ficha vai herdar dele."""
        return {
            'valor': f'moda:{produto.pk}',
            'nome': produto.nome,
            'codigo': produto.codigo or '',
            'origem': 'confeccao',
            # É o que a tela da ficha mostra em "o que vem do cadastro deste
            # produto": sem ver O QUE ela lê, a frase não ajuda ninguém.
            'modelo': str(produto.modelo) if produto.modelo_id else '',
            'colecao': str(produto.colecao) if produto.colecao_id else '',
            'tecido': str(produto.tecido) if produto.tecido_id else '',
            'grade': str(produto.grade) if produto.grade_id else '',
        }

    @staticmethod
    def do_erp(produto) -> dict:
        """
        Produto do ERP, ainda sem correspondente na confecção.

        Modelo, coleção, tecido e grade vêm vazios porque o ERP não os tem —
        e é justamente isso que a pessoa precisa saber ANTES de escolher: o
        produto entra, e esses campos ficam para ela preencher.
        """
        return {
            'valor': f'erp:{produto.pk}',
            'nome': produto.descricao or '',
            'codigo': getattr(produto, 'codigo', '') or '',
            'origem': 'erp',
            'modelo': '', 'colecao': '', 'tecido': '', 'grade': '',
        }

    @staticmethod
    def resolver(filial, valor: str):
        """
        A escolha vira um `ProdutoModa` de verdade — importando, se preciso.

        Em um lugar só porque as duas telas que usam o campo (ficha e item
        do pedido) precisam da mesma resolução: duas cópias divergiriam, e
        uma delas passaria a gravar produto de outra filial.
        """
        from apps.moda.models import ProdutoModa
        from apps.produtos.models import Produto

        valor = (valor or '').strip()
        if not valor:
            return None

        origem, _, chave = valor.partition(':')
        if not chave.isdigit():
            raise DomainError('Produto inválido.')

        if origem == 'moda':
            produto = ProdutoModa.objects.for_filial(filial).filter(pk=chave).first()
            if produto is None:
                raise DomainError('Este produto não é desta filial.')
            return produto

        if origem == 'erp':
            do_erp = Produto.objects.for_filial(filial).filter(pk=chave).first()
            if do_erp is None:
                raise DomainError('Este produto não é desta filial.')
            return ImportarProdutosService.importar(filial, [do_erp.pk])[0]

        raise DomainError('Produto inválido.')
