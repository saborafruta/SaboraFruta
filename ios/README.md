# iTED Força de Vendas — iOS

Aplicativo SwiftUI nativo, empacotado com Swift Package Manager e preparado para o
[xTool](https://github.com/xtool-org/xtool). A API utilizada é a mesma da versão
Android (`https://ited.app.br/api/v1/mobile/`).

## Entregue nesta primeira versão

- login com seleção e troca de filial;
- sessão salva no Keychain e desbloqueio pela segurança do iPhone;
- tema claro, escuro ou conforme o sistema;
- indicador e central de sincronização;
- checkout com cliente balcão, busca de clientes e produtos, depósitos, carrinho,
  desconto em reais ou percentual, pagamento, pendência e finalização sem sair da tela;
- consulta e cadastro completo de clientes, com preenchimento por CNPJ e CEP;
- consulta e criação de pedidos, categorias, destaques, calendário e atalhos de 5,
  10 e 15 dias;
- fila de separação com seleção de depósito, lote e quantidade;
- logo do iTED na interface e no ícone do aplicativo.

Não há emissão fiscal no aplicativo.

## Compilar no Windows com xTool

O xTool roda dentro do WSL. Depois de instalar uma distribuição Ubuntu, Swift 6.4,
as dependências do `libimobiledevice`, o xTool e o SDK extraído do Xcode 27:

```bash
cd /mnt/c/Users/diego/Documents/ChatGPT/ited/ios
xtool setup
xtool dev
```

Para gerar uma IPA:

```bash
xtool dev build --ipa
```

É necessário conectar um iPhone por USB, habilitar o Modo de Desenvolvedor e entrar
com uma conta Apple para que o aparelho, certificado e perfil sejam registrados.
O Windows não oferece o simulador oficial do iOS; a validação final é feita em um
iPhone físico ou em um Mac com simulador.
