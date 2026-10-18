import SwiftUI

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Group {
            switch model.phase {
            case .login: LoginView()
            case .chooseContext: ContextPickerView()
            case .locked: LockedView()
            case .ready: MainTabView()
            }
        }
        .overlay {
            if model.isBusy {
                ZStack {
                    Color.black.opacity(0.16).ignoresSafeArea()
                    ProgressView("Aguarde…")
                        .padding(24)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18))
                }
            }
        }
        .alert("Atenção", isPresented: errorPresented) {
            Button("OK") { model.errorMessage = nil }
        } message: { Text(model.errorMessage ?? "") }
        .alert("Tudo certo", isPresented: successPresented) {
            Button("OK") { model.successMessage = nil }
        } message: { Text(model.successMessage ?? "") }
    }

    private var errorPresented: Binding<Bool> {
        Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })
    }
    private var successPresented: Binding<Bool> {
        Binding(get: { model.successMessage != nil }, set: { if !$0 { model.successMessage = nil } })
    }
}
private struct LoginView: View {
    @Environment(AppModel.self) private var model
    @State private var email = ""
    @State private var password = ""
    @State private var remember = true

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 22) {
                    Spacer(minLength: 36)
                    ITEDLogo()
                    VStack(spacing: 6) {
                        Text("Força de Vendas").font(.largeTitle.bold())
                        Text("Pedidos, checkout e separação no seu iPhone")
                            .foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }
                    VStack(spacing: 14) {
                        TextField("E-mail", text: $email)
                            .textContentType(.username).keyboardType(.emailAddress)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                        SecureField("Senha", text: $password).textContentType(.password)
                        Toggle("Salvar acesso com a segurança do iPhone", isOn: $remember)
                            .font(.subheadline)
                    }
                    .textFieldStyle(.roundedBorder)
                    Button {
                        Task { await model.login(email: email, password: password, remember: remember) }
                    } label: {
                        Label("Entrar no iTED", systemImage: "arrow.right.circle.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .itedPrimaryButton()
                    .disabled(email.isEmpty || password.isEmpty || model.isBusy)
                }
                .padding(24)
            }
            .background(Color(.systemGroupedBackground))
        }
    }
}

private struct ContextPickerView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        NavigationStack {
            List(model.contexts) { context in
                Button { model.selectContext(context) } label: {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(context.filialNome).font(.headline)
                        Text(context.empresaNome).font(.subheadline).foregroundStyle(.secondary)
                        Text(context.filialCnpj).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("Escolha a filial")
        }
    }
}

private struct LockedView: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        VStack(spacing: 22) {
            ITEDLogo()
            Text("Olá, \(model.session?.usuarioNome ?? "")").font(.title2.bold())
            Text("Confirme sua identidade para continuar.").foregroundStyle(.secondary)
            Button { Task { await model.unlock() } } label: {
                Label("Desbloquear", systemImage: "faceid").frame(maxWidth: .infinity)
            }
            .itedPrimaryButton()
            Button("Entrar com outra conta", role: .destructive) { model.logout() }
        }
        .padding(28)
        .task { await model.unlock() }
    }
}

private enum MainRoute: Hashable {
    case checkout
    case pendingSales
    case newOrder
    case orders
    case clients
    case separation
}

private struct MainTabView: View {
    var body: some View {
        NavigationStack {
            HomeView()
                .navigationDestination(for: MainRoute.self) { route in
                    switch route {
                    case .checkout: CheckoutView()
                    case .pendingSales: PendingSalesView()
                    case .newOrder: NewOrderView()
                    case .orders: OrdersView()
                    case .clients: ClientsView()
                    case .separation: SeparationView()
                    }
                }
        }
        .tint(ITEDStyle.orange)
    }
}

private struct HomeView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 12) {
                ITEDLogo()
                    .frame(maxHeight: 88)

