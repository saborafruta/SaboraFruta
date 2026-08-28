"""
Os primeiros passos da viagem: o que precisa estar pronto antes do caminhão.

O ERRO SEMPRE APARECE NA PIOR HORA

Falta de natureza cadastrada não atrapalha ninguém enquanto a carga está sendo
montada. Ela aparece na doca, com o caminhão encostado e a mercadoria já
baixada do estoque, no clique de emitir a nota — e aí a correção passa por
outro módulo, outro usuário e outra permissão. Esta tela antecipa esse
encontro para um momento em que ele custa cinco minutos.

CADA CHECAGEM PERGUNTA A QUEM DECIDE

A conferência não reimplementa regra nenhuma: ela chama o mesmo código que
vai recusar a emissão depois. Uma segunda versão da regra aqui diria "tudo
pronto" no dia em que a primeira mudasse — e essa é a pior mentira que uma
tela de conferência pode contar.

OBRIGATÓRIO E RECOMENDADO SÃO COISAS DIFERENTES

Sem natureza de venda fora a nota não sai; sem motorista cadastrado ela sai
igual, só dá mais trabalho. Misturar os dois num "pendências" só ensinaria a
ignorar a lista.

A LISTA SOME QUANDO NÃO É MAIS NECESSÁRIA

Depois que a filial já rodou viagens e o essencial está pronto, insistir com
o checklist vira ruído. Ele continua acessível pelo menu — o que acaba é a
cobrança.
"""
from __future__ import annotations

from apps.core.services.exceptions import DadosInvalidosError
from apps.fiscal.models import NaturezaOperacao
from apps.logistica.models import Viagem

E = NaturezaOperacao.Especie

OBRIGATORIO = 'obrigatorio'
RECOMENDADO = 'recomendado'

# As naturezas que a viagem usa, e o que cada uma ampara. A ordem é a da
# operação: primeiro o que sai, depois o que acontece na rua, por fim o que
# volta.
NATUREZAS = (
    (E.VENDA, 'Venda', 'ampara a mercadoria que já tem dono na doca'),
    (E.REMESSA_VENDA_FORA, 'Remessa para venda fora',
     'ampara a mercadoria que sai sem comprador'),
    (E.VENDA_FORA, 'Venda fora do estabelecimento',
     'a nota da venda feita na rua'),
    (E.BONIFICACAO, 'Bonificação', 'a cortesia, como operação própria'),
    (E.RETORNO_VENDA_FORA, 'Retorno de venda fora',
     'fecha a remessa com o que voltou'),
)


