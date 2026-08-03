"""
Roteiro sugerido e relatório consolidado de mapas.

Fica separado de `relatorios.py` porque responde outra pergunta: lá é
"como foi", aqui é "o que fazer agora" — e o roteiro depende do CRM e do
otimizador, que os relatórios de conferência não precisam conhecer.
"""
from __future__ import annotations

from decimal import Decimal

from apps.mapas.services.heatmap import HeatmapService
from apps.mapas.services.relatorios import (
    RelatorioCoberturaService,
    RelatorioRegiaoService,
)


class RoteiroSugeridoService:
    """
    Sugestão de roteiro a partir das vendas já realizadas.

    Duas perguntas diferentes, respondidas em sequência:

    1. **Quem visitar** — não é quem comprou mais, é quem vale a visita agora.
       O CRM já calcula isso (`RecompraCliente.score`, que combina atraso,
       ticket e regularidade). Quem ainda não tem padrão entra pela receita do
       período, para nenhum cliente ficar de fora só por ser recente.
    2. **Em que ordem** — aí sim é geografia: a lista escolhida no passo 1 é
       reordenada pelo otimizador (§5).

    Misturar as duas num critério só daria um roteiro que ou anda muito para
    vender pouco, ou visita o vizinho errado.
    """

    # Teto do roteirizador. Um roteiro maior que isso não cabe num dia de rua.
    MAX_PARADAS = 25

    @classmethod
    def gerar(cls, filial, *, inicio=None, fim=None, cidade='', uf='',
              bairro='', zona='', limite=MAX_PARADAS):
        filiais = HeatmapService._escopo_filiais(filial)
        inicio, fim = HeatmapService._periodo(inicio, fim)

        receita = RelatorioRegiaoService._por_cliente(
            filiais, inicio, fim, None, 'receita')
        comprou = [cid for cid, v in receita.items() if v > 0]
        if not comprou:
            return cls._vazio(inicio, fim, 'Nenhuma venda no período.')

        clientes = list(
            HeatmapService._clientes(filiais, cidade, uf, bairro, zona)
            .filter(pk__in=comprou)
            .only('id', 'razao_social', 'nome_fantasia', 'endereco', 'numero',
                  'bairro', 'cidade', 'uf', 'telefone', 'celular',
                  'latitude', 'longitude')
        )
        if not clientes:
            return cls._vazio(
                inicio, fim,
                'Nenhum cliente com venda e coordenada neste recorte.')

        recompra = cls._recompra(clientes, filiais)
        escolhidos = cls._priorizar(clientes, receita, recompra, limite)
        ordenados, rota = cls._ordenar(filial, escolhidos)

        return {
            'inicio': inicio, 'fim': fim,
            'paradas': [
                cls._parada(i, c, receita, recompra)
                for i, c in enumerate(ordenados, start=1)
            ],
            'candidatos': len(clientes),
            'limite': limite,
            'km': rota.get('km'),
            'duracao': rota.get('duracao'),
            'ordem_por': rota.get('ordem_por', ''),
            'motivo': '',
        }

    @staticmethod
    def _vazio(inicio, fim, motivo):
        return {
            'inicio': inicio, 'fim': fim, 'paradas': [], 'candidatos': 0,
            'limite': 0, 'km': None, 'duracao': None, 'ordem_por': '',
            'motivo': motivo,
        }

    @staticmethod
    def _recompra(clientes, filiais):
        """Indicadores do CRM por cliente, numa query só."""
        try:
            from apps.crm.models import RecompraCliente

            return {
                r.cliente_id: r
                for r in RecompraCliente.objects.filter(
                    cliente_id__in=[c.pk for c in clientes], filial__in=filiais)
            }
        except Exception:  # pragma: no cover - CRM é opcional aqui
            return {}

    @classmethod
    def _priorizar(cls, clientes, receita, recompra, limite):
        """
        Ordena por quem vale a visita e corta no limite.

        O score do CRM manda; sem ele, a receita desempata. Cliente em atraso
        sobe ao topo: é a visita que evita a perda, que é o motivo de sair com
        um roteiro em vez de esperar o pedido chegar.
        """
        def chave(c):
            r = recompra.get(c.pk)
            atrasado = 1 if (r and r.status == 'vermelho') else 0
            score = r.score if r else 0
            return (atrasado, score, float(receita.get(c.pk, 0)))

        return sorted(clientes, key=chave, reverse=True)[:limite]

    @classmethod
    def _ordenar(cls, filial, clientes):
        """
        Reordena geograficamente. Nunca falha: cai no otimizador local.

        O otimizador de rua depende de um provider externo, e um relatório não
        pode sair em branco porque o servidor de rotas piscou. Sem ele a ordem
        sai por distância em linha reta — pior, mas utilizável. A tela diz
        qual dos dois foi usado, senão o número de km pareceria equivalente.
        """
        from apps.mapas.services.otimizacao import (
            OtimizacaoService, distancia_haversine_m, otimizar_local,
        )

        if len(clientes) < 2:
            return clientes, {'ordem_por': 'parada única'}

        por_id = {c.pk: c for c in clientes}
        try:
            resultado = OtimizacaoService().otimizar(
                filial=filial, cliente_ids=list(por_id), partir_da_filial=True)
            if resultado.ok and resultado.ordem_depois:
                ordenados = [por_id[i] for i in resultado.ordem_depois if i in por_id]
                if len(ordenados) == len(clientes):
                    return ordenados, {
                        'km': resultado.rota_depois.distancia_km,
                        'duracao': resultado.rota_depois.duracao_texto,
                        'ordem_por': 'rota por rua',
                    }
        except Exception:
            pass

        pontos = [(c.latitude, c.longitude) for c in clientes]
        indices = otimizar_local(pontos, fixar_primeiro=False)
        ordenados = [clientes[i] for i in indices]
        metros = sum(
            distancia_haversine_m(
                (ordenados[i - 1].latitude, ordenados[i - 1].longitude),
                (ordenados[i].latitude, ordenados[i].longitude))
            for i in range(1, len(ordenados))
        )
        return ordenados, {
            'km': round(metros / 1000, 1),
            'ordem_por': 'proximidade em linha reta',
        }

    @staticmethod
    def _parada(ordem, cliente, receita, recompra):
        r = recompra.get(cliente.pk)
        endereco = ', '.join(p for p in [
            cliente.endereco or '', str(cliente.numero or ''),
            cliente.bairro or '',
        ] if p)

        return {
            'ordem': ordem,
            'id': cliente.pk,
            'nome': (cliente.nome_fantasia or cliente.razao_social
                     or f'Cliente {cliente.pk}'),
            'endereco': endereco or '—',
            'cidade': cliente.cidade or '',
            'telefone': cliente.celular or cliente.telefone or '',
            'receita': receita.get(cliente.pk, Decimal('0')),
            'ultima_compra': r.ultima_compra if r else None,
            'dias_sem_comprar': getattr(r, 'dias_desde_ultima_compra', None),
            'frequencia': r.get_frequencia_display() if r else '',
            'atrasado': bool(r and r.status == 'vermelho'),
            'valor_medio': r.valor_medio if r else None,
        }