                Button {
                    Task { await model.switchContext() }
                } label: {
                    ITEDCard {
                        HStack(spacing: 12) {
                            Image(systemName: "building.2")
                                .foregroundStyle(ITEDStyle.blue)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(model.session?.context.empresaNome ?? "")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text(model.session?.context.filialNome ?? "")
                                    .font(.headline)
                                    .foregroundStyle(.primary)
                                    .lineLimit(2)
                            }
                            Spacer(minLength: 8)
                            Image(systemName: "arrow.left.arrow.right")
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .buttonStyle(.plain)

                Text("Operação")
                    .font(.headline)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.top, 4)

                HomeActionLink(
                    route: .checkout,
                    title: "Checkout de venda",
                    subtitle: "Venda de balcão, pendência ou baixa imediata",
                    icon: "cart.fill",
                    color: ITEDStyle.orange,
                    enabled: model.session?.context.podeCheckout == true
                )
                HomeActionLink(
                    route: .pendingSales,
                    title: "Vendas pendentes",
                    subtitle: "Receber e dar baixa nas vendas aguardando caixa",
                    icon: "hourglass",
                    color: ITEDStyle.orange,
                    enabled: model.session?.context.podeCheckout == true
                )
                HomeActionLink(
                    route: .newOrder,
                    title: "Novo pedido",
                    subtitle: "Cliente, produtos, preço e pagamento",
                    icon: "doc.badge.plus",
                    color: ITEDStyle.blue,
                    enabled: model.session?.context.podeCriarPedido == true
                )
                HomeActionLink(
                    route: .orders,
                    title: "Meus pedidos",
                    subtitle: "Rascunhos, envio e acompanhamento",
                    icon: "doc.text.fill",
                    color: ITEDStyle.blue
                )
                HomeActionLink(
                    route: .clients,
                    title: "Clientes",
                    subtitle: "Carteira, crédito e cadastro completo",
                    icon: "person.2.fill",
                    color: .indigo,
                    enabled: model.session?.context.podeCriarPedido == true
                )
                HomeActionLink(
                    route: .separation,
                    title: "Separação",
                    subtitle: "Depósito, lote e quantidade conferida",
                    icon: "shippingbox.fill",
                    color: .teal,
                    enabled: model.session?.context.podeSeparar == true
                )
            }
            .padding()
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle("ITED Força de Vendas")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                SyncButton()
                Button {
                    model.theme = colorScheme == .dark ? .light : .dark
                } label: {
                    Image(systemName: colorScheme == .dark ? "sun.max.fill" : "moon.fill")
                }
                .accessibilityLabel(colorScheme == .dark ? "Usar tema claro" : "Usar tema escuro")
                Button(role: .destructive) { model.logout() } label: {
                    Image(systemName: "rectangle.portrait.and.arrow.right")
                }
                .accessibilityLabel("Sair")
            }
        }
    }
}

private struct HomeActionLink: View {
    let route: MainRoute
    let title: String
    let subtitle: String
    let icon: String
    let color: Color
    var enabled = true

    var body: some View {
        NavigationLink(value: route) {
            ITEDCard {
                HStack(spacing: 14) {
                    Image(systemName: icon)
                        .font(.title3)
                        .foregroundStyle(enabled ? color : .secondary)
                        .frame(width: 28)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(title)
                            .font(.headline)
                            .foregroundStyle(.primary)
                        Text(enabled ? subtitle : "Sem permissão nesta filial")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.right")
                        .font(.caption.bold())
                        .foregroundStyle(.tertiary)
                }
            }
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.58)
    }
}

struct SyncButton: View {
    @Environment(AppModel.self) private var model
    @State private var showSheet = false
    var body: some View {
        Button { showSheet = true } label: {
            Image(systemName: model.lastSync == nil ? "wifi.exclamationmark" : "wifi")
                .foregroundStyle(model.lastSync == nil ? .red : .green)
                .font(.title3).padding(10).background(.thinMaterial, in: Circle())
        }
        .sheet(isPresented: $showSheet) {
            NavigationStack {
                VStack(spacing: 16) {
                    Image(systemName: "arrow.triangle.2.circlepath").font(.system(size: 48)).foregroundStyle(ITEDStyle.blue)
                    Text("Central de sincronização").font(.title2.bold())
                    if let date = model.lastSync { Text("Última sincronização: \(date.formatted(date: .abbreviated, time: .shortened))") }
                    else { Text("Ainda não sincronizado nesta sessão.") }
                    Button("Sincronizar agora") { Task { await model.synchronize(); showSheet = false } }
                        .itedPrimaryButton()
                    Spacer()
                }.padding().navigationTitle("Sincronização").navigationBarTitleDisplayMode(.inline)
            }.presentationDetents([.medium])
        }
    }
}
