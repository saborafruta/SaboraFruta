import json
import shutil
import subprocess
from decimal import Decimal
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.template.loader import get_template
from django.test import SimpleTestCase
from django.template import Context, Template
from django.utils import timezone
from apps.pdv.services.comprovante_service import dados_comprovante, gerar_pdf


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
    def render_receipt(self, image_url='', settings_logo='', company_logo='', desconto=False, observacao=False, celular='', telefone=''):
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
        source = source.replace("numero_venda:1,", f"cliente_celular:{json.dumps(celular)},cliente_telefone:{json.dumps(telefone)},numero_venda:1,")
        result = subprocess.run(
            [shutil.which('node'), '-e', source], input=methods,
            encoding='utf-8', capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_telefone_reimpressao_com_fallback_e_escape(self):
        for celular, telefone, esperado in [('84999990000', '', '84999990000'), ('', '8432220000', '8432220000'), ('  ', '8432220000', '8432220000'), ('84999990000', '8432220000', '84999990000')]:
            html = self.render_receipt(celular=celular, telefone=telefone)
            self.assertIn('<strong>Telefone:</strong> ' + esperado, html)
        self.assertNotIn('<strong>Telefone:</strong>', self.render_receipt())
        self.assertIn('&lt;fone&gt;', self.render_receipt(telefone='<fone>'))

    def test_telefone_impressao_direta_html_e_pdf(self):
        template = get_template('pdv/home.html').template.source
        trechos = [
            template.split('    imprimirCupom(modo) {', 1)[1].split('    _cupomPdfHtml()', 1)[0],
            template.split('    _cupomPdfHtml() {', 1)[1].split('    async _carregarHtml2Pdf()', 1)[0],
            template.split('    async _gerarCupomPdfBlob(vendaCupom = null) {', 1)[1].split('    _telefoneWhatsAppCliente(', 1)[0],
        ]
        contexto = {'filial_ativa': SimpleNamespace(nome_fantasia='Loja teste', razao_social='Loja teste')}
        methods = get_template('pdv/_comprovante_methods.html').render(contexto)
        methods += Template('imprimirCupom(modo) {' + trechos[0] + '_cupomPdfHtml() {' + trechos[1] + 'async _gerarCupomPdfBlob(vendaCupom = null) {' + trechos[2]).render(Context(contexto))
        source = r'''
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
let html='', lines=[];
const popup={document:{write(s){html=s},close(){},querySelector(){return null}},focus(){}};
class PDF { setFont(){} setFontSize(){} splitTextToSize(s){return [s]} text(s){lines.push(s)} setDrawColor(){} setLineDashPattern(){} line(){} addPage(){} output(){return lines.join('\n')} }
const app=vm.runInNewContext('({'+fs.readFileSync(0,'utf8')+'})',{URL,setTimeout(){},window:{location:{origin:'https://ited.app.br'},open(){return popup},jspdf:{jsPDF:PDF}}});
app._carregarHtml2Pdf=async()=>{};
app.vendaFinalizadaData={};
(async()=>{
for (const cliente of [{celular:'84999990000'},{telefone:'8432220000'},{celular:' ',telefone:'8432220000'},null]) {
  app.venda={cliente:cliente?{nome:'Cliente teste',...cliente}:null,itens:[],pagamentos:[],total:0};
  const expected=cliente?(cliente.celular?.trim()||cliente.telefone):'';
  for(const modo of ['termica','a4']) {app.imprimirCupom(modo);assert.equal(html.includes('Telefone:'),!!expected);if(expected)assert.ok(html.includes(expected));}
  const htmlPdf=app._cupomPdfHtml();assert.equal(htmlPdf.includes('Telefone:'),!!expected);if(expected)assert.ok(htmlPdf.includes(expected));
  lines=[];const pdf=await app._gerarCupomPdfBlob();assert.equal(pdf.includes('Telefone:'),!!expected);if(expected)assert.ok(pdf.includes(expected));
}
})().catch(e=>{console.error(e);process.exit(1)});
'''
        result = subprocess.run([shutil.which('node'), '-e', source], input=methods, encoding='utf-8', capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

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
        self.assertNotIn('Subtotal bruto', html)

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


class ComprovanteDadosTests(SimpleTestCase):
    @staticmethod
    def venda(celular='', telefone='', com_cliente=True):
        return SimpleNamespace(
            filial=SimpleNamespace(parametros_sistema=None, imagem=None, empresa=SimpleNamespace(logo_url=''), nome_fantasia='Loja teste', razao_social='Loja teste'),
            numero_venda=1, data_venda=timezone.now(),
            cliente=SimpleNamespace(razao_social='Cliente teste', celular=celular, telefone=telefone) if com_cliente else None,
            itens=SimpleNamespace(all=lambda: []), pagamentos=SimpleNamespace(all=lambda: []),
            valor_desconto=Decimal(0), valor_acrescimo=Decimal(0), valor_total=Decimal(0), troco=Decimal(0),
        )

    def test_comprovante_publico_considera_ambos_os_campos(self):
        for celular, telefone, esperado in [('84999990000', '', '84999990000'), ('', '8432220000', '8432220000'), ('  ', '8432220000', '8432220000'), ('84999990000', '8432220000', '84999990000'), ('', '', '')]:
            cupom = dados_comprovante(self.venda(celular, telefone))
            self.assertEqual(cupom['cliente_telefone'], esperado)
            html = get_template('pdv/comprovante_publico.html').render({'cupom': cupom})
            self.assertEqual('<span>Telefone</span>' in html, bool(esperado))
            if esperado:
                self.assertIn(esperado, html)
        self.assertEqual(dados_comprovante(self.venda(com_cliente=False))['cliente_telefone'], '')

    def test_telefone_no_pdf_publico(self):
        from reportlab.platypus import Paragraph
        for celular, telefone, esperado in [('84999990000', '', '84999990000'), ('', '8432220000', '8432220000'), (' ', '8432220000', '8432220000')]:
            with patch('reportlab.platypus.Paragraph', wraps=Paragraph) as paragrafo:
                pdf = gerar_pdf(self.venda(celular, telefone))
            self.assertTrue(pdf.startswith(b'%PDF-'))
            self.assertIn('Telefone: ' + esperado, [chamada.args[0] for chamada in paragrafo.call_args_list])
