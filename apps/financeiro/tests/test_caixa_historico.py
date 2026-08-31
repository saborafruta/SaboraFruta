from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
import json
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.financeiro.models import (
    ContaBancaria, ContaPagar, ContaReceber, ExtratoBancario,
    DiaCaixaHistorico, LoteCaixaHistorico, MovimentoCaixaHistorico,
)
from apps.financeiro.services.caixa_historico_service import (
    consultar_historico, importar_historico, validar_historico,
)
from apps.financeiro.services.posicao_diaria_service import PosicaoDiariaCaixaService


class CaixaHistoricoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(razao_social='Histórico', nome_fantasia='Histórico',
                                             cnpj='53345678000191', codigo_regime_tributario=1)
        cls.filial = Filial.objects.create(empresa=cls.empresa, razao_social='Matriz',
                                          nome_fantasia='Matriz', cnpj='53345678000192', uf='RN')
        cls.outra = Filial.objects.create(empresa=cls.empresa, razao_social='Outra',
                                         nome_fantasia='Outra', cnpj='53345678000273', uf='RN')
        perfil = PerfilAcesso.objects.create(empresa=cls.empresa, nome='Admin', is_admin=True)
        cls.usuario = Usuario.objects.create_user(email='historico@example.com', nome='Teste',
                                                  password='teste', empresa=cls.empresa,
                                                  filial=cls.filial, perfil=perfil)

    def setUp(self):
        self.payload = dict(versao=1, arquivo='caixa.xlsx', arquivo_sha256='a'*64, dias=[dict(
            data='2026-08-28', aba='28.08.2026', saldo_anterior='100.00', saldo_final='175.00',
            total_entradas='100.00', total_saidas='25.00', observacoes=[], movimentos=[
                dict(tipo='entrada', descricao='Recebimento antigo', celula='B9', valor_original='100'),
                dict(tipo='saida', descricao='', celula='G9', valor_original='25'),
            ])])
        self.params = dict(filial_id=self.filial.pk, cnpj=self.filial.cnpj,
                           inicio=date(2023, 8, 1), fim=date(2026, 8, 30))
        self.client.force_login(self.usuario)
        session = self.client.session
        session['filial_ativa_id'] = self.filial.pk
        session.save()

    def importar(self, **kwargs):
        return importar_historico(self.payload, **self.params, **kwargs)

    def test_simulacao_nao_grava(self):
        resultado = self.importar()
        self.assertEqual(resultado['movimentos_novos'], 2)
        self.assertEqual(LoteCaixaHistorico.objects.count(), 0)
        self.assertEqual(DiaCaixaHistorico.objects.count(), 0)

    def test_importacao_isolada_e_repeticao_idempotente(self):
        modelos = [ContaBancaria, ContaPagar, ContaReceber, ExtratoBancario]
        antes = [m.objects.count() for m in modelos]
        bancos = list(ContaBancaria.objects.values('pk', 'saldo_atual', 'saldo_inicial'))
        saldo = PosicaoDiariaCaixaService(self.filial, date(2026, 8, 31)).gerar()['total_fechamento']
        self.importar(aplicar=True)
        repeticao = self.importar(aplicar=True)
        self.assertEqual(repeticao['dias_novos'], 0)
        self.assertEqual(repeticao['dias_ja_importados'], 1)
        self.assertEqual(LoteCaixaHistorico.objects.count(), 1)
        self.assertEqual(MovimentoCaixaHistorico.objects.count(), 2)
        self.assertEqual([m.objects.count() for m in modelos], antes)
        self.assertEqual(list(ContaBancaria.objects.values('pk', 'saldo_atual', 'saldo_inicial')), bancos)
        self.assertEqual(PosicaoDiariaCaixaService(self.filial, date(2026, 8, 31)).gerar()['total_fechamento'], saldo)
        self.assertFalse(DiaCaixaHistorico.objects.filter(data=date(2026, 8, 31)).exists())

    def test_recusa_sobrescrever_dia_importado(self):
        self.importar(aplicar=True)
        self.payload['dias'][0]['saldo_final'] = '999'
        with self.assertRaisesMessage(ValueError, 'Histórico diferente'):
            self.importar(aplicar=True)
        self.assertEqual(DiaCaixaHistorico.objects.get().saldo_final_informado, Decimal('175'))

    def test_recusa_cnpj_errado(self):
        self.params['cnpj'] = self.outra.cnpj
        with self.assertRaisesMessage(ValueError, 'CNPJ'):
            self.importar(aplicar=True)
        self.assertEqual(DiaCaixaHistorico.objects.count(), 0)

    def test_recusa_dia_fora_do_periodo_e_duplicado(self):
        self.payload['dias'][0]['data'] = '2026-08-31'
        with self.assertRaisesMessage(ValueError, 'fora do período'):
            self.importar(aplicar=True)
        self.payload['dias'][0]['data'] = '2026-08-28'
        self.payload['dias'].append(deepcopy(self.payload['dias'][0]))
        with self.assertRaisesMessage(ValueError, 'Data duplicada'):
            self.importar(aplicar=True)

    def test_recusa_celula_duplicada_e_total_errado(self):
        self.payload['dias'][0]['movimentos'][1]['celula'] = 'B9'
        with self.assertRaisesMessage(ValueError, 'Célula duplicada'):
            self.importar(aplicar=True)
        self.payload['dias'][0]['movimentos'][1]['celula'] = 'G9'
        self.payload['dias'][0]['total_saidas'] = '26'
        with self.assertRaisesMessage(ValueError, 'Total de saida'):
            self.importar(aplicar=True)
        self.assertEqual(DiaCaixaHistorico.objects.count(), 0)

    def test_precision_original_e_fechamento_informado_preservados(self):
        self.payload['dias'][0]['movimentos'][1]['valor_original'] = '25.004'
        self.payload['dias'][0]['saldo_final'] = '150'
        self.importar(aplicar=True)
        saida = MovimentoCaixaHistorico.objects.get(tipo='saida')
        self.assertEqual(saida.valor, Decimal('25'))
        self.assertEqual(saida.valor_original, '25.004')
        self.assertEqual(DiaCaixaHistorico.objects.get().saldo_final_informado, Decimal('150'))

    def test_nao_deduplica_linhas_legitimas_iguais(self):
        item = deepcopy(self.payload['dias'][0]['movimentos'][0])
        item['celula'] = 'B10'
        self.payload['dias'][0]['movimentos'].append(item)
        self.payload['dias'][0]['total_entradas'] = '200'
        self.importar(aplicar=True)
        self.assertEqual(MovimentoCaixaHistorico.objects.filter(tipo='entrada').count(), 2)

    def test_falha_reverte_lote_inteiro(self):
        with patch.object(MovimentoCaixaHistorico.objects, 'bulk_create', side_effect=RuntimeError('falha')):
            with self.assertRaises(RuntimeError):
                self.importar(aplicar=True)
        self.assertEqual(LoteCaixaHistorico.objects.count(), 0)
        self.assertEqual(DiaCaixaHistorico.objects.count(), 0)

    def test_tela_automatica_sem_bancos_e_fonte_operacional_separada(self):
        self.importar(aplicar=True)
        url = reverse('financeiro:posicao_diaria')
        response = self.client.get(url, {'data': '2026-08-28'})
        self.assertTemplateUsed(response, 'financeiro/posicao_diaria_historico.html')
        self.assertContains(response, 'Recebimento antigo')
        self.assertContains(response, 'Importação — sem descrição na planilha')
        self.assertNotContains(response, 'Saldos por conta')
        self.assertNotContains(response, 'Transferir entre contas')
        self.assertNotContains(response, 'Adicionar entrada manual')
        operational = self.client.get(url, {'data': '2026-08-28', 'fonte': 'operacional'})
        self.assertTemplateUsed(operational, 'financeiro/posicao_diaria.html')
        self.assertNotContains(operational, 'Recebimento antigo')

    def test_isolamento_filial_e_dia_ausente(self):
        self.importar(aplicar=True)
        self.assertEqual(consultar_historico(self.outra, date(2026, 8, 1), date(2026, 8, 31))['dias'], [])
        response = self.client.get(reverse('financeiro:posicao_diaria'), {'data': '2026-08-31', 'fonte': 'historico'})
        self.assertContains(response, 'Nenhum histórico importado')
        self.assertNotContains(response, 'Recebimento antigo')
        self.assertNotContains(response, 'R$ 0,00')

    def test_periodo_nao_soma_fechamentos(self):
        outro = deepcopy(self.payload['dias'][0])
        outro.update(data='2026-08-29', aba='29.08.2026', saldo_final='250')
        self.payload['dias'].append(outro)
        self.importar(aplicar=True)
        result = consultar_historico(self.filial, date(2026, 8, 1), date(2026, 8, 31))
        self.assertEqual(result['total_entradas'], Decimal('200'))
        self.assertEqual(result['ultimo_dia'].saldo_final_informado, Decimal('250'))

    def test_html_nao_interpreta_descricao_e_login_obrigatorio(self):
        self.payload['dias'][0]['movimentos'][0]['descricao'] = '<script>alert(1)</script>'
        self.importar(aplicar=True)
        response = self.client.get(reverse('financeiro:posicao_diaria'), {'data': '2026-08-28'})
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.client.logout()
        self.assertEqual(self.client.get(reverse('financeiro:posicao_diaria'), {'data': '2026-08-28'}).status_code, 302)

    def test_comando_stdin_simula_por_padrao(self):
        out = StringIO()
        with patch('sys.stdin', StringIO(json.dumps(self.payload))):
            call_command('importar_caixa_historico', '-', filial=self.filial.pk,
                         cnpj=self.filial.cnpj, inicio=date(2023, 8, 1), fim=date(2026, 8, 30), stdout=out)
        self.assertFalse(json.loads(out.getvalue())['aplicado'])
        self.assertFalse(DiaCaixaHistorico.objects.exists())

    def test_paginacao_mantem_totais_do_periodo_completo(self):
        modelo = self.payload['dias'][0]
        self.payload['dias'] = []
        for numero in range(40):
            dia = deepcopy(modelo)
            data = date(2026, 1, 1) + timedelta(days=numero)
            dia.update(data=data.isoformat(), aba=data.strftime('%d.%m.%Y'))
            self.payload['dias'].append(dia)
        self.importar(aplicar=True)
        contexto = consultar_historico(self.filial, date(2026, 1, 1), date(2026, 2, 28), pagina=2)
        self.assertEqual(len(contexto['dias']), 9)
        self.assertEqual(contexto['quantidade_dias'], 40)
        self.assertEqual(contexto['total_entradas'], Decimal('4000'))
        self.assertEqual(contexto['ultimo_dia'].data, date(2026, 2, 9))

    def test_mes_historico_inclui_mes_inteiro_e_isola_outra_filial_na_tela(self):
        self.importar(aplicar=True)
        url = reverse('financeiro:posicao_diaria')
        response = self.client.get(url, {'data': '2026-08-01', 'periodo': 'mes', 'fonte': 'historico'})
        self.assertContains(response, 'Recebimento antigo')
        self.assertEqual(response.context['data_fim'], date(2026, 8, 31))
        session = self.client.session
        session['filial_ativa_id'] = self.outra.pk
        session.save()
        response = self.client.get(url, {'data': '2026-08-28', 'fonte': 'historico'})
        self.assertNotContains(response, 'Recebimento antigo')

    def test_recusa_valores_invalidos(self):
        for valor in ['NaN', 'Infinity', '-25', '0', '1e100', 'texto']:
            with self.subTest(valor=valor):
                self.payload['dias'][0]['movimentos'][1]['valor_original'] = valor
                with self.assertRaises(ValueError):
                    validar_historico(self.payload, self.params['inicio'], self.params['fim'])
