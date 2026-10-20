from django.conf import settings
from django.db import migrations


ITED_CNPJ = '06722483000114'


# Fotografias localizadas por nome no Wikimedia Commons e confirmadas como
# CC0 (sem exigencia de atribuicao). Usamos miniaturas de 960 px para evitar
# que as telas do ERP baixem os arquivos originais, que podem ter varios MB.
# A lista e deliberadamente conservadora: variedades ou embalagens sem uma
# correspondencia visual inequivoca permanecem sem foto.
IMAGENS_HORTIFRUTI = {
    'Abacaxi Pérola': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/3/3f/Pineapple-2540622.jpg/960px-Pineapple-2540622.jpg',
    'Abóbora Cabotiá': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/c/c6/Cucurbita_kabocha_o_kabuti%C3%A1_o_tipo_Tetsukabuto_2_zapallos_c%C3%A1scara.jpg/960px-Cucurbita_kabocha_o_kabuti%C3%A1_o_tipo_Tetsukabuto_2_zapallos_c%C3%A1scara.jpg',
    'Acelga': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/0/0e/Organic_Swiss_chard_bunches.jpg/960px-Organic_Swiss_chard_bunches.jpg',
    'Agrião': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/4/4c/20190904Nasturtium_officinale3.jpg/960px-20190904Nasturtium_officinale3.jpg',
    'Alface Americana': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/c/c6/Lettuce_iceberg.jpg/960px-Lettuce_iceberg.jpg',
    'Alho': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/9/96/Single_clove_garlic_%2820240923%29.jpg/960px-Single_clove_garlic_%2820240923%29.jpg',
    'Ameixa': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/9/91/Black_plums.jpg/960px-Black_plums.jpg',
    'Beterraba': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ac/Beetroot_fruit.jpg/960px-Beetroot_fruit.jpg',
    'Brócolis': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/0/00/Broccoli_-_San_Francisco%2C_CA.jpg/960px-Broccoli_-_San_Francisco%2C_CA.jpg',
    'Caju': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/5/56/A_ripe_cashew_fruit.jpg/960px-A_ripe_cashew_fruit.jpg',
    'Cebola Branca': 'https://upload.wikimedia.org/wikipedia/commons/a/a1/Alibag_White_Onions_from_Pezari_village_of_Alibag_taluka.jpg',
    'Cenoura': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/f/fc/Carrot_bunches.jpg/960px-Carrot_bunches.jpg',
    'Chuchu': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/5/5d/Chayote_on_vine_Central_America.jpg/960px-Chayote_on_vine_Central_America.jpg',
    'Coentro': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ac/Cilantro_%28Alabama_Extension%29.jpg/960px-Cilantro_%28Alabama_Extension%29.jpg',
    'Couve-flor': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/1/13/Cauliflower_%2820240215%29.jpg/960px-Cauliflower_%2820240215%29.jpg',
    'Espinafre': 'https://upload.wikimedia.org/wikipedia/commons/4/4f/Spinach_2.jpg',
    'Inhame': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/d/d4/Tuber_of_yam.jpg/960px-Tuber_of_yam.jpg',
    'Kiwi': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/c/c0/Kiwifruit_01.jpg/960px-Kiwifruit_01.jpg',
    'Laranja Pera': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/3/31/Orange_Fruit_%28Unsplash%29.jpg/960px-Orange_Fruit_%28Unsplash%29.jpg',
    'Limão Tahiti': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/9/9c/Lim%C3%B5es-persa.jpg/960px-Lim%C3%B5es-persa.jpg',
    'Maçã Gala': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/2/2c/Apples_%28Gala%29_-_Cambridge%2C_MA.jpg/960px-Apples_%28Gala%29_-_Cambridge%2C_MA.jpg',
    'Maracujá': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/4/43/Cut_and_uncut_passion_fruits.jpg/960px-Cut_and_uncut_passion_fruits.jpg',
    'Melancia': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/7/7d/Quite_big_watermelon_in_Shandong.jpg/960px-Quite_big_watermelon_in_Shandong.jpg',
    'Milho Verde': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/0/02/Corn_on_the_cob_%28sweet_corn%29.jpg/960px-Corn_on_the_cob_%28sweet_corn%29.jpg',
    'Pepino': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/1/14/Cucumber_01.jpg/960px-Cucumber_01.jpg',
    'Pimentão Verde': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/1/17/Green_bell_pepper_2017_A.jpg/960px-Green_bell_pepper_2017_A.jpg',
    'Pimentão Vermelho': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/3/33/Bent_red_bell_pepper_2017_-_1A.jpg/960px-Bent_red_bell_pepper_2017_-_1A.jpg',
    'Pitaya': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/9/96/A_dragon_fruit.jpg/960px-A_dragon_fruit.jpg',
    'Quiabo': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/7/7a/Abelmoschus-esculentus.jpg/960px-Abelmoschus-esculentus.jpg',
    'Repolho Roxo': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/f/fb/Cabbage_%28red%29_-_Harbourside_Market%2C_Wellington%2C_New_Zealand_-_DSC09856.jpg/960px-Cabbage_%28red%29_-_Harbourside_Market%2C_Wellington%2C_New_Zealand_-_DSC09856.jpg',
    'Rúcula': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/d/db/Arugula_for_sale.jpg/960px-Arugula_for_sale.jpg',
    'Tangerina': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/0/02/Bowl_of_tangerines.jpg/960px-Bowl_of_tangerines.jpg',
    'Tomate Cereja': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/3/39/Cherry-Tomatoes-in-Pack.jpg/960px-Cherry-Tomatoes-in-Pack.jpg',
    'Uva Verde': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ad/GD_%E5%BB%A3%E6%9D%B1_Guangdong_%E6%9D%B1%E8%8E%9E_Dongguan_%E6%B0%91%E7%9B%88_%E5%9C%8B%E8%B2%BF%E5%9F%8E_DC_Mall_shop_BLT_Supermarket_food_fruit_green_grape_December_2024_R12S_01.jpg/960px-GD_%E5%BB%A3%E6%9D%B1_Guangdong_%E6%9D%B1%E8%8E%9E_Dongguan_%E6%B0%91%E7%9B%88_%E5%9C%8B%E8%B2%BF%E5%9F%8E_DC_Mall_shop_BLT_Supermarket_food_fruit_green_grape_December_2024_R12S_01.jpg',
}


