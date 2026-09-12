from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from django.template.loader import get_template
from django.test import SimpleTestCase
from django.urls import reverse

from apps.core.models import ConfiguracaoEtiquetaVenda


class EtiquetaVendaTests(SimpleTestCase):
    def test_configuracao_tem_tamanho_fisico_e_texto_padrao(self):
        config = ConfiguracaoEtiquetaVenda(filial_id=1)

        self.assertEqual(config.largura_mm, Decimal('60.00'))
        self.assertEqual(config.altura_mm, Decimal('40.00'))
        self.assertEqual(config.texto_rodape, 'Obrigado pela sua preferência!')
        self.assertTrue(config.ativa)

        largura = ConfiguracaoEtiquetaVenda._meta.get_field('largura_mm')
        altura = ConfiguracaoEtiquetaVenda._meta.get_field('altura_mm')
        for field in (largura, altura):
            for validator in field.validators:
                validator(Decimal('60'))

    def test_template_renderiza_dados_da_venda_e_tamanho_em_milimetros(self):
        config = ConfiguracaoEtiquetaVenda(
            filial_id=1,
            largura_mm=Decimal('60.00'),
            altura_mm=Decimal('40.00'),
            impressora_nome='Zebra ZD220',
            texto_rodape='Volte sempre!',
        )
        filial = SimpleNamespace(nome_fantasia='Sabor a Fruta', razao_social='Sabor LTDA')
        venda = SimpleNamespace(numero_venda=644, data_venda=datetime(2026, 9, 12, 19, 11))

        html = get_template('pdv/etiqueta_venda.html').render({
            'config': config,
            'filial_identidade': filial,
            'logo_url': '/media/logo.png',
            'cliente_nome': 'Cliente Teste',
            'venda': venda,
            'auto_print': False,
        })

        self.assertIn('@page { size: 60.00mm 40.00mm; margin: 0; }', html)
        self.assertIn('Cliente Teste', html)
        self.assertIn('Venda #000644', html)
        self.assertIn('Volte sempre!', html)
        self.assertIn('Zebra ZD220', html)

    def test_rotas_e_botoes_estao_integrados(self):
        self.assertEqual(reverse('pdv:etiqueta_venda', args=[12]), '/pdv/venda/12/etiqueta/')
        self.assertEqual(
            reverse('core:admin_etiqueta_venda_config', args=[3]),
            '/gestao/central/filiais/3/etiqueta-venda/',
        )
        pdv_app = Path(__file__).resolve().parents[1]
        apps_dir = pdv_app.parent
        pdv = (pdv_app / 'templates/pdv/home.html').read_text(encoding='utf-8')
        central = (apps_dir / 'core/templates/core/admin/central.html').read_text(encoding='utf-8')
        self.assertIn('Imprimir etiqueta de venda', pdv)
        self.assertIn('imprimirEtiquetaVenda()', pdv)
        self.assertIn('admin_etiqueta_venda_config', central)
