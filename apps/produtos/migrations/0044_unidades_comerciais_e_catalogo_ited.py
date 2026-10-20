from decimal import Decimal

from django.conf import settings
from django.db import migrations


ITED_CNPJ = '06722483000114'


# As unidades de embalagem não têm conversão global: a quantidade contida em
# uma caixa, pacote ou fardo pertence à apresentação de cada produto. Só peso,
# volume, comprimento, área e agrupamentos fixos recebem fator canônico aqui.
UNIDADES_COMERCIAIS = (
    ('UN', 'Unidade', 'unidade', '1', 0),
    ('PC', 'Peça', 'unidade', '1', 0),
    ('PCT', 'Pacote', 'unidade', '1', 0),
    ('CX', 'Caixa', 'unidade', '1', 0),
    ('FD', 'Fardo', 'unidade', '1', 0),
    ('SC', 'Saco', 'unidade', '1', 0),
    ('SCL', 'Sacola', 'unidade', '1', 0),
    ('BAND', 'Bandeja', 'unidade', '1', 0),
    ('BOB', 'Bobina', 'unidade', '1', 0),
    ('RL', 'Rolo', 'unidade', '1', 0),
    ('KIT', 'Kit', 'unidade', '1', 0),
    ('CJ', 'Conjunto', 'unidade', '1', 0),
    ('JG', 'Jogo', 'unidade', '1', 0),
    ('PAR', 'Par', 'unidade', '1', 0),
    ('MC', 'Maço', 'unidade', '1', 0),
    ('DZ', 'Dúzia', 'unidade', '12', 0),
    ('CENT', 'Cento', 'unidade', '100', 0),
    ('MIL', 'Milheiro', 'unidade', '1000', 0),
    ('CRT', 'Cartela', 'unidade', '1', 0),
    ('DP', 'Display', 'unidade', '1', 0),
    ('LT', 'Lata', 'unidade', '1', 0),
    ('PT', 'Pote', 'unidade', '1', 0),
    ('FR', 'Frasco', 'unidade', '1', 0),
    ('GF', 'Garrafa', 'unidade', '1', 0),
    ('GL', 'Galão', 'unidade', '1', 0),
    ('BD', 'Balde', 'unidade', '1', 0),
    ('TB', 'Tubo', 'unidade', '1', 0),
    ('AMP', 'Ampola', 'unidade', '1', 0),
    ('BIS', 'Bisnaga', 'unidade', '1', 0),
    ('CAP', 'Cápsula', 'unidade', '1', 0),
    ('FOL', 'Folha', 'unidade', '1', 0),
    ('RM', 'Resma', 'unidade', '1', 0),
    ('BOMB', 'Bombona', 'unidade', '1', 0),
    ('TAMB', 'Tambor', 'unidade', '1', 0),
    ('BARRA', 'Barra', 'unidade', '1', 0),
    ('KG', 'Quilograma', 'peso', '1', 3),
    ('G', 'Grama', 'peso', '0.001', 3),
    ('MG', 'Miligrama', 'peso', '0.000001', 6),
    ('T', 'Tonelada', 'peso', '1000', 3),
    ('L', 'Litro', 'volume', '1', 3),
    ('ML', 'Mililitro', 'volume', '0.001', 3),
    ('M3', 'Metro cúbico', 'volume', '1', 3),
    ('CM3', 'Centímetro cúbico', 'volume', '0.000001', 6),
    ('M', 'Metro', 'comprimento', '1', 3),
    ('CM', 'Centímetro', 'comprimento', '0.01', 3),
    ('MM', 'Milímetro', 'comprimento', '0.001', 3),
    ('KM', 'Quilômetro', 'comprimento', '1000', 3),
    ('M2', 'Metro quadrado', 'area', '1', 3),
    ('CM2', 'Centímetro quadrado', 'area', '0.0001', 6),
)