class RelatorioCompletoService:
    """
    Um documento com tudo: faturamento por zona e por bairro, os clientes de
    cada zona, quem está sem endereço e o roteiro sugerido.

    Separado dos relatórios individuais porque a pergunta é outra: os de uma
    seção só servem para conferir um número; este serve para levar a operação
    inteira para uma reunião — ou para a rua — numa impressão só.
    """

    @classmethod
    def gerar(cls, filial, *, inicio=None, fim=None, cidade='', uf='',
              zona='', limite_roteiro=RoteiroSugeridoService.MAX_PARADAS):
        filiais = HeatmapService._escopo_filiais(filial)
        inicio, fim = HeatmapService._periodo(inicio, fim)

        return {
            'inicio': inicio, 'fim': fim,
            'zona': zona, 'cidade': cidade, 'uf': uf,
            'por_zona': RelatorioRegiaoService.gerar(
                filial, agrupar_por='zona', inicio=inicio, fim=fim,
                cidade=cidade, uf=uf),
            'por_bairro': RelatorioRegiaoService.gerar(
                filial, agrupar_por='bairro', inicio=inicio, fim=fim,
                cidade=cidade, uf=uf),
            'clientes_por_zona': cls._clientes_por_zona(
                filiais, inicio, fim, cidade, uf, zona),
            'cobertura': RelatorioCoberturaService.gerar(
                filial, cidade=cidade, uf=uf),
            'roteiro': RoteiroSugeridoService.gerar(
                filial, inicio=inicio, fim=fim, cidade=cidade, uf=uf,
                zona=zona, limite=limite_roteiro),
        }

    @classmethod
    def _clientes_por_zona(cls, filiais, inicio, fim, cidade, uf, zona):
        """
        Quem são os clientes de cada zona — não só quantos.

        O relatório por região responde "a Zona Sul faturou X"; esta seção
        responde "e são estes os clientes", que é o que permite distribuir a
        carteira ou perceber alguém no lugar errado.

        Aqui entra a base toda do recorte, inclusive quem não comprou no
        período: para conferir cobertura de carteira, quem não comprou é
        justamente a informação que interessa.
        """
        receita = RelatorioRegiaoService._por_cliente(
            filiais, inicio, fim, None, 'receita')
        centro = HeatmapService._centro_da_base(filiais, cidade, uf)

        qs = HeatmapService._clientes(filiais, cidade, uf, zona=zona).only(
            'id', 'razao_social', 'nome_fantasia', 'bairro', 'cidade', 'uf',
            'telefone', 'celular', 'latitude', 'longitude')

        grupos = {}
        for c in qs:
            chave = RelatorioRegiaoService._chave(
                'zona', c.cidade, c.bairro, c.uf, c.latitude, c.longitude, centro)
            grupos.setdefault(chave, []).append({
                'id': c.pk,
                'nome': (c.nome_fantasia or c.razao_social or f'Cliente {c.pk}'),
                'bairro': c.bairro or '—',
                'cidade': c.cidade or '',
                'telefone': c.celular or c.telefone or '',
                'receita': receita.get(c.pk, Decimal('0')),
            })

        saida = []
        for nome, clientes in grupos.items():
            # Dentro da zona, quem mais fatura primeiro.
            clientes.sort(key=lambda x: x['receita'], reverse=True)
            saida.append({
                'zona': nome,
                'clientes': clientes,
                'total': sum((c['receita'] for c in clientes), Decimal('0')),
                'quantidade': len(clientes),
                'sem_compra': sum(1 for c in clientes if not c['receita']),
            })
        # A zona de maior faturamento abre a lista.
        saida.sort(key=lambda g: g['total'], reverse=True)
        return saida
