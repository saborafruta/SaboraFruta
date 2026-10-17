# ITED Força de Vendas — Android

Aplicativo Android nativo integrado ao ERP ITED.

## Parâmetros da primeira versão

- pacote: `br.com.ited.forcavendas`;
- Android mínimo: 9 / API 28;
- Android alvo e compilação: 16 / API 36;
- interface: Kotlin + Jetpack Compose;
- tema claro: branco/cinza com laranja;
- tema escuro: preto/cinza com azul;
- API padrão: `https://ited.app.br/`, substituível pela propriedade Gradle
  `ITED_API_BASE_URL`.

## Ambiente necessário

- JDK 17;
- Android SDK Platform 36 e Build Tools 36;
- Android Studio compatível com AGP 9.4;
- Gradle 9.6.

O wrapper do Gradle já faz parte do projeto. Para gerar a versão técnica de
desenvolvimento:

```powershell
.\gradlew.bat :app:assembleDebug
```

O APK será criado em `app/build/outputs/apk/debug/app-debug.apk`.

## Princípios de sincronização

- o ITED online é a fonte oficial;
- dados mestres recebidos do servidor são somente leitura no cache local;
- rascunhos e operações ainda não confirmadas ficam na fila local;
- um pedido só aparece como recebido após o servidor devolver seu ID e número;
- toda escrita usa identificador idempotente para não duplicar pedidos;
- preço, crédito, estoque, depósito e lote são revalidados no servidor;
- a baixa definitiva da separação exige conexão.
