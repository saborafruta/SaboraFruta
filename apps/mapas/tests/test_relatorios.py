"""
Relatorios do modulo de mapas.

O que se verifica: os numeros do relatorio batem com os do mapa (mesma regra de
receita), o agrupamento nao mistura empresas, e a pagina traz de fato os botoes
de imprimir/PDF e as ressalvas -- um relatorio impresso circula, e um numero
sem contexto vira decisao errada.
"""
import datetime
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BaseRelatorio(TestCase):
    _seq = 0

    def setUp(self):
        self.filial = self._empresa('Alfa', '11222333000181')
        self.usuario = self._usuario(self.filial)
        self._logar(self.usuario, self.filial)

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

    def _usuario(self, filial, *, admin=True):
        from apps.core.models import PerfilAcesso, Usuario

        BaseRelatorio._seq += 1
        n = BaseRelatorio._seq
        perfil = PerfilAcesso.objects.create(
            empresa=filial.empresa, nome=f'Perfil {n}', is_admin=admin,
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

    def _cliente(self, nome, doc, *, lat=-5.79, lng=-35.21, cidade='Natal',
                 bairro='', uf='RN', filial=None):
        from apps.cadastros.models import Cliente

        return Cliente.objects.create(
            filial=filial or self.filial, razao_social=nome, cpf_cnpj=doc,
            cidade=cidade, bairro=bairro, uf=uf,
            latitude=lat, longitude=lng, ativo=True,
        )

    def _venda(self, cliente, valor, *, filial=None, dias_atras=1):
        from apps.pdv.models import VendaPDV

        BaseRelatorio._seq += 1
        filial = filial or self.filial
        return VendaPDV.objects.create(
            filial=filial, numero_venda=BaseRelatorio._seq, cliente=cliente,
            usuario=self.usuario, status='finalizada',
            valor_total=Decimal(str(valor)),
            data_venda=timezone.now() - datetime.timedelta(days=dias_atras),
        )

    def _regiao(self, **kw):
        from apps.mapas.services import RelatorioRegiaoService

        kw.setdefault('agrupar_por', 'cidade')
        return RelatorioRegiaoService.gerar(self.filial, **kw)


class RegiaoTests(BaseRelatorio):
    def test_agrupa_por_cidade_com_as_quatro_metricas(self):
        natal = self._cliente('A', '1', cidade='Natal')
        outro = self._cliente('B', '2', cidade='Natal', lat=-5.80)
        mossoro = self._cliente('C', '3', cidade='Mossoró', lat=-5.19)
        self._venda(natal, 100)
        self._venda(outro, 200)
        self._venda(mossoro, 50)

        d = self._regiao()
        por_nome = {l['regiao']: l for l in d['linhas']}

        self.assertEqual(por_nome['Natal']['clientes'], 2)
        self.assertEqual(por_nome['Natal']['pedidos'], 2)
        self.assertEqual(float(por_nome['Natal']['receita']), 300.0)
        self.assertEqual(float(por_nome['Mossoró']['receita']), 50.0)

    def test_ordena_por_receita(self):
        pequeno = self._cliente('P', '1', cidade='Assu')
        grande = self._cliente('G', '2', cidade='Natal', lat=-5.80)
        self._venda(pequeno, 10)
        self._venda(grande, 900)

        d = self._regiao()
        self.assertEqual([l['regiao'] for l in d['linhas']], ['Natal', 'Assu'])

    def test_participacao_soma_cem_por_cento(self):
        a = self._cliente('A', '1', cidade='Natal')
        b = self._cliente('B', '2', cidade='Assu', lat=-5.57)
        self._venda(a, 750)
        self._venda(b, 250)

        d = self._regiao()
        self.assertEqual(sorted(l['participacao'] for l in d['linhas']), [25.0, 75.0])

    def test_cliente_sem_venda_nao_infla_a_contagem(self):
        """
        Bairro com 40 cadastros e 2 compradores nao pode aparecer com 40
        clientes: a linha e sobre quem comprou no periodo.
        """
        comprou = self._cliente('COMPROU', '1', cidade='Natal')
        self._cliente('NAO COMPROU', '2', cidade='Natal', lat=-5.80)
        self._venda(comprou, 100)

        d = self._regiao()
        self.assertEqual(d['linhas'][0]['clientes'], 1)

    def test_permuta_fica_fora_da_receita_como_no_mapa(self):
        """Se divergisse do mapa, os dois numeros brigariam na reuniao."""
        from apps.financeiro.models import FormaPagamento
        from apps.pdv.models import PagamentoVendaPDV

        c = self._cliente('A', '1', cidade='Natal')
        venda = self._venda(c, 100)
        forma = FormaPagamento.objects.create(
            empresa=self.filial.empresa, descricao='Permuta', movimenta_caixa=False,
        )
        PagamentoVendaPDV.objects.create(
            venda_pdv=venda, forma_pagamento=forma,
            valor=Decimal('100'), troco=Decimal('0'),
        )

        d = self._regiao()
        self.assertEqual(float(d['total']['receita']), 0.0)
        # O pedido aconteceu; o que nao houve foi dinheiro.
        self.assertEqual(float(d['total']['pedidos']), 1.0)

    def test_agrupa_por_bairro(self):
        a = self._cliente('A', '1', bairro='Alecrim')
        b = self._cliente('B', '2', bairro='Tirol', lat=-5.80)
        self._venda(a, 100)
        self._venda(b, 300)

        d = self._regiao(agrupar_por='bairro')
        self.assertEqual([l['regiao'] for l in d['linhas']], ['Tirol', 'Alecrim'])

    def test_sem_bairro_vira_rotulo_explicito(self):
        """Linha em branco no relatorio pareceria erro de impressao."""
        c = self._cliente('A', '1', bairro='')
        self._venda(c, 100)

        d = self._regiao(agrupar_por='bairro')
        self.assertEqual(d['linhas'][0]['regiao'], '(sem bairro)')

    def test_agrupa_por_zona_com_o_mesmo_criterio_do_mapa(self):
        norte = self._cliente('N', '1', lat=-5.70)
        sul = self._cliente('S', '2', lat=-5.88)
        self._venda(norte, 100)
        self._venda(sul, 300)

        d = self._regiao(agrupar_por='zona')
        self.assertEqual(sorted(l['regiao'] for l in d['linhas']), ['Norte', 'Sul'])

    def test_agrupamento_invalido_cai_em_cidade(self):
        self.assertEqual(self._regiao(agrupar_por='galaxia')['agrupar_por'], 'cidade')

    def test_outra_empresa_nao_entra(self):
        outra = self._empresa('Beta', '99888777000166')
        meu = self._cliente('MEU', '1', cidade='Natal')
        alheio = self._cliente('ALHEIO', '9', cidade='Recife', filial=outra)
        self._venda(meu, 100)
        self._venda(alheio, 9000, filial=outra)

        d = self._regiao()
        self.assertEqual([l['regiao'] for l in d['linhas']], ['Natal'])
        self.assertEqual(float(d['total']['receita']), 100.0)

    def test_periodo_recorta(self):
        c = self._cliente('A', '1')
        self._venda(c, 100, dias_atras=2)
        self._venda(c, 900, dias_atras=400)

        hoje = timezone.localdate()
        d = self._regiao(inicio=hoje - datetime.timedelta(days=30), fim=hoje)
        self.assertEqual(float(d['total']['receita']), 100.0)


class CoberturaTests(BaseRelatorio):
    def _cobertura(self, **kw):
        from apps.mapas.services import RelatorioCoberturaService

        return RelatorioCoberturaService.gerar(self.filial, **kw)

    def test_lista_quem_esta_sem_coordenada(self):
        from apps.cadastros.models import Cliente

        self._cliente('COM GEO', '1')
        Cliente.objects.create(
            filial=self.filial, razao_social='SEM GEO', cpf_cnpj='2',
            endereco='Rua X', numero='10', bairro='Centro',
            cidade='Natal', uf='RN', ativo=True,
        )

        d = self._cobertura()

        self.assertEqual(d['total'], 2)
        self.assertEqual(d['com_coordenada'], 1)
        self.assertEqual(d['sem_coordenada'], 1)
        self.assertEqual(d['pendentes'][0]['nome'], 'SEM GEO')

    def test_traz_o_endereco_para_dar_para_corrigir(self):
        """So o nome nao permitiria fazer nada com a lista."""
        from apps.cadastros.models import Cliente

        Cliente.objects.create(
            filial=self.filial, razao_social='SEM GEO', cpf_cnpj='2',
            endereco='Av Principal', numero='500', bairro='Centro',
            cidade='Natal', uf='RN', cep='59000000', ativo=True,
        )

        endereco = self._cobertura()['pendentes'][0]['endereco']
        self.assertIn('Av Principal', endereco)
        self.assertIn('500', endereco)
        self.assertIn('59000000', endereco)

    def test_agrupa_pendencias_por_cidade_da_maior_para_a_menor(self):
        from apps.cadastros.models import Cliente

        for i in range(3):
            Cliente.objects.create(
                filial=self.filial, razao_social=f'N{i}', cpf_cnpj=f'n{i}',
                cidade='Natal', uf='RN', ativo=True,
            )
        Cliente.objects.create(
            filial=self.filial, razao_social='M', cpf_cnpj='m',
            cidade='Mossoró', uf='RN', ativo=True,
        )

        por_cidade = self._cobertura()['por_cidade']
        self.assertEqual(por_cidade[0], {'cidade': 'Natal', 'qtd': 3})

    def test_inativo_nao_conta_como_pendencia(self):
        from apps.cadastros.models import Cliente

        Cliente.objects.create(
            filial=self.filial, razao_social='INATIVO', cpf_cnpj='9',
            cidade='Natal', uf='RN', ativo=False,
        )
        self.assertEqual(self._cobertura()['sem_coordenada'], 0)

    def test_base_vazia_nao_divide_por_zero(self):
        self.assertEqual(self._cobertura()['percentual'], 0.0)

    def test_cliente_de_outra_empresa_nao_aparece(self):
        from apps.cadastros.models import Cliente

        outra = self._empresa('Beta', '99888777000166')
        Cliente.objects.create(
            filial=outra, razao_social='ALHEIO', cpf_cnpj='9',
            cidade='Recife', uf='PE', ativo=True,
        )
        self.assertEqual(self._cobertura()['total'], 0)


class RotasRelatorioTests(BaseRelatorio):
    def _rotas(self, **kw):
        from apps.mapas.services import RelatorioRotasService

        return RelatorioRotasService.gerar(self.filial, **kw)

    def _registro(self, **kw):
        from apps.mapas.models import RegistroRota

        kw.setdefault('paradas', 3)
        kw.setdefault('duracao_s', 1800)
        return RegistroRota.objects.create(
            filial=self.filial, usuario=self.usuario, **kw)

    def test_soma_km_paradas_e_economia(self):
        self._registro(distancia_m=12000)
        self._registro(distancia_m=8000, distancia_antes_m=10000, otimizada=True)

        d = self._rotas()

        self.assertEqual(d['total_rotas'], 2)
        self.assertEqual(d['total_km'], 20.0)
        self.assertEqual(d['total_economia_km'], 2.0)
        self.assertEqual(d['total_paradas'], 6)

    def test_periodo_vazio_nao_quebra(self):
        d = self._rotas()
        self.assertEqual(d['linhas'], [])
        self.assertEqual(d['total_km'], 0)

    def test_rota_de_outra_empresa_nao_entra(self):
        from apps.mapas.models import RegistroRota

        outra = self._empresa('Beta', '99888777000166')
        RegistroRota.objects.create(filial=outra, paradas=9, distancia_m=99000)
        self._registro(distancia_m=5000)

        self.assertEqual(self._rotas()['total_km'], 5.0)


class PaginaTests(BaseRelatorio):
    """As paginas em si: abrem, imprimem e dizem o que o numero significa."""

    def test_as_tres_paginas_abrem(self):
        for nome in ('mapas:relatorio-regiao', 'mapas:relatorio-cobertura',
                     'mapas:relatorio-rotas'):
            with self.subTest(nome=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)

    def test_tem_botao_de_imprimir_e_de_pdf(self):
        resp = self.client.get(reverse('mapas:relatorio-regiao'))
        self.assertContains(resp, 'window.print()')
        self.assertContains(resp, 'btn-export-pdf')

    def test_relatorio_de_regiao_avisa_sobre_permuta_e_volume(self):
        """Impresso circula: numero sem contexto vira decisao errada."""
        c = self._cliente('A', '1')
        self._venda(c, 100)

        resp = self.client.get(reverse('mapas:relatorio-regiao'))
        self.assertContains(resp, 'Doação e Permuta')
        self.assertContains(resp, 'unidades diferentes')

    def test_relatorio_de_rotas_avisa_que_km_nao_e_gps(self):
        from apps.mapas.models import RegistroRota

        RegistroRota.objects.create(
            filial=self.filial, paradas=2, distancia_m=5000, duracao_s=600)

        resp = self.client.get(reverse('mapas:relatorio-rotas'))
        self.assertContains(resp, 'não de percurso medido por GPS')

    def test_agrupamento_vem_da_querystring(self):
        resp = self.client.get(reverse('mapas:relatorio-regiao'), {'agrupar': 'bairro'})
        self.assertEqual(resp.context['dados']['agrupar_por'], 'bairro')
        self.assertEqual(resp.context['dados']['rotulo_grupo'], 'Bairro')

    def test_data_invalida_nao_derruba_a_pagina(self):
        resp = self.client.get(reverse('mapas:relatorio-regiao'), {'de': 'ontem'})
        self.assertEqual(resp.status_code, 200)

    def test_hub_de_relatorios_lista_os_tres(self):
        resp = self.client.get(reverse('core:relatorios-hub'))
        self.assertContains(resp, 'Vendas por Região')
        self.assertContains(resp, 'Cobertura de Geolocalização')
        self.assertContains(resp, 'Rotas e Otimização')

    def test_exige_autenticacao(self):
        self.client.logout()
        resp = self.client.get(reverse('mapas:relatorio-regiao'))
        self.assertIn(resp.status_code, (302, 401, 403))

    def test_permissao_e_a_de_relatorios(self):
        """
        Quem tira relatorio para uma reuniao nao e necessariamente quem opera
        o mapa; o hub ja e governado pela permissao de relatorios.
        """
        from apps.core.models import Permissao

        comum = self._usuario(self.filial, admin=False)
        Permissao.objects.create(perfil=comum.perfil, modulo='relatorios', pode_ver=True)
        self._logar(comum, self.filial)

        self.assertEqual(
            self.client.get(reverse('mapas:relatorio-regiao')).status_code, 200)
