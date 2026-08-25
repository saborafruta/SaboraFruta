"""
O estoque de produto acabado: onde cada lote está e quando ele vence.

A ENTRADA JÁ ACONTECE. Encerrar a ordem cria o lote e dá entrada no estoque
pelo serviço do ERP (seção 3), com fabricação, validade, quantidade e custo.
O que faltava era o resto da pergunta: EM QUE CÂMARA e EM QUE ENDEREÇO — sem
isso, quem separa um pedido às 4h da manhã procura, e cada minuto de porta
de câmara aberta é temperatura subindo em tudo que está lá dentro.

FEFO NÃO É PREFERÊNCIA, É A ORDEM DA LISTA. O que vence primeiro sai
primeiro; ordenar por qualquer outra coisa aqui seria oferecer o lote errado
para quem está separando. O ERP já sai por FEFO no consumo — a tela precisa
mostrar na mesma ordem, senão a pessoa vê uma coisa e o sistema faz outra.

O QUE VENCEU CONTINUA NA LISTA, em vermelho. Sumir com ele seria esconder
justamente o que precisa de decisão: alguém tem de bloquear, descartar ou
reprocessar, e lote vencido invisível é lote vencido que sai na carga.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.estoque.models import LoteProduto
from apps.polpa.models import Camara, FichaProduto, LoteArmazenado

ZERO = Decimal('0')

# Quantos dias antes do vencimento o lote já pede decisão. É a janela em que
# ainda dá para vender com desconto ou reprocessar — depois dela, sobra o
# descarte.
JANELA_ALERTA = 30


class ArmazenagemService:

    # ── Guardar ──────────────────────────────────────────────────────────

    @staticmethod
    @transaction.atomic
    def guardar(lote: LoteProduto, camara: Camara, dados: dict | None = None):
        """
        Diz em que câmara e endereço o lote está.

        IDEMPOTENTE: guardar de novo MOVE o lote, não cria um segundo
        registro. Um lote em duas câmaras ao mesmo tempo é a informação que
        faz a conferência não fechar.
        """
        dados = dados or {}
        if lote.filial_id != camara.filial_id:
            raise DomainError(
                'A câmara é de outra filial — o lote não pode ser guardado nela.'
            )

        armazenado, _ = LoteArmazenado.objects.update_or_create(
            lote=lote,
            defaults={
                'filial': lote.filial,
                'camara': camara,
                'endereco': (dados.get('endereco') or '').strip()[:40],
                'temperatura_entrada': dados.get('temperatura_entrada'),
                'observacao': dados.get('observacao') or '',
            },
        )
        return armazenado

    @staticmethod
    def guardar_da_ordem(op, camara: Camara | None, dados: dict | None = None):
        """
        Guarda o lote que a ordem acabou de produzir.

        SEM CÂMARA ESCOLHIDA NÃO INVENTA UMA. Jogar o lote na primeira
        câmara ativa daria um endereço errado com cara de certo — e o erro
        só apareceria na separação, com o cliente esperando. Sem escolha, o
        lote fica sem localização e aparece na lista de "sem endereço".
        """
        lote = getattr(op, 'lote', None)
        if lote is None or camara is None:
            return None
        return ArmazenagemService.guardar(lote, camara, dados)

    # ── Leitura ──────────────────────────────────────────────────────────

    @classmethod
    def estoque(cls, filial, filtros: dict | None = None) -> list[dict]:
        """
        Os lotes de produto acabado que ainda têm saldo, em ordem FEFO.

        O SALDO É DO LOTE DO ERP. Aqui só se acrescenta onde ele está e
        quanto falta para vencer — a quantidade continua sendo uma verdade
        só, a dele.
        """
        filtros = filtros or {}
        hoje = timezone.localdate()

        acabados = set(
            FichaProduto.objects.for_filial(filial)
            .filter(classe=FichaProduto.Classe.ACABADO)
            .values_list('produto_id', flat=True)
        )

        lotes = (
            LoteProduto.objects.filter(filial=filial, produto_id__in=acabados)
            .exclude(status=LoteProduto.Status.ESGOTADO)
            .select_related(
                'produto', 'produto__unidade_medida',
                'armazenamento_polpa', 'armazenamento_polpa__camara',
            )
        )
        if filtros.get('produto'):
            lotes = lotes.filter(produto_id=filtros['produto'])
        if filtros.get('busca'):
            from django.db.models import Q
            termo = filtros['busca']
            lotes = lotes.filter(
                Q(numero_lote__icontains=termo)
                | Q(produto__descricao__icontains=termo)
            )

        linhas = []
        for lote in lotes:
            if (lote.quantidade_atual or ZERO) <= ZERO and not filtros.get('zerados'):
                continue

            armazenado = getattr(lote, 'armazenamento_polpa', None)
            if filtros.get('camara'):
                if not armazenado or str(armazenado.camara_id) != str(filtros['camara']):
                    continue
            if filtros.get('sem_endereco') and armazenado:
                continue

            dias = (
                (lote.data_validade - hoje).days if lote.data_validade else None
            )
            situacao = cls._situacao(dias)
            if filtros.get('situacao') and situacao != filtros['situacao']:
                continue

            linhas.append({
                'lote': lote,
                'armazenado': armazenado,
                'camara': armazenado.camara if armazenado else None,
                'endereco': armazenado.endereco if armazenado else '',
                'peso': armazenado.peso if armazenado else cls._peso(lote),
                'dias': dias,
                'situacao': situacao,
                'fora_da_faixa': armazenado.fora_da_faixa if armazenado else False,
            })

        # FEFO: o que vence primeiro vem primeiro. Lote sem validade vai
        # para o fim -- não é "vence nunca", é "ninguém informou", e misturá-lo
        # com os novos faria a ausência parecer folga.
        linhas.sort(key=lambda l: (l['dias'] is None, l['dias'] or 0))
        return linhas

    @staticmethod
    def _peso(lote) -> Decimal | None:
        unitario = getattr(lote.produto, 'peso_liquido', None)
        if not unitario:
            return None
        return ((lote.quantidade_atual or ZERO) * unitario).quantize(Decimal('0.001'))

    @staticmethod
    def _situacao(dias: int | None) -> str:
        if dias is None:
            return 'sem_validade'
        if dias < 0:
            return 'vencido'
        if dias <= JANELA_ALERTA:
            return 'vencendo'
        return 'ok'

    @classmethod
    def resumo(cls, filial) -> dict:
        """
        O estoque congelado em números — e o que precisa de decisão.

        VENCIDO E VENCENDO SÃO OS NÚMEROS QUE IMPORTAM. Saldo total não faz
        ninguém se mexer; "3 lotes vencem em 30 dias" faz.
        """
        linhas = cls.estoque(filial)
        return {
            'lotes': len(linhas),
            'unidades': sum(
                (l['lote'].quantidade_atual or ZERO for l in linhas), ZERO,
            ),
            'peso': sum((l['peso'] or ZERO for l in linhas), ZERO),
            'vencidos': sum(1 for l in linhas if l['situacao'] == 'vencido'),
            'vencendo': sum(1 for l in linhas if l['situacao'] == 'vencendo'),
            'sem_validade': sum(1 for l in linhas if l['situacao'] == 'sem_validade'),
            'sem_endereco': sum(1 for l in linhas if l['armazenado'] is None),
            'fora_da_faixa': sum(1 for l in linhas if l['fora_da_faixa']),
            'janela': JANELA_ALERTA,
        }

    @classmethod
    def por_camara(cls, filial) -> list[dict]:
        """Quanto tem em cada câmara, contra o que ela aguenta."""
        linhas = cls.estoque(filial)
        camaras = Camara.objects.for_filial(filial).filter(ativo=True)

        peso_por_camara: dict = {}
        for linha in linhas:
            if linha['camara'] is None:
                continue
            peso_por_camara[linha['camara'].pk] = (
                peso_por_camara.get(linha['camara'].pk, ZERO) + (linha['peso'] or ZERO)
            )

        resultado = []
        for camara in camaras:
            peso = peso_por_camara.get(camara.pk, ZERO)
            resultado.append({
                'camara': camara,
                'lotes': sum(
                    1 for l in linhas
                    if l['camara'] and l['camara'].pk == camara.pk
                ),
                'peso': peso,
                'ocupacao': (
                    (peso / camara.capacidade_kg * 100).quantize(Decimal('0.1'))
                    if camara.capacidade_kg else None
                ),
            })
        return resultado

    @staticmethod
    @transaction.atomic
    def bloquear(lote: LoteProduto, motivo: str):
        """
        Tira o lote do jogo sem apagá-lo.

        BLOQUEAR NÃO É EXCLUIR: o lote vencido continua existindo, com saldo
        e história — o que muda é que ele para de ser oferecido na separação.
        Apagar seria perder o rastro justamente do lote que deu problema.
        """
        motivo = (motivo or '').strip()
        if len(motivo) < 5:
            raise DomainError(
                'Escreva o motivo do bloqueio — é o que explica o lote parado '
                'na câmara seis meses depois.'
            )
        lote.status = LoteProduto.Status.BLOQUEADO
        lote.motivo_bloqueio = motivo
        lote.save(update_fields=['status', 'motivo_bloqueio'])
        return lote

    @staticmethod
    def a_vencer(filial, dias: int = JANELA_ALERTA):
        """Os lotes que vencem dentro da janela — a fila do que decidir."""
        limite = timezone.localdate() + timedelta(days=dias)
        return (
            LoteProduto.objects
            .filter(
                filial=filial, data_validade__lte=limite,
                quantidade_atual__gt=0,
            )
            .exclude(status=LoteProduto.Status.ESGOTADO)
            .select_related('produto')
            .order_by('data_validade')
        )