def _empresa_ited(apps, alias):
    Empresa = apps.get_model('core', 'Empresa')
    return Empresa.objects.using(alias).filter(cnpj=ITED_CNPJ).order_by('pk').first()


def cadastrar_imagens_hortifruti(apps, schema_editor):
    alias = schema_editor.connection.alias
    if alias == 'default' and getattr(settings, 'TENANT_DATABASE_ROUTING_ENABLED', False):
        return

    empresa = _empresa_ited(apps, alias)
    if empresa is None:
        return

    Produto = apps.get_model('produtos', 'Produto')
    produtos = Produto.objects.using(alias).filter(
        filial__empresa_id=empresa.pk,
        descricao__in=IMAGENS_HORTIFRUTI,
        foto_url='',
    )
    for produto in produtos.iterator():
        produto.foto_url = IMAGENS_HORTIFRUTI[produto.descricao]
        produto.save(using=alias, update_fields=['foto_url'])


def remover_imagens_hortifruti(apps, schema_editor):
    alias = schema_editor.connection.alias
    if alias == 'default' and getattr(settings, 'TENANT_DATABASE_ROUTING_ENABLED', False):
        return

    empresa = _empresa_ited(apps, alias)
    if empresa is None:
        return

    Produto = apps.get_model('produtos', 'Produto')
    for descricao, foto_url in IMAGENS_HORTIFRUTI.items():
        Produto.objects.using(alias).filter(
            filial__empresa_id=empresa.pk,
            descricao=descricao,
            foto_url=foto_url,
        ).update(foto_url='')


class Migration(migrations.Migration):

    dependencies = [
        ('produtos', '0044_unidades_comerciais_e_catalogo_ited'),
    ]

    operations = [
        migrations.RunPython(
            cadastrar_imagens_hortifruti,
            remover_imagens_hortifruti,
        ),
    ]
