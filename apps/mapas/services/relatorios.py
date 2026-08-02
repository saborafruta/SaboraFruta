"""
Relatórios do módulo de mapas (imprimíveis / PDF).

Três perguntas que o mapa mostra mas não deixa levar para uma reunião:

1. **Cobertura** — quem está fora do mapa e por quê. A tela diz "13 sem
   coordenada"; o relatório diz *quais* e com que endereço, que é o que
   permite corrigir.
2. **Vendas por região** — o mapa de calor vira tabela, com as quatro métricas
   lado a lado. No mapa você escolhe uma métrica por vez; num relatório
   comparar receita com número de clientes na mesma linha é o que revela
   bairro de muita venda e pouco cliente (ou o contrário).
3. **Rotas** — o que foi planejado no período e quanto a otimização poupou.

As métricas reaproveitam `HeatmapService`: se este módulo recalculasse por
conta própria, o relatório e o mapa acabariam divergindo para o mesmo período.
"""
from __future__ import annotations

from decimal import Decimal

from apps.mapas.services.heatmap import ZONAS, HeatmapService

AGRUPAMENTOS = {
    'cidade': 'Cidade',
    'bairro': 'Bairro',
    'zona':   'Zona',
    'uf':     'Estado',
}


class RelatorioRegiaoService:
    """Vendas agregadas por cidade, bairro, zona ou estado."""

    @classmethod
    def gerar(cls, filial, *, agrupar_por='cidade', inicio=None, fim=None,
              cidade='', uf='', filial_id=None, representante_id=None):
        """
        Uma linha por região, com as quatro métricas juntas.

        Diferente do mapa, que pinta uma métrica por vez: aqui as quatro
        aparecem lado a lado, que é o que deixa ver um bairro com muita
        receita e poucos clientes — ou o contrário.
        """
        if agrupar_por not in AGRUPAMENTOS:
            agrupar_por = 'cidade'

        filiais = HeatmapService._escopo_filiais(filial, filial_id)
        inicio, fim = HeatmapService._periodo(inicio, fim)
        clientes = HeatmapService._clientes(filiais, cidade, uf)

        valores = {
            m: cls._por_cliente(filiais, inicio, fim, representante_id, m)
            for m in ('receita', 'pedidos', 'volume')
        }

        centro = HeatmapService._centro_da_base(filiais, cidade, uf)
        campos = ('id', 'cidade', 'bairro', 'uf', 'latitude', 'longitude')

        linhas = {}
        for cid, cid_cidade, bairro, cid_uf, lat, lng in clientes.values_list(*campos):
            receita = valores['receita'].get(cid, Decimal('0'))
            pedidos = valores['pedidos'].get(cid, Decimal('0'))
            volume = valores['volume'].get(cid, Decimal('0'))
            # Cliente sem venda no período não pertence a nenhuma linha: ele
            # infla a contagem de clientes de um bairro que não vendeu nada.
            if not (receita or pedidos or volume):
                continue

            chave = cls._chave(agrupar_por, cid_cidade, bairro, cid_uf,
                               lat, lng, centro)
            linha = linhas.setdefault(chave, {
                'regiao': chave, 'clientes': 0,
                'pedidos': Decimal('0'), 'volume': Decimal('0'),
                'receita': Decimal('0'),
            })
            linha['clientes'] += 1
            linha['pedidos'] += pedidos
            linha['volume'] += volume
            linha['receita'] += receita

        ordenadas = sorted(linhas.values(), key=lambda r: r['receita'], reverse=True)
        total = {
            'clientes': sum(r['clientes'] for r in ordenadas),
            'pedidos': sum((r['pedidos'] for r in ordenadas), Decimal('0')),
            'volume': sum((r['volume'] for r in ordenadas), Decimal('0')),
            'receita': sum((r['receita'] for r in ordenadas), Decimal('0')),
        }

        # Participação de cada região no faturamento — a coluna que ordena a
        # conversa numa reunião.
        for r in ordenadas:
            r['participacao'] = (
                round(float(r['receita'] / total['receita'] * 100), 1)
                if total['receita'] else 0.0
            )

        return {
            'agrupar_por': agrupar_por,
            'rotulo_grupo': AGRUPAMENTOS[agrupar_por],
            'inicio': inicio, 'fim': fim,
            'linhas': ordenadas,
            'total': total,
        }

    @staticmethod
    def _por_cliente(filiais, inicio, fim, representante_id, metrica):
        """Soma B2B + PDV por cliente, com a mesma regra do mapa de calor."""
        b2b = HeatmapService._pedidos_b2b(
            filiais, inicio, fim, representante_id, metrica)
        pdv = HeatmapService._vendas_pdv(
            filiais, inicio, fim, representante_id, metrica)

        junto = dict(b2b)
        for cid, v in pdv.items():
            junto[cid] = junto.get(cid, Decimal('0')) + v
        return junto

    @staticmethod
    def _chave(agrupar_por, cidade, bairro, uf, lat, lng, centro):
        """Nome da região de um cliente, conforme o agrupamento escolhido."""
        if agrupar_por == 'cidade':
            return cidade or '(sem cidade)'
        if agrupar_por == 'bairro':
            return bairro or '(sem bairro)'
        if agrupar_por == 'uf':
            return uf or '(sem estado)'

        # Zona: mesmo critério de cunhas do mapa de calor. Repetir a regra em
        # SQL aqui faria o relatório e o mapa poderem discordar.
        centro_lat, centro_lng = centro
        if centro_lat is None or lat is None:
            return '(sem coordenada)'
        dlat, dlng = lat - centro_lat, lng - centro_lng
        if abs(dlat) >= abs(dlng):
            return ZONAS['norte'] if dlat > 0 else ZONAS['sul']
        return ZONAS['leste'] if dlng > 0 else ZONAS['oeste']


