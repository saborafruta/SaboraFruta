import json
import shutil
import subprocess
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest import skipUnless

from django.template.loader import get_template
from django.test import SimpleTestCase


class ReceiptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            self.images.append(dict(attrs)['src'])

    def handle_data(self, data):
        self.text.append(data)


@skipUnless(shutil.which('node'), 'Node.js necessário para validar o comprovante')
class ComprovanteTests(SimpleTestCase):
    def render_receipt(self, image_url='', settings_logo='', company_logo='', desconto=False, observacao=False):
        context = {
            'filial_ativa': SimpleNamespace(
                nome_fantasia='L&R SPORTS — São José', razao_social='Empresa',
                imagem=SimpleNamespace(url=image_url) if image_url else None,
            ),
            'parametros_sistema': SimpleNamespace(logo_url=settings_logo, logo=None),
            'empresa_logo_url': company_logo,
        }
        methods = get_template('pdv/_comprovante_methods.html').render(context)
        source = """
const vm = require('node:vm');
const fs = require('node:fs');
const app=vm.runInNewContext('({' + fs.readFileSync(0,'utf8') + '})', {
  URL, window:{location:{origin:'https://ited.app.br'}}
});
let html='';
const popup={document:{open(){},write(value){html=value;},close(){}},focus(){}};
app.renderizarComprovante(popup,{
  numero_venda:1, data_venda:'30/08/2026',operador:'João & Maria',
  cliente_nome:'<script>não executar</script>',valor_total:100,desconto:0,acrescimo:0,
  itens:[{descricao:'Açúcar & Café',quantidade:1,valor_unitario:100,valor_total:100}],
  pagamentos:[{forma_descricao:'Cartão',valor:100,troco:0}]
});
process.stdout.write(JSON.stringify(html));
"""
        if desconto:
            source = source.replace('quantidade:1,valor_unitario:100,valor_total:100',
                                    'quantidade:4,valor_unitario:100,valor_total:280,desconto_valor:120,desconto_percentual:30')
        if observacao:
            source = source.replace("descricao:'Açúcar & Café'", "descricao:'Açúcar & Café',observacao:'Separar <frágil> & urgente'")
        result = subprocess.run(
            [shutil.which('node'), '-e', source], input=methods,
            encoding='utf-8', capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_observacao_do_item_sai_em_negrito_e_escapada(self):
        html = self.render_receipt(observacao=True)
        self.assertIn('<strong>Observação: Separar &lt;frágil&gt; &amp; urgente</strong>', html)

    def test_desconto_no_item_e_resumo_em_valor_e_percentual(self):
        html = self.render_receipt(desconto=True)
        for trecho in ['30,00%', '120,00', '400,00', 'Subtotal bruto', 'Desconto nos itens']:
            self.assertIn(trecho, html)

    def test_caracteres_escapados_uma_vez_e_texto_seguro(self):
        html = self.render_receipt()
        self.assertIn('<h1>L&amp;R SPORTS — São José</h1>', html)
        self.assertNotIn('L&amp;amp;R', html)
        self.assertIn('Açúcar &amp; Café', html)
        self.assertIn('&lt;script&gt;não executar&lt;/script&gt;', html)
        self.assertIn('Imprimir térmica (80mm)', html)
        self.assertIn('Imprimir em A4', html)
        self.assertIn('img.decode()', html)

    def test_logo_absoluta_relativa_e_fallback(self):
        for image, settings, company, expected in [
            ('https://cdn.example.com/logo.png?a=1&b=2','','','https://cdn.example.com/logo.png?a=1&b=2'),
            ('/media/logo.png','','','https://ited.app.br/media/logo.png'),
            ('','/media/config.png','','https://ited.app.br/media/config.png'),
            ('','','/media/empresa.png','https://ited.app.br/media/empresa.png'),
            ('javascript:alert(1)','','',None),
        ]:
            with self.subTest(image=image, settings=settings, company=company):
                parser = ReceiptParser()
                parser.feed(self.render_receipt(image, settings, company))
                self.assertEqual(parser.images, [expected] if expected else [])
