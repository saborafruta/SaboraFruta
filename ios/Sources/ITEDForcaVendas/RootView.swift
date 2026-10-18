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

private struct MainTabView: View {
    var body: some View {
        TabView {
            NavigationStack { HomeView() }
                .tabItem { Label("Início", systemImage: "house.fill") }
            NavigationStack { CheckoutView() }
                .tabItem { Label("Checkout", systemImage: "cart.fill") }
            NavigationStack { OrdersView() }
                .tabItem { Label("Pedidos", systemImage: "doc.text.fill") }
            NavigationStack { ClientsView() }
                .tabItem { Label("Clientes", systemImage: "person.2.fill") }
            NavigationStack { MoreView() }
                .tabItem { Label("Mais", systemImage: "ellipsis.circle.fill") }
        }
        .tint(ITEDStyle.orange)
    }
}

private struct HomeView: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                HStack {
                    ITEDLogo(compact: true)
                    VStack(alignment: .leading) {
                        Text("Olá, \(model.session?.usuarioNome ?? "")").font(.headline)
                        Text(model.session?.context.filialNome ?? "").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    SyncButton()
                }
                ITEDCard {
                    Label("Operação integrada", systemImage: "checkmark.seal.fill").foregroundStyle(.green)
                    Text("Consulte dados atualizados do iTED e acompanhe o envio das vendas e pedidos.")
                        .font(.subheadline).foregroundStyle(.secondary).padding(.top, 4)
                }
                LazyVGrid(columns: [.init(.flexible()), .init(.flexible())], spacing: 12) {
                    HomeTile(title: "Checkout", subtitle: "Venda rápida", icon: "cart.fill", color: ITEDStyle.orange)
                    HomeTile(title: "Pedidos", subtitle: "Força de vendas", icon: "doc.badge.plus", color: ITEDStyle.blue)
                    HomeTile(title: "Clientes", subtitle: "Busca e cadastro", icon: "person.crop.circle.badge.plus", color: .indigo)
                    HomeTile(title: "Separação", subtitle: "Estoque e lotes", icon: "shippingbox.fill", color: .teal)
                }
            }.padding()
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle("iTED")
    }
}

private struct HomeTile: View {
    let title: String; let subtitle: String; let icon: String; let color: Color
    var body: some View {
        ITEDCard {
            Image(systemName: icon).font(.title).foregroundStyle(color)
            Text(title).font(.headline).padding(.top, 8)
            Text(subtitle).font(.caption).foregroundStyle(.secondary)
        }.frame(minHeight: 128)
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

private struct MoreView: View {
    @Environment(AppModel.self) private var model
    var body: some View {
        List {
            Section("Operação") {
                NavigationLink { SeparationView() } label: { Label("Separação", systemImage: "shippingbox") }
                NavigationLink { PendingSalesView() } label: { Label("Vendas pendentes", systemImage: "hourglass") }
                Button { Task { await model.switchContext() } } label: { Label("Trocar filial", systemImage: "building.2") }
            }
            Section("Aparência") {
                Picker("Tema", selection: Binding(
                    get: { model.theme },
                    set: { model.theme = $0 }
                )) {
                    ForEach(AppTheme.allCases) { Text($0.rawValue).tag($0) }
                }
            }
            Section { Button("Sair", role: .destructive) { model.logout() } }
        }.navigationTitle("Mais")
    }
}