class RelatorioCoberturaService:
    """Quem está fora do mapa, e por quê."""

    @classmethod
    def gerar(cls, filial, *, cidade='', uf=''):
        """
        A tela diz "13 sem coordenada". Este relatório diz **quais**.

        Traz o endereço de cada um e o erro registrado pelo geocodificador —
        sem isso não dá para saber se o problema é endereço incompleto,
        cidade errada ou um CEP que o provider não conhece.
        """
        from apps.cadastros.models import Cliente

        filiais = HeatmapService._escopo_filiais(filial)
        base = Cliente.objects.filter(filial__in=filiais, ativo=True)
        if cidade:
            base = base.filter(cidade__iexact=cidade)
        if uf:
            base = base.filter(uf__iexact=uf)

        sem = base.filter(latitude__isnull=True).order_by('cidade', 'razao_social')
        total = base.count()
        com = total - sem.count()

        pendentes = [
            {
                'id': c.pk,
                'nome': c.nome_fantasia or c.razao_social or f'Cliente {c.pk}',
                'endereco': cls._endereco(c),
                'cidade': c.cidade or '',
                'uf': c.uf or '',
                'erro': c.geo_erro or '',
            }
            for c in sem.only(
                'id', 'razao_social', 'nome_fantasia', 'endereco', 'numero',
                'bairro', 'cidade', 'uf', 'cep', 'geo_erro',
            )
        ]

        # Cidades com mais pendências primeiro: é por onde compensa começar.
        por_cidade = {}
        for p in pendentes:
            chave = p['cidade'] or '(sem cidade)'
            por_cidade[chave] = por_cidade.get(chave, 0) + 1

        return {
            'total': total,
            'com_coordenada': com,
            'sem_coordenada': len(pendentes),
            'percentual': round(com / total * 100, 1) if total else 0.0,
            'pendentes': pendentes,
            'por_cidade': sorted(
                ({'cidade': k, 'qtd': v} for k, v in por_cidade.items()),
                key=lambda r: r['qtd'], reverse=True,
            ),
        }

    @staticmethod
    def _endereco(c):
        partes = [c.endereco or '']
        if c.numero:
            partes.append(str(c.numero))
        if c.bairro:
            partes.append(c.bairro)
        if c.cep:
            partes.append(f'CEP {c.cep}')
        return ', '.join(p for p in partes if p)


class RelatorioRotasService:
    """Rotas montadas no período e o que a otimização poupou."""

    @classmethod
    def gerar(cls, filial, *, inicio=None, fim=None):
        from apps.mapas.models import RegistroRota
        from apps.mapas.services.painel import PainelService

        filiais = HeatmapService._escopo_filiais(filial)
        inicio, fim = PainelService.periodo_padrao(inicio, fim)

        qs = (
            RegistroRota.objects
            .filter(filial__in=filiais,
                    created_at__date__gte=inicio, created_at__date__lte=fim)
            .select_related('usuario', 'filial')
            .order_by('-created_at')
        )

        linhas = [
            {
                'quando': r.created_at,
                'usuario': getattr(r.usuario, 'nome', '') or '—',
                'paradas': r.paradas,
                'km': round(r.distancia_m / 1000, 1),
                'tempo': PainelService.formatar_duracao(r.duracao_s),
                'otimizada': r.otimizada,
                'economia_km': round(r.economia_m / 1000, 1),
                'provider': r.provider or '—',
            }
            for r in qs[:500]
        ]

        return {
            'inicio': inicio, 'fim': fim,
            'linhas': linhas,
            'total_rotas': len(linhas),
            'total_km': round(sum(l['km'] for l in linhas), 1),
            'total_economia_km': round(sum(l['economia_km'] for l in linhas), 1),
            'total_paradas': sum(l['paradas'] for l in linhas),
        }
