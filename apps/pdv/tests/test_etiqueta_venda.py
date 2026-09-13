from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from django.template.loader import get_template
from django.test import SimpleTestCase
from django.urls import reverse

from apps.core.models import ConfiguracaoEtiquetaVenda
from apps.core.forms.admin_forms import ConfiguracaoEtiquetaVendaForm


class EtiquetaVendaTests(SimpleTestCase):
    def test_formulario_preserva_offset_negativo_com_virgula(self):
        dados = {
            'largura_mm': '60', 'altura_mm': '30', 'margem_interna_mm': '1',
            'deslocamento_horizontal_mm': '-1,5', 'deslocamento_vertical_mm': '−0,5',
            'impressora_nome': 'Zebra', 'texto_rodape': 'Teste',
            'tamanho_fonte_mensagem_mm': '4,0',
            'layout_elementos': '{}',
        }
        form = ConfiguracaoEtiquetaVendaForm(
            dados,
            instance=ConfiguracaoEtiquetaVenda(filial_id=1),
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['deslocamento_horizontal_mm'], Decimal('-1.5'))
        self.assertEqual(form.cleaned_data['deslocamento_vertical_mm'], Decimal('-0.5'))
        self.assertEqual(form.cleaned_data['tamanho_fonte_mensagem_mm'], Decimal('4.0'))

    def test_ajuste_horizontal_usa_select_nativo_com_valor_negativo(self):
        form = ConfiguracaoEtiquetaVendaForm(
            instance=ConfiguracaoEtiquetaVenda(
                filial_id=1,
                deslocamento_horizontal_mm=Decimal('-2.00'),
            ),
        )

        html = str(form['deslocamento_horizontal_mm'])
        self.assertIn('<select', html)
        self.assertIn('value="-2.00" selected', html)
        self.assertIn('← 2,00 mm — esquerda', html)
        self.assertNotIn('type="text"', html)

    def test_configuracao_tem_tamanho_fisico_e_texto_padrao(self):
        config = ConfiguracaoEtiquetaVenda(filial_id=1)

        self.assertEqual(config.largura_mm, Decimal('60.00'))
        self.assertEqual(config.altura_mm, Decimal('40.00'))
        self.assertEqual(config.texto_rodape, 'Obrigado pela sua preferência!')
        self.assertEqual(config.tamanho_fonte_mensagem_mm, Decimal('2.70'))
        self.assertFalse(config.ativa)
        self.assertEqual(config.margem_interna_mm, Decimal('1.00'))
        self.assertEqual(config.deslocamento_horizontal_mm, Decimal('-1.00'))
        self.assertFalse(config.alta_nitidez)
        self.assertEqual(config.layout_normalizado()['logo']['x'], 3)
        self.assertEqual(config.layout_normalizado()['cliente']['x'], 39)

        largura = ConfiguracaoEtiquetaVenda._meta.get_field('largura_mm')
        altura = ConfiguracaoEtiquetaVenda._meta.get_field('altura_mm')
        for field in (largura, altura):
            for validator in field.validators:
                validator(Decimal('60'))

    def test_template_renderiza_dados_da_venda_e_tamanho_em_milimetros(self):
        config = ConfiguracaoEtiquetaVenda(
            filial_id=1,
            ativa=True,
            largura_mm=Decimal('60.00'),
            altura_mm=Decimal('30.00'),
            impressora_nome='Zebra ZD220',
            texto_rodape='Volte sempre!',
            tamanho_fonte_mensagem_mm=Decimal('4.00'),
            margem_interna_mm=Decimal('1.50'),
            deslocamento_horizontal_mm=Decimal('-1.00'),
            alta_nitidez=True,
        )
        filial = SimpleNamespace(nome_fantasia='Sabor a Fruta', razao_social='Sabor LTDA')
        venda = SimpleNamespace(numero_venda=644, data_venda=datetime(2026, 9, 12, 19, 11))

        html = get_template('pdv/etiqueta_venda.html').render({
            'config': config,
            'filial_identidade': filial,
            'logo_url': '/media/logo.png',
            'cliente_nome': 'Cliente Teste',
            'venda': venda,
            'auto_print': True,
            'layout_etiqueta': config.layout_normalizado(),
        })

        self.assertIn('@page { size: 60.00mm 30.00mm; margin: 0; }', html)
        self.assertIn('Cliente Teste', html)
        self.assertIn('Venda #000644', html)
        self.assertIn('Volte sempre!', html)
        self.assertIn('font-size:4.00mm', html)
        self.assertIn('Zebra ZD220', html)
        self.assertIn('left:3.0%', html)
        self.assertIn('left:39.0%', html)
        self.assertIn('--safe-margin: 1.50mm', html)
        self.assertIn('--offset-x: -1.00mm', html)
        self.assertIn('label high-sharpness', html)
        self.assertEqual(html.count(' data-fit-text '), 4)
        self.assertNotIn('class="element message" data-fit-text', html)
        self.assertIn('<span class="message-text">Volte sempre!</span>', html)
        self.assertIn("window.addEventListener('beforeprint', window.fitLabelText)", html)
        self.assertIn('window.printLabel = function ()', html)
        self.assertIn('window.setTimeout(() => window.printLabel(), 180)', html)
        self.assertIn('range.getBoundingClientRect()', html)
        self.assertIn('height: var(--label-height) !important', html)

    def test_layout_padrao_e_seguro_e_pode_ser_reposicionado(self):
        config = ConfiguracaoEtiquetaVenda(
            filial_id=1,
            layout_elementos={
                'logo': {'x': 12.5, 'y': 7, 'w': 28, 'h': 70},
                'cliente': {'x': 500, 'y': -20, 'w': 40, 'h': 20},
                'desconhecido': {'x': 1, 'y': 1, 'w': 1, 'h': 1},
            },
        )

        layout = config.layout_normalizado()

        self.assertEqual(layout['logo']['x'], 12.5)
        self.assertEqual(layout['logo']['y'], 7)
        self.assertEqual(layout['cliente']['x'], 60)
        self.assertEqual(layout['cliente']['y'], 0)
        self.assertNotIn('desconhecido', layout)

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
        editor = (apps_dir / 'core/templates/core/admin/etiqueta_venda_form.html').read_text(encoding='utf-8')
        self.assertIn('Imprimir etiqueta de venda', pdv)
        self.assertIn('imprimirEtiquetaVenda()', pdv)
        self.assertIn('{% if etiqueta_venda_disponivel %}', pdv)
        self.assertIn('admin_etiqueta_venda_config', central)
        self.assertIn('data-layout-key="logo"', editor)
        self.assertIn("element.addEventListener('pointerdown'", editor)
        self.assertIn('id_layout_elementos', editor)
        self.assertIn('id_deslocamento_horizontal_mm', editor)
        self.assertIn('id_alta_nitidez', editor)
        self.assertIn('id_tamanho_fonte_mensagem_mm', editor)
        self.assertIn('id="preview-message-text"', editor)
        self.assertIn("messageElement.style.fontSize = (decimalValue(messageFont, 2.7) * pxPerMm) + 'px'", editor)
        self.assertIn('Escolha “esquerda”', editor)
        self.assertNotIn('setHorizontalOffset', editor)
        self.assertNotIn('offset-left-2', editor)
        self.assertNotIn('data-horizontal-offset=', editor)
        self.assertIn("return '−' + magnitude + ' mm · esquerda'", editor)
        self.assertIn('function scaleAndFitPreview(w)', editor)
        self.assertIn('fitPreviewText(byId(id)', editor)