class OnboardingViagemService:

    # ── A conferência ────────────────────────────────────────────────────

    @classmethod
    def checagens(cls, filial) -> list[dict]:
        """Tudo que a primeira viagem vai precisar, conferido de verdade."""
        return [
            *cls._naturezas(filial),
            cls._token(filial),
            cls._pagamento(filial),
            cls._transporte(filial),
            cls._produtos(filial),
        ]

    @classmethod
    def resumo(cls, filial, checagens=None) -> dict:
        """
        Em que pé está a filial — e se ainda vale mostrar o convite.

        O CONVITE É PARA QUEM AINDA NÃO RODOU. Filial com viagens feitas e o
        essencial pronto não precisa de checklist na cara: ele vira ruído, e
        ruído ensina a ignorar o aviso que importa.
        """
        checagens = checagens if checagens is not None else cls.checagens(filial)
        obrigatorias = [c for c in checagens if c['peso'] == OBRIGATORIO]
        faltando = [c for c in obrigatorias if not c['pronto']]
        primeira = not Viagem.objects.filter(filial=filial).exists()
        return {
            'total': len(obrigatorias),
            'prontas': len(obrigatorias) - len(faltando),
            'faltando': faltando,
            'pronto': not faltando,
            'primeira_viagem': primeira,
            'mostrar_convite': bool(faltando) or primeira,
        }

    # ── Cada checagem ────────────────────────────────────────────────────

    @staticmethod
    def _naturezas(filial) -> list[dict]:
        """
        Uma natureza ativa por espécie — nem nenhuma, nem duas.

        QUEM RESPONDE É O EMISSOR. `VendaForaNFeService.natureza` já recusa
        emitir sem natureza e com natureza ambígua; aqui só se pergunta a ele
        antes, para a resposta não chegar com o caminhão carregado.
        """
        from apps.logistica.services.venda_fora_nfe import VendaForaNFeService

        linhas = []
        for especie, nome, para_que in NATUREZAS:
            try:
                natureza = VendaForaNFeService.natureza_da_especie(filial, especie)
                pronto, detalhe = True, f'{natureza.codigo} — {natureza.descricao}'
            except DadosInvalidosError as erro:
                pronto, detalhe = False, str(erro)

            if pronto and not natureza.regras.exists():
                # NATUREZA SEM REGRA NAO TEM CFOP. Ela existe, o cadastro
                # parece feito, e a emissao para do mesmo jeito -- com uma
                # mensagem que fala de regra, nao de natureza.
                pronto = False
                detalhe = (
                    'Natureza cadastrada, mas sem nenhuma regra: é a regra que '
                    'traz o CFOP e a tributação.'
                )

            linhas.append({
                'chave': f'natureza-{especie}',
                'grupo': 'Naturezas de operação',
                'titulo': nome,
                'para_que': para_que,
                'peso': OBRIGATORIO,
                'pronto': pronto,
                'detalhe': detalhe,
                'rota': 'fiscal:natureza-list',
                'acao': 'Abrir naturezas de operação',
            })
        return linhas

    @staticmethod
    def _token(filial) -> dict:
        token = (getattr(filial, 'focusnfe_token', '') or '').strip()
        ambiente = getattr(filial, 'focusnfe_ambiente', None)
        return {
            'chave': 'focus',
            'grupo': 'Transmissão à SEFAZ',
            'titulo': 'Token da Focus NFe',
            'para_que': 'sem ele a nota é emitida e não chega à SEFAZ',
            'peso': OBRIGATORIO,
            'pronto': bool(token),
            'detalhe': (
                f'Configurado · ambiente {"produção" if ambiente == 1 else "homologação"}'
                if token else
                'A filial está sem token. As notas ficariam emitidas e paradas.'
            ),
            'rota': 'core:admin_parametros',
            'acao': 'Abrir parâmetros fiscais',
        }

    @staticmethod
    def _pagamento(filial) -> dict:
        """
        A forma de pagamento é quem decide se a venda na rua vira cobrança.

        SEM NENHUMA FORMA A PRAZO, toda venda da rua é tratada como recebida
        na entrega — e ninguém percebe que o contas a receber nunca recebe
        nada da viagem.
        """
        from apps.financeiro.models.formas_pagamento import (
            CondicaoPagamento, FormaPagamento,
        )

        formas = FormaPagamento.objects.filter(empresa=filial.empresa, ativo=True)
        a_prazo = formas.filter(gera_parcelas=True).count()
        a_vista = formas.filter(gera_parcelas=False).count()
        condicoes = CondicaoPagamento.objects.filter(
            empresa=filial.empresa, ativo=True,
        ).count()

        pronto = bool(a_prazo and a_vista)
        if pronto:
            detalhe = (
                f'{a_vista} forma(s) à vista, {a_prazo} a prazo e '
                f'{condicoes} condição(ões) de parcelamento.'
            )
        elif not formas.exists():
            detalhe = 'Nenhuma forma de pagamento ativa — a venda na rua não teria como ser cobrada.'
        elif not a_prazo:
            detalhe = (
                'Nenhuma forma que gera parcelas: toda venda da rua seria tratada '
                'como recebida na entrega, e nada iria para o contas a receber.'
            )
        else:
            detalhe = 'Nenhuma forma à vista — o dinheiro recebido na entrega não teria onde ser lançado.'

        return {
            'chave': 'pagamento',
            'grupo': 'Dinheiro da rua',
            'titulo': 'Formas e condições de pagamento',
            'para_que': 'decidem se a venda na rua abre contas a receber',
            'peso': OBRIGATORIO,
            'pronto': pronto,
            'detalhe': detalhe,
            'rota': 'financeiro:formas_pagamento',
            'acao': 'Abrir formas de pagamento',
        }

    @staticmethod
    def _transporte(filial) -> dict:
        """
        Motorista e veículo cadastrados são conveniência, e não trava.

        A viagem aceita nome e placa digitados — é assim que ela funciona no
        dia em que o motorista de sempre falta. Com cadastro, o MDF-e já sai
        preenchido e a placa do manifesto não diverge da que está no pátio.
        """
        from apps.cadastros.models import Motorista, Veiculo

        motoristas = Motorista.objects.for_filial(filial).filter(ativo=True).count()
        veiculos = Veiculo.objects.for_filial(filial).filter(ativo=True).count()
        return {
            'chave': 'transporte',
            'grupo': 'Transporte',
            'titulo': 'Motoristas e veículos',
            'para_que': 'preenchem o MDF-e sozinhos e evitam placa divergente',
            'peso': RECOMENDADO,
            'pronto': bool(motoristas and veiculos),
            'detalhe': (
                f'{motoristas} motorista(s) e {veiculos} veículo(s) ativos.'
                if motoristas and veiculos else
                'Dá para digitar nome e placa na viagem, mas o MDF-e virá em branco.'
            ),
            'rota': 'cadastros:veiculo-list',
            'acao': 'Abrir veículos',
        }

    @staticmethod
    def _produtos(filial) -> dict:
        """
        NCM é o campo que a SEFAZ recusa quando falta.

        A CONFERÊNCIA É POR AMOSTRA DO CATÁLOGO ATIVO, e não item a item da
        carga: aqui ainda não há carga. O objetivo é descobrir agora que
        metade do cadastro sairia rejeitada.
        """
        from apps.produtos.models import Produto

        ativos = Produto.objects.for_filial(filial).filter(ativo=True)
        total = ativos.count()
        sem_ncm = ativos.filter(ncm='').count()
        return {
            'chave': 'produtos',
            'grupo': 'Produtos',
            'titulo': 'NCM preenchido',
            'para_que': 'a SEFAZ rejeita item sem NCM',
            'peso': OBRIGATORIO if total else RECOMENDADO,
            'pronto': bool(total) and not sem_ncm,
            'detalhe': (
                f'{total} produto(s) ativos, todos com NCM.'
                if total and not sem_ncm else
                f'{sem_ncm} de {total} produto(s) ativos estão sem NCM.'
                if total else
                'Nenhum produto ativo nesta filial.'
            ),
            'rota': 'produtos:produto-list',
            'acao': 'Abrir produtos',
        }
