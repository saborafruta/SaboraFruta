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
