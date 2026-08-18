"""
Geração das variantes (SKUs) de um produto.

Regra: uma variante para cada cruzamento cor ativa × tamanho da grade.

    CAMISETA ESPORTIVA, cor Amarelo, grade Adulto (PP..XGG)
    → CAM001-AMA-PP, CAM001-AMA-P, ... CAM001-AMA-XGG

O serviço é idempotente: rodar de novo depois de acrescentar uma cor cria
só as variantes que faltam e não toca nas que já existem. Isso importa
porque a variante é referenciada por estoque e produção — recriá-la
apagaria o histórico dela.
"""
from dataclasses import dataclass

from django.db import transaction

from apps.core.services.exceptions import DadosInvalidosError

from ..models import Variante

SEPARADOR = '-'


@dataclass
class ResultadoGeracao:
    criadas: int
    ja_existiam: int
    reativadas: int

    @property
    def total(self) -> int:
        return self.criadas + self.ja_existiam + self.reativadas

    @property
    def mensagem(self) -> str:
        partes = []
        if self.criadas:
            partes.append(f'{self.criadas} variante(s) criada(s)')
        if self.reativadas:
            partes.append(f'{self.reativadas} reativada(s)')
        if self.ja_existiam:
            partes.append(f'{self.ja_existiam} já existia(m)')
        return '; '.join(partes) if partes else 'Nada a gerar.'


def montar_sku(codigo_produto: str, sigla_cor: str, sigla_tamanho: str) -> str:
    """CODIGO-COR-TAMANHO, em maiúsculas e sem espaços."""
    partes = [
        (codigo_produto or '').strip().upper().replace(' ', ''),
        (sigla_cor or '').strip().upper().replace(' ', ''),
        (sigla_tamanho or '').strip().upper().replace(' ', ''),
    ]
    return SEPARADOR.join(p for p in partes if p)


class VarianteService:

    @staticmethod
    @transaction.atomic
    def gerar(produto) -> ResultadoGeracao:
        """
        Cria as variantes que faltam para o produto.

        Não apaga nada: cor removida do produto ou tamanho tirado da grade
        deixa a variante existente intocada — desativá-la é decisão de quem
        opera, porque pode haver estoque ou produção em andamento nela.
        """
        if produto.grade_id is None:
            raise DadosInvalidosError(
                'Defina a grade do produto antes de gerar as variantes — '
                'é ela que diz quais tamanhos existem.'
            )

        cores = list(produto.cores.filter(ativo=True).select_related('cor'))
        if not cores:
            raise DadosInvalidosError(
                'Cadastre ao menos uma cor no produto antes de gerar as variantes.'
            )

        tamanhos = produto.grade.tamanhos_ordenados()
        if not tamanhos:
            raise DadosInvalidosError(
                f'A grade "{produto.grade.nome}" não tem nenhum tamanho.'
            )

        # Uma consulta só, em vez de um get_or_create por cruzamento: com 6
        # cores × 7 tamanhos seriam 42 idas ao banco.
        existentes = {
            (v.produto_cor_id, v.tamanho_id): v
            for v in produto.variantes.all()
        }

        novas = []
        criadas = reativadas = ja_existiam = 0

        for produto_cor in cores:
            for tamanho in tamanhos:
                chave = (produto_cor.id, tamanho.id)
                atual = existentes.get(chave)
                if atual is not None:
                    if not atual.ativo:
                        atual.ativo = True
                        atual.save(update_fields=['ativo'])
                        reativadas += 1
                    else:
                        ja_existiam += 1
                    continue

                novas.append(Variante(
                    produto=produto,
                    produto_cor=produto_cor,
                    tamanho=tamanho,
                    sku=montar_sku(produto.codigo, produto_cor.cor.sigla, tamanho.sigla),
                ))
                criadas += 1

        if novas:
            Variante.objects.bulk_create(novas)

        return ResultadoGeracao(
            criadas=criadas, ja_existiam=ja_existiam, reativadas=reativadas,
        )

    @staticmethod
    def previa(produto) -> list[dict]:
        """
        O que a geração produziria, sem gravar — para a tela mostrar antes
        de confirmar. Marca o que já existe, para ninguém achar que vai
        recriar SKU em uso.
        """
        if produto.grade_id is None:
            return []

        cores = list(produto.cores.filter(ativo=True).select_related('cor'))
        tamanhos = produto.grade.tamanhos_ordenados()
        existentes = {
            (v.produto_cor_id, v.tamanho_id) for v in produto.variantes.all()
        }

        return [
            {
                'cor': pc.cor.nome,
                'tamanho': t.sigla,
                'sku': montar_sku(produto.codigo, pc.cor.sigla, t.sigla),
                'ja_existe': (pc.id, t.id) in existentes,
            }
            for pc in cores
            for t in tamanhos
        ]