# O banco da iTed recebeu parte deste catálogo com a unidade escolhida entre
# G/KG/ML/UN sem relação com o item. O mapa abaixo é deliberadamente explícito:
# não tenta adivinhar cadastros futuros nem altera empresas que tenham produtos
# com nomes parecidos.
CATALOGO_ITED = {
    'Abacaxi Perola': ('Abacaxi Pérola', 'UN', 'unitario', '', 1),
    'Abobora Cabotia': ('Abóbora Cabotiá', 'KG', 'granel_peso', '', 1),
    'Abobrinha Italiana': ('Abobrinha Italiana', 'KG', 'granel_peso', '', 1),
    'Acelga': ('Acelga', 'UN', 'unitario', '', 1),
    'Agriao': ('Agrião', 'MC', 'unitario', 'Maço', 1),
    'Alface Americana': ('Alface Americana', 'UN', 'unitario', '', 1),
    'Alface Crespa': ('Alface Crespa', 'UN', 'unitario', '', 1),
    'Alho': ('Alho', 'KG', 'granel_peso', '', 1),
    'Ameixa': ('Ameixa', 'KG', 'granel_peso', '', 1),
    'Banana Maca': ('Banana Maçã', 'KG', 'granel_peso', '', 1),
    'Banana Prata': ('Banana Prata', 'KG', 'granel_peso', '', 1),
    'Batata Doce': ('Batata Doce', 'KG', 'granel_peso', '', 1),
    'Batata Inglesa': ('Batata Inglesa', 'KG', 'granel_peso', '', 1),
    'Berinjela': ('Berinjela', 'KG', 'granel_peso', '', 1),
    'Beterraba': ('Beterraba', 'KG', 'granel_peso', '', 1),
    'Brocolis': ('Brócolis', 'UN', 'unitario', '', 1),
    'Caju': ('Caju', 'KG', 'granel_peso', '', 1),
    'Caja': ('Cajá', 'KG', 'granel_peso', '', 1),
    'Cebola Branca': ('Cebola Branca', 'KG', 'granel_peso', '', 1),
    'Cenoura': ('Cenoura', 'KG', 'granel_peso', '', 1),
    'Chuchu': ('Chuchu', 'KG', 'granel_peso', '', 1),
    'Coentro': ('Coentro', 'MC', 'unitario', 'Maço', 1),
    'Couve Manteiga': ('Couve Manteiga', 'MC', 'unitario', 'Maço', 1),
    'Couve-flor': ('Couve-flor', 'UN', 'unitario', '', 1),
    'Espinafre': ('Espinafre', 'MC', 'unitario', 'Maço', 1),
    'Goiaba': ('Goiaba', 'KG', 'granel_peso', '', 1),
    'Inhame': ('Inhame', 'KG', 'granel_peso', '', 1),
    'Kiwi': ('Kiwi', 'KG', 'granel_peso', '', 1),
    'Laranja Pera': ('Laranja Pera', 'KG', 'granel_peso', '', 1),
    'Limao Tahiti': ('Limão Tahiti', 'KG', 'granel_peso', '', 1),
    'Maca Gala': ('Maçã Gala', 'KG', 'granel_peso', '', 1),
    'Mamao Formosa': ('Mamão Formosa', 'UN', 'unitario', '', 1),
    'Manga Palmer': ('Manga Palmer', 'KG', 'granel_peso', '', 1),
    'Maracuja': ('Maracujá', 'KG', 'granel_peso', '', 1),
    'Maxixe': ('Maxixe', 'KG', 'granel_peso', '', 1),
    'Melancia': ('Melancia', 'UN', 'unitario', '', 1),
    'Melao Amarelo': ('Melão Amarelo', 'UN', 'unitario', '', 1),
    'Milho Verde': ('Milho Verde', 'UN', 'unitario', '', 1),
    'Morango': ('Morango', 'BAND', 'unitario', 'Bandeja', 1),
    'Pepino': ('Pepino', 'KG', 'granel_peso', '', 1),
    'Pera': ('Pera', 'KG', 'granel_peso', '', 1),
    'Pimentao Amarelo': ('Pimentão Amarelo', 'KG', 'granel_peso', '', 1),
    'Pimentao Verde': ('Pimentão Verde', 'KG', 'granel_peso', '', 1),
    'Pimentao Vermelho': ('Pimentão Vermelho', 'KG', 'granel_peso', '', 1),
    'Pitaya': ('Pitaya', 'KG', 'granel_peso', '', 1),
    'Quiabo': ('Quiabo', 'KG', 'granel_peso', '', 1),
    'Repolho Roxo': ('Repolho Roxo', 'KG', 'granel_peso', '', 1),
    'Repolho Verde': ('Repolho Verde', 'KG', 'granel_peso', '', 1),
    'Rucula': ('Rúcula', 'MC', 'unitario', 'Maço', 1),
    'Tangerina': ('Tangerina', 'KG', 'granel_peso', '', 1),
    'Tomate Cereja': ('Tomate Cereja', 'BAND', 'unitario', 'Bandeja', 1),
    'Tomate Italiano': ('Tomate Italiano', 'KG', 'granel_peso', '', 1),
    'Uva Verde': ('Uva Verde', 'KG', 'granel_peso', '', 1),
    'Vagem': ('Vagem', 'KG', 'granel_peso', '', 1),
    'Coco Verde': ('Coco Verde', 'UN', 'unitario', '', 1),
    'POLPA DE MANGA 100G': ('Polpa de Manga 100 g', 'UN', 'unitario', 'Pacote', 1),
    'Saco PEAD 12x26 (1/2 kg) - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Saco PEAD 15x30 (1 kg) - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Saco PEAD 20x35 (2 kg) - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Saco PEAD 25x35 (3 kg) - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Saco PEAD 28x44 (5 kg) - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Bobina PEAD 19x29 (2 kg) - 500 un': (None, 'BOB', 'unitario', 'Bobina', 500),
    'Bobina PEAD 24x34 (3 kg) - 500 un': (None, 'BOB', 'unitario', 'Bobina', 500),
    'Bobina PEAD 29x39 (5 kg) - 500 un': (None, 'BOB', 'unitario', 'Bobina', 500),
    'Saco PEBD 15x30x04 - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Saco PEBD Zip Lock nº 5 (14x10) - 100 un': (None, 'PCT', 'unitario', 'Pacote', 100),
    'Saco para geladinho 4,5x18 - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Saco para gelo 30x50 (3 kg) - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Saco PP 10x15x04 - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Saco PP 15x30x04 - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Saco PP 24x35x04 com adesivo - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Sacola branca camiseta 25x35 PP - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Sacola branca camiseta 38x48 M - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Sacola verde camiseta 29x44 P - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Sacola amarela camiseta 42x60 G - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Sacola boca reta 30x45 - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Sacola kraft 24x20 P - pacote': (None, 'PCT', 'unitario', 'Pacote', 1),
    'Sacola kraft 32x30 G - pacote': (None, 'PCT', 'unitario', 'Pacote', 1),
    'Saco kraft 1 kg (10x24,5) - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Saco kraft delivery 32x25 G - pacote': (None, 'PCT', 'unitario', 'Pacote', 1),
    'Caixa para pizza 30 cm - un': (None, 'UN', 'unitario', 'Caixa', 1),
    'Caixa para pizza 35 cm - un': (None, 'UN', 'unitario', 'Caixa', 1),
    'Caixa para salgados e doces 30 cm - un': (None, 'UN', 'unitario', 'Caixa', 1),
    'Copo descartável 180 ml branco - pacote': (None, 'PCT', 'unitario', 'Pacote', 1),
    'Copo descartável 200 ml transparente - pacote': (None, 'PCT', 'unitario', 'Pacote', 1),
    'Copo descartável 300 ml transparente - pacote': (None, 'PCT', 'unitario', 'Pacote', 1),
    'Copo térmico de isopor 180 ml - pacote': (None, 'PCT', 'unitario', 'Pacote', 1),
    'Garrafa PE 300 ml com tampa - un': (None, 'UN', 'unitario', 'Garrafa', 1),
    'Canudo flexível 6x210 mm - 100 un': (None, 'PCT', 'unitario', 'Pacote', 100),
    'Garfo refeição branco - 50 un': (None, 'PCT', 'unitario', 'Pacote', 50),
    'Colher refeição branca - 50 un': (None, 'PCT', 'unitario', 'Pacote', 50),
    'Prato raso branco 21 cm - 10 un': (None, 'PCT', 'unitario', 'Pacote', 10),
    'Guardanapo 20x22 - 50 folhas': (None, 'PCT', 'unitario', 'Pacote', 50),
    'Filme PVC 28 cm x 30 m - rolo': (None, 'RL', 'unitario', 'Rolo', 1),
    'Papel alumínio 30 cm x 7,5 m - rolo': (None, 'RL', 'unitario', 'Rolo', 1),
    'Marmita alumínio T120 500 ml com tampa - caixa': (None, 'CX', 'unitario', 'Caixa', 1),
    'Marmita isopor nº 8 750 ml - 100 un': (None, 'PCT', 'unitario', 'Pacote', 100),
    'Hamburgueira isopor CH001 - 400 un': (None, 'FD', 'unitario', 'Fardo', 400),
    'Bandeja isopor B2 rasa - 400 un': (None, 'FD', 'unitario', 'Fardo', 400),
    'Kit pote redondo 500 ml com tampa - 25 un': (None, 'KIT', 'unitario', 'Kit', 25),
    'Embalagem acetato I-32 para bolo - un': (None, 'UN', 'unitario', 'Unidade', 1),
    'Prato laminado nº 4 (24 cm) - un': (None, 'UN', 'unitario', 'Unidade', 1),
    'Forminha de papel nº 5 - 1.000 un': (None, 'PCT', 'unitario', 'Pacote', 1000),
    'Fita adesiva 45 mm x 100 m transparente - rolo': (None, 'RL', 'unitario', 'Rolo', 1),
    'Etiqueta MX 2316 lisa - rolo': (None, 'RL', 'unitario', 'Rolo', 1),
    'Desinfetante - un': (None, 'UN', 'unitario', 'Unidade', 1),
    'Saco para lixo picotado 50 L azul - rolo': (None, 'RL', 'unitario', 'Rolo', 1),
    'Luva plástica descartável - 100 un': (None, 'PCT', 'unitario', 'Pacote', 100),
    'Papel toalha interfolhado branco - pacote': (None, 'PCT', 'unitario', 'Pacote', 1),
    'Caneta esferográfica azul - un': (None, 'UN', 'unitario', 'Unidade', 1),
    'Pote plástico redondo com tampa - un': (None, 'UN', 'unitario', 'Unidade', 1),
}


