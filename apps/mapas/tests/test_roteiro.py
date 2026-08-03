"""
Roteiro sugerido e relatorio consolidado.

O ponto do roteiro sao duas decisoes distintas: QUEM visitar (prioridade
comercial) e EM QUE ORDEM (geografia). Um criterio so daria um roteiro que
anda muito para vender pouco, ou visita o vizinho errado -- os testes fixam a
separacao.
"""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

CENTRO = (-5.790, -35.210)


def deslocar(metros_norte):
    return (CENTRO[0] + metros_norte / 111_320, CENTRO[1])


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BaseRoteiro(TestCase):
    _seq = 0

    def setUp(self):
        self.filial = self._empresa('Alfa', '11222333000181')
        self.usuario = self._usuario(self.filial)
        self._logar(self.usuario, self.filial)
        # Sem rede nos testes: o otimizador de rua nunca e chamado de verdade.
        p = patch('apps.mapas.services.otimizacao.OtimizacaoService.otimizar',
                  side_effect=RuntimeError('sem rede'))
        p.start()
        self.addCleanup(p.stop)

    def _empresa(self, nome, cnpj):
        from apps.core.models import Empresa, Filial

        emp = Empresa.objects.create(
            razao_social=nome, cnpj=cnpj,
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        return Filial.objects.create(
            empresa=emp, razao_social=nome, nome_fantasia=nome,
            cnpj=cnpj, uf='RN', is_matriz=True,
        )

    def _usuario(self, filial):
        from apps.core.models import PerfilAcesso, Usuario

        BaseRoteiro._seq += 1
        n = BaseRoteiro._seq
        perfil = PerfilAcesso.objects.create(
            empresa=filial.empresa, nome=f'Perfil {n}', is_admin=True,
        )
        return Usuario.objects.create_user(
            email=f'u{n}@teste.local', nome='U', password='senha-de-teste-123',
            empresa=filial.empresa, perfil=perfil, filial=filial,
        )

    def _logar(self, usuario, filial):
        self.client.force_login(usuario)
        sessao = self.client.session
        sessao['filial_ativa_id'] = filial.pk
        sessao.save()

    def _cliente(self, nome, doc, *, ponto=CENTRO, bairro='Centro',
                 cidade='Natal', filial=None):
        from apps.cadastros.models import Cliente

        return Cliente.objects.create(
            filial=filial or self.filial, razao_social=nome, cpf_cnpj=doc,
            endereco='Rua X', numero='10', bairro=bairro,
            cidade=cidade, uf='RN', celular='84999990000',
            latitude=ponto[0], longitude=ponto[1], ativo=True,
        )

    def _venda(self, cliente, valor, *, filial=None, dias_atras=1):
        from apps.pdv.models import VendaPDV

        BaseRoteiro._seq += 1
        filial = filial or self.filial
        return VendaPDV.objects.create(
            filial=filial, numero_venda=BaseRoteiro._seq, cliente=cliente,
            usuario=self.usuario, status='finalizada',
            valor_total=Decimal(str(valor)),
            data_venda=timezone.now() - datetime.timedelta(days=dias_atras),
        )

    def _recompra(self, cliente, *, status='verde', score=50, dias=None):
        from apps.crm.models import RecompraCliente

        return RecompraCliente.objects.create(
            cliente=cliente, filial=self.filial, status=status, score=score,
            valor_medio=Decimal('100'),
            ultima_compra=timezone.localdate() - datetime.timedelta(days=dias or 5),
        )

    def _roteiro(self, **kw):
        from apps.mapas.services import RoteiroSugeridoService

        return RoteiroSugeridoService.gerar(self.filial, **kw)


class QuemVisitarTests(BaseRoteiro):
    def test_so_entra_quem_comprou_no_periodo(self):
        comprou = self._cliente('COMPROU', '1')
        self._cliente('NAO COMPROU', '2', ponto=deslocar(500))
        self._venda(comprou, 100)

        nomes = [p['nome'] for p in self._roteiro()['paradas']]
        self.assertEqual(nomes, ['COMPROU'])

    def test_cliente_atrasado_vem_antes_de_quem_faturou_mais(self):
        """
        A visita que evita a perda vale mais que a que so confirma um bom
        cliente. Ordenar por receita pura inverteria isso.
        """
        atrasado = self._cliente('ATRASADO', '1')
        bom = self._cliente('BOM PAGADOR', '2', ponto=deslocar(300))
        self._venda(atrasado, 100)
        self._venda(bom, 5000)
        self._recompra(atrasado, status='vermelho', score=40)
        self._recompra(bom, status='verde', score=90)

        d = self._roteiro(limite=1)
        self.assertEqual([p['nome'] for p in d['paradas']], ['ATRASADO'])

    def test_score_do_crm_desempata_entre_nao_atrasados(self):
        a = self._cliente('SCORE BAIXO', '1')
        b = self._cliente('SCORE ALTO', '2', ponto=deslocar(300))
        self._venda(a, 1000)
        self._venda(b, 100)
        self._recompra(a, status='verde', score=10)
        self._recompra(b, status='verde', score=95)

        d = self._roteiro(limite=1)
        self.assertEqual([p['nome'] for p in d['paradas']], ['SCORE ALTO'])

    def test_sem_recompra_entra_pela_receita(self):
        """Cliente novo nao pode ficar de fora so por nao ter padrao ainda."""
        pequeno = self._cliente('PEQUENO', '1')
        grande = self._cliente('GRANDE', '2', ponto=deslocar(300))
        self._venda(pequeno, 50)
        self._venda(grande, 900)

        d = self._roteiro(limite=1)
        self.assertEqual([p['nome'] for p in d['paradas']], ['GRANDE'])

    def test_respeita_o_limite_de_paradas(self):
        for i in range(30):
            c = self._cliente(f'C{i}', str(i), ponto=deslocar(i * 100))
            self._venda(c, 100)

        self.assertEqual(len(self._roteiro(limite=25)['paradas']), 25)
        self.assertEqual(self._roteiro(limite=25)['candidatos'], 30)

    def test_filtra_por_zona(self):
        norte = self._cliente('NORTE', '1', ponto=deslocar(3000))
        sul = self._cliente('SUL', '2', ponto=deslocar(-3000))
        self._venda(norte, 100)
        self._venda(sul, 100)

        nomes = [p['nome'] for p in self._roteiro(zona='norte')['paradas']]
        self.assertEqual(nomes, ['NORTE'])

    def test_cliente_de_outra_empresa_nao_entra(self):
        outra = self._empresa('Beta', '99888777000166')
        meu = self._cliente('MEU', '1')
        alheio = self._cliente('ALHEIO', '9', filial=outra)
        self._venda(meu, 100)
        self._venda(alheio, 9000, filial=outra)

        nomes = [p['nome'] for p in self._roteiro()['paradas']]
        self.assertEqual(nomes, ['MEU'])

    def test_sem_venda_nenhuma_explica_o_motivo(self):
        self._cliente('SEM VENDA', '1')

        d = self._roteiro()
        self.assertEqual(d['paradas'], [])
        self.assertIn('Nenhuma venda', d['motivo'])


class OrdemTests(BaseRoteiro):
    def test_ordem_e_geografica_nao_comercial(self):
        """
        Escolhidos os clientes, a sequencia e por proximidade. Se a prioridade
        comercial mandasse tambem na ordem, o roteiro atravessaria a cidade
        entre uma parada e outra.
        """
        perto = self._cliente('PERTO', '1', ponto=deslocar(0))
        meio = self._cliente('MEIO', '2', ponto=deslocar(1000))
        longe = self._cliente('LONGE', '3', ponto=deslocar(2000))
        # Receita em ordem inversa a geografia.
        self._venda(perto, 10)
        self._venda(meio, 500)
        self._venda(longe, 9000)

        ordem = [p['nome'] for p in self._roteiro()['paradas']]

        # As duas sequencias geograficas validas (ida ou volta). A ordem de
        # faturamento seria LONGE, MEIO, PERTO -- que coincide com uma delas,
        # entao o que realmente prova o ponto e MEIO estar sempre no meio.
        self.assertEqual(ordem[1], 'MEIO')
        self.assertIn(ordem, [['PERTO', 'MEIO', 'LONGE'],
                              ['LONGE', 'MEIO', 'PERTO']])

    def test_numera_as_paradas_em_sequencia(self):
        for i in range(3):
            c = self._cliente(f'C{i}', str(i), ponto=deslocar(i * 500))
            self._venda(c, 100)

        d = self._roteiro()
        self.assertEqual([p['ordem'] for p in d['paradas']], [1, 2, 3])

    def test_falha_do_provider_cai_no_local_e_avisa(self):
        """Um relatorio nao pode sair em branco porque o roteador piscou."""
        for i in range(3):
            c = self._cliente(f'C{i}', str(i), ponto=deslocar(i * 500))
            self._venda(c, 100)

        d = self._roteiro()
        self.assertEqual(len(d['paradas']), 3)
        self.assertEqual(d['ordem_por'], 'proximidade em linha reta')
        self.assertIsNotNone(d['km'])

    def test_uma_parada_so_nao_tenta_otimizar(self):
        c = self._cliente('UNICO', '1')
        self._venda(c, 100)

        d = self._roteiro()
        self.assertEqual(len(d['paradas']), 1)
        self.assertEqual(d['ordem_por'], 'parada única')

    def test_parada_traz_o_que_o_vendedor_precisa_na_rua(self):
        c = self._cliente('PADARIA', '1')
        self._venda(c, 100)
        self._recompra(c, status='vermelho', score=80, dias=40)

        p = self._roteiro()['paradas'][0]
        self.assertIn('Rua X', p['endereco'])
        self.assertEqual(p['telefone'], '84999990000')
        self.assertTrue(p['atrasado'])
        self.assertIsNotNone(p['valor_medio'])


class CompletoTests(BaseRoteiro):
    def _completo(self, **kw):
        from apps.mapas.services import RelatorioCompletoService

        return RelatorioCompletoService.gerar(self.filial, **kw)

    def test_traz_as_cinco_secoes(self):
        c = self._cliente('A', '1')
        self._venda(c, 100)

        d = self._completo()
        for chave in ('por_zona', 'por_bairro', 'clientes_por_zona',
                      'cobertura', 'roteiro'):
            self.assertIn(chave, d)

    def test_clientes_por_zona_inclui_quem_nao_comprou(self):
        """Para conferir cobertura de carteira, quem nao comprou e o que importa."""
        comprou = self._cliente('COMPROU', '1')
        self._cliente('NAO COMPROU', '2', ponto=deslocar(200))
        self._venda(comprou, 100)

        grupos = self._completo()['clientes_por_zona']
        nomes = [c['nome'] for g in grupos for c in g['clientes']]

        self.assertIn('COMPROU', nomes)
        self.assertIn('NAO COMPROU', nomes)
        self.assertEqual(sum(g['sem_compra'] for g in grupos), 1)

    def test_dentro_da_zona_quem_mais_fatura_vem_primeiro(self):
        a = self._cliente('MENOR', '1')
        b = self._cliente('MAIOR', '2', ponto=deslocar(100))
        self._venda(a, 100)
        self._venda(b, 900)

        grupos = self._completo()['clientes_por_zona']
        primeiro = grupos[0]['clientes'][0]['nome']
        self.assertEqual(primeiro, 'MAIOR')

    def test_cobertura_lista_quem_esta_sem_endereco(self):
        from apps.cadastros.models import Cliente

        Cliente.objects.create(
            filial=self.filial, razao_social='SEM GEO', cpf_cnpj='9',
            cidade='Natal', uf='RN', ativo=True)

        d = self._completo()
        self.assertEqual(d['cobertura']['sem_coordenada'], 1)

    def test_outra_empresa_nao_aparece_em_nenhuma_secao(self):
        outra = self._empresa('Beta', '99888777000166')
        alheio = self._cliente('ALHEIO', '9', cidade='Recife', filial=outra)
        self._venda(alheio, 9000, filial=outra)

        d = self._completo()
        nomes = [c['nome'] for g in d['clientes_por_zona'] for c in g['clientes']]

        self.assertNotIn('ALHEIO', nomes)
        self.assertEqual(float(d['por_zona']['total']['receita']), 0.0)
        self.assertEqual(d['roteiro']['paradas'], [])


class PaginaTests(BaseRoteiro):
    def test_pagina_abre(self):
        c = self._cliente('A', '1')
        self._venda(c, 100)

        resp = self.client.get(reverse('mapas:relatorio-completo'))
        self.assertEqual(resp.status_code, 200)

    def test_tem_imprimir_e_pdf(self):
        resp = self.client.get(reverse('mapas:relatorio-completo'))
        self.assertContains(resp, 'window.print()')
        self.assertContains(resp, 'btn-export-pdf')

    def test_mostra_as_cinco_secoes_na_pagina(self):
        c = self._cliente('PADARIA', '1')
        self._venda(c, 100)

        resp = self.client.get(reverse('mapas:relatorio-completo'))
        self.assertContains(resp, 'Faturamento por zona')
        self.assertContains(resp, 'Faturamento por bairro')
        self.assertContains(resp, 'Clientes por zona')
        self.assertContains(resp, 'Clientes sem endereço no mapa')
        self.assertContains(resp, 'Roteiro sugerido')

    def test_explica_como_o_roteiro_foi_montado(self):
        """Sem isso o roteiro parece arbitrario e ninguem discorda com base."""
        c = self._cliente('A', '1')
        self._venda(c, 100)

        resp = self.client.get(reverse('mapas:relatorio-completo'))
        self.assertContains(resp, 'Como a lista foi montada')

    def test_filtro_de_zona_vem_da_querystring(self):
        resp = self.client.get(reverse('mapas:relatorio-completo'), {'zona': 'norte'})
        self.assertEqual(resp.context['dados']['zona'], 'norte')

    def test_data_invalida_nao_derruba(self):
        resp = self.client.get(reverse('mapas:relatorio-completo'), {'de': 'ontem'})
        self.assertEqual(resp.status_code, 200)

    def test_hub_lista_o_relatorio(self):
        resp = self.client.get(reverse('core:relatorios-hub'))
        self.assertContains(resp, 'Relatório de Mapas (completo)')

    def test_exige_autenticacao(self):
        self.client.logout()
        resp = self.client.get(reverse('mapas:relatorio-completo'))
        self.assertIn(resp.status_code, (302, 401, 403))