def cadastrar_unidades_e_corrigir_catalogo(apps, schema_editor):
    alias = schema_editor.connection.alias
    if alias == 'default' and getattr(settings, 'TENANT_DATABASE_ROUTING_ENABLED', False):
        return

    Empresa = apps.get_model('core', 'Empresa')
    Filial = apps.get_model('core', 'Filial')
    Produto = apps.get_model('produtos', 'Produto')
    UnidadeMedida = apps.get_model('produtos', 'UnidadeMedida')
    UnidadeMedidaFilial = apps.get_model('produtos', 'UnidadeMedidaFilial')

    empresa = Empresa.objects.using(alias).filter(cnpj=ITED_CNPJ).order_by('pk').first()
    if empresa is None:
        return

    filiais = list(
        Filial.objects.using(alias).filter(empresa_id=empresa.pk, ativo=True).values_list('pk', flat=True)
    )
    existentes = {
        unidade.sigla.upper(): unidade
        for unidade in UnidadeMedida.objects.using(alias).filter(empresa_id=empresa.pk).order_by('pk')
    }
    novas = []
    for sigla, descricao, tipo, fator, casas in UNIDADES_COMERCIAIS:
        unidade = existentes.get(sigla)
        if unidade is None:
            unidade = UnidadeMedida(empresa_id=empresa.pk, sigla=sigla)
            novas.append(unidade)
        unidade.sigla = sigla
        unidade.descricao = descricao
        unidade.tipo = tipo
        unidade.fator_conversao_base = Decimal(fator)
        unidade.casas_decimais = casas
        unidade.ativo = True

    UnidadeMedida.objects.using(alias).bulk_create(novas)
    unidades = {
        unidade.sigla.upper(): unidade
        for unidade in UnidadeMedida.objects.using(alias).filter(
            empresa_id=empresa.pk,
            sigla__in=[item[0] for item in UNIDADES_COMERCIAIS],
        )
    }
    unidades_atualizadas = []
    for sigla, descricao, tipo, fator, casas in UNIDADES_COMERCIAIS:
        unidade = unidades[sigla]
        unidade.descricao = descricao
        unidade.tipo = tipo
        unidade.fator_conversao_base = Decimal(fator)
        unidade.casas_decimais = casas
        unidade.ativo = True
        if not unidade.id_externo:
            unidade.id_externo = f'unidade:{unidade.pk}'
        unidades_atualizadas.append(unidade)
    UnidadeMedida.objects.using(alias).bulk_update(
        unidades_atualizadas,
        ['descricao', 'tipo', 'fator_conversao_base', 'casas_decimais', 'ativo', 'id_externo'],
    )

    unidades_ids = [unidade.pk for unidade in unidades.values()]
    vinculos_existentes = {
        (vinculo.unidade_id, vinculo.filial_id): vinculo
        for vinculo in UnidadeMedidaFilial.objects.using(alias).filter(
            unidade_id__in=unidades_ids,
            filial_id__in=filiais,
        )
    }
    vinculos_novos = []
    vinculos_reativados = []
    for unidade_id in unidades_ids:
        for filial_id in filiais:
            vinculo = vinculos_existentes.get((unidade_id, filial_id))
            if vinculo is None:
                vinculos_novos.append(UnidadeMedidaFilial(
                    unidade_id=unidade_id,
                    filial_id=filial_id,
                    ativo=True,
                ))
            elif not vinculo.ativo:
                vinculo.ativo = True
                vinculos_reativados.append(vinculo)
    UnidadeMedidaFilial.objects.using(alias).bulk_create(vinculos_novos)
    if vinculos_reativados:
        UnidadeMedidaFilial.objects.using(alias).bulk_update(vinculos_reativados, ['ativo'])

    produtos = Produto.objects.using(alias).filter(
        filial__empresa_id=empresa.pk,
        descricao__in=CATALOGO_ITED,
    ).select_related('unidade_medida', 'unidade_medida_compra')

    produtos_atualizados = []
    for produto in produtos:
        descricao_original = produto.descricao
        nova_descricao, sigla, tipo_produto, embalagem, quantidade = CATALOGO_ITED[descricao_original]
        unidade = unidades[sigla]
        produto.unidade_medida_id = unidade.pk
        if produto.unidade_medida_compra_id:
            produto.unidade_medida_compra_id = unidade.pk
            produto.fator_conversao_compra = Decimal('1')
        produto.tipo_produto = tipo_produto
        produto.vendido_por_peso_granel = tipo_produto == 'granel_peso'
        produto.fracionavel = tipo_produto == 'granel_peso'
        produto.unidade_pesagem = 'kg'
        produto.tipo_embalagem = embalagem
        produto.quantidade_por_embalagem = Decimal(str(quantidade))
        if nova_descricao:
            produto.descricao = nova_descricao
            if produto.descricao_curta == descricao_original:
                produto.descricao_curta = nova_descricao[:120]
            if produto.descricao_pdv == descricao_original:
                produto.descricao_pdv = nova_descricao[:80]
        if descricao_original == 'POLPA DE MANGA 100G':
            produto.peso_liquido = Decimal('0.100')
        produtos_atualizados.append(produto)
    if produtos_atualizados:
        Produto.objects.using(alias).bulk_update(
            produtos_atualizados,
            [
                'unidade_medida', 'unidade_medida_compra', 'fator_conversao_compra',
                'tipo_produto', 'vendido_por_peso_granel', 'fracionavel',
                'unidade_pesagem', 'tipo_embalagem', 'quantidade_por_embalagem',
                'descricao', 'descricao_curta', 'descricao_pdv', 'peso_liquido',
            ],
        )


class Migration(migrations.Migration):

    dependencies = [
        ('produtos', '0043_backfill_codigos_barras_obrigatorios'),
    ]

    operations = [
        migrations.RunPython(
            cadastrar_unidades_e_corrigir_catalogo,
            migrations.RunPython.noop,
        ),
    ]
