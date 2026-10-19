import SwiftUI

struct OrdersView: View {
    @Environment(AppModel.self) private var model
    @State private var orders: [OrderSummary] = []

    var body: some View {
        List(orders) { order in
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text("Pedido #\(order.numeroPedido)").font(.headline)
                    Spacer()
                    Text(order.statusDescricao).font(.caption.bold()).foregroundStyle(ITEDStyle.blue)
                }
                Text(order.clienteNome)
                HStack { Text(order.dataEmissao).font(.caption).foregroundStyle(.secondary); Spacer(); Text(Decimal.parse(order.valorTotal).currency).bold() }
            }.padding(.vertical, 5)
        }
        .overlay { if orders.isEmpty && !model.isBusy { ContentUnavailableView("Nenhum pedido", systemImage: "doc.text") } }
        .navigationTitle("Pedidos")
        .toolbar { ToolbarItem(placement: .topBarTrailing) { NavigationLink { NewOrderView() } label: { Image(systemName: "plus") } } }
        .refreshable { await load() }
        .task { await load() }
    }

    private func load() async {
        guard let session = model.session else { return }
        await model.perform { orders = try await model.api.orders(session: session); model.lastSync = Date() }
    }
}

private struct OrderStepHeader: View {
    let step: String
    let title: String
    var caption: String? = nil
    var accent: Color = ITEDStyle.blue

    var body: some View {
        HStack(spacing: 10) {
            Text(step)
                .font(.caption.bold())
                .foregroundStyle(.white)
                .frame(width: 26, height: 26)
                .background(accent, in: Circle())
                .accessibilityHidden(true)
            Text(title)
                .font(.headline)
            Spacer(minLength: 8)
            if let caption {
                Text(caption)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.trailing)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Etapa \(step), \(title)\(caption.map { ", \($0)" } ?? "")")
    }
}

private struct OrderSectionCard<Content: View>: View {
    @Environment(\.colorScheme) private var colorScheme
    let accent: Color
    let content: Content

    init(accent: Color, @ViewBuilder content: () -> Content) {
        self.accent = accent
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            accent.opacity(colorScheme == .dark ? 0.14 : 0.08),
            in: RoundedRectangle(cornerRadius: ITEDStyle.radius)
        )
        .overlay {
            RoundedRectangle(cornerRadius: ITEDStyle.radius)
                .stroke(accent.opacity(0.20), lineWidth: 1)
        }
    }
}

private struct OrderMenuLabel: View {
    let title: String
    let value: String
    let icon: String
    let accent: Color

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icon)
                .foregroundStyle(accent)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(value)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            Image(systemName: "chevron.up.chevron.down")
                .font(.caption.weight(.semibold))
                .foregroundStyle(accent)
        }
        .padding(.horizontal, 12)
        .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
        .background(accent.opacity(0.10), in: RoundedRectangle(cornerRadius: 12))
        .overlay {
            RoundedRectangle(cornerRadius: 12)
                .stroke(accent.opacity(0.24), lineWidth: 1)
        }
    }
}

struct NewOrderView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var options: OrderOptions?
    @State private var client: Client?
    @State private var clientQuery = ""
    @State private var clientResults: [Client] = []
    @State private var category: ProductCategory?
    @State private var featuredOnly = false
    @State private var productQuery = ""
    @State private var products: [Product] = []
    @State private var lines: [OrderDraftLine] = []
    @State private var payment: PaymentOption?
    @State private var condition: PaymentCondition?
    @State private var priority: OrderPriority?
    @State private var hasDeliveryDate = true
    @State private var deliveryDate = Calendar.current.date(byAdding: .day, value: 5, to: Date()) ?? Date()
    @State private var notes = ""
    @State private var searchTask: Task<Void, Never>?
    @State private var removalCandidateID: Int?

    private var total: Decimal { lines.reduce(0) { $0 + $1.total } }

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                clientSection
                productSection
                if !lines.isEmpty {
                    orderItemsSection
                    commercialSection
                    submitSection
                }
            }
            .padding()
        }
        .scrollDismissesKeyboard(.interactively)
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Novo pedido")
        .navigationBarTitleDisplayMode(.inline)
        .task { await loadOptions() }
        .confirmationDialog(
            "Remover produto do pedido?",
            isPresented: removalConfirmationPresented,
            titleVisibility: .visible
        ) {
            Button("Remover produto", role: .destructive) {
                if let removalCandidateID { lines.removeProduct(id: removalCandidateID) }
                removalCandidateID = nil
            }
            Button("Cancelar", role: .cancel) { removalCandidateID = nil }
        } message: {
            Text("O produto só será removido depois desta confirmação.")
        }
    }

    private var clientSection: some View {
        OrderSectionCard(accent: ITEDStyle.blue) {
            OrderStepHeader(step: "1", title: "Cliente", caption: "Obrigatório")
            if let client {
                Divider().overlay(ITEDStyle.blue.opacity(0.25))
                HStack(spacing: 12) {
                    Image(systemName: "person.crop.circle.fill")
                        .font(.title2)
                        .foregroundStyle(ITEDStyle.blue)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(client.nome).font(.subheadline.weight(.semibold)).lineLimit(2)
                        let detail = [client.cpfCnpj, client.cidadeUf].filter { !$0.isEmpty }.joined(separator: " • ")
                        if !detail.isEmpty { Text(detail).font(.caption).foregroundStyle(.secondary) }
                        Text("Crédito disponível: \(Decimal.parse(client.creditoDisponivel).currency)")
                            .font(.caption.weight(.semibold))
                        Text("Tabela: \(client.tabelaPreco)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 8)
                    Button("Trocar") {
                        self.client = nil
                        clientQuery = ""
                        clientResults = []
                        products = []
                        category = nil
                        featuredOnly = false
                        lines.removeAll()
                    }
                    .buttonStyle(.bordered)
                    .buttonBorderShape(.capsule)
                    .tint(ITEDStyle.blue)
                }
            } else {
                TextField("Buscar por nome, CPF ou CNPJ", text: $clientQuery)
                    .textFieldStyle(.roundedBorder)
                    .submitLabel(.search)
                    .onChange(of: clientQuery) { _, value in searchClients(value) }
                if !clientResults.isEmpty {
                    SearchResultPanel {
                        LazyVStack(spacing: 0) {
                            ForEach(clientResults.prefix(5)) { item in
                                Button {
                                    client = item
                                    clientQuery = ""
                                    clientResults = []
                                    products = []
                                    category = nil
                                    featuredOnly = false
                                    lines.removeAll()
                                } label: {
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(item.nome).foregroundStyle(.primary)
                                        let detail = [item.cpfCnpj, item.cidadeUf].filter { !$0.isEmpty }.joined(separator: " • ")
                                        if !detail.isEmpty { Text(detail).font(.caption).foregroundStyle(.secondary) }
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(12)
                                }
                                Divider()
                            }
                        }
                    }
                }
            }
        }
    }

    private var productSection: some View {
        OrderSectionCard(accent: ITEDStyle.orange) {
            OrderStepHeader(
                step: "2",
                title: "Produtos",
                caption: client == nil ? "Selecione o cliente" : "\(lines.count) no pedido",
                accent: ITEDStyle.orange
            )
            if client == nil {
                Text("A busca de produtos será liberada depois que você selecionar o cliente.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .padding(.top, 10)
            } else {
                Menu {
                    Button("Produtos em destaque", systemImage: "star") {
                        category = nil
                        featuredOnly = true
                        Task { await searchProducts() }
                    }
                    Button("Todas as categorias", systemImage: "square.grid.2x2") {
                        category = nil
                        featuredOnly = false
                        Task { await searchProducts() }
                    }
                    ForEach(options?.categories ?? []) { item in
                        Button(item.nome) {
                            category = item
                            featuredOnly = false
                            Task { await searchProducts() }
                        }
                    }
                } label: {
                    OrderMenuLabel(
                        title: "Categoria dos produtos",
                        value: selectedProductFilterLabel,
                        icon: "list.bullet",
                        accent: ITEDStyle.orange
                    )
                }
                .buttonStyle(.plain)
                HStack(spacing: 8) {
                    TextField("Nome, código ou código de barras", text: $productQuery)
                        .textFieldStyle(.roundedBorder)
                        .submitLabel(.search)
                        .onSubmit { Task { await searchProducts() } }
                    Button { Task { await searchProducts() } } label: {
                        Image(systemName: "magnifyingglass")
                            .frame(minWidth: 28, minHeight: 28)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(ITEDStyle.orange)
                }
                if !products.isEmpty {
                    SearchResultPanel {
                        LazyVStack(spacing: 0) {
                            ForEach(products) { product in
                                Button { add(product) } label: {
                                    HStack {
                                        VStack(alignment: .leading, spacing: 3) {
                                            Text(product.descricao).foregroundStyle(.primary).lineLimit(2)
                                            Text("\(product.categoria) • Estoque: \(product.estoqueDisponivel) \(product.unidade)")
                                                .font(.caption)
                                                .foregroundStyle(product.podeVender ? Color.secondary : Color.red)
                                        }
                                        Spacer(minLength: 8)
                                        Text(Decimal.parse(product.preco).currency)
                                            .fontWeight(.semibold)
                                            .foregroundStyle(ITEDStyle.orange)
                                    }
                                    .padding(12)
                                }
                                .disabled(!product.podeVender)
                                Divider()
                            }
                        }
                    }
                }
                Text("Escolha uma categoria ou digite para pesquisar.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var orderItemsSection: some View {
        OrderSectionCard(accent: ITEDStyle.blue) {
            OrderStepHeader(step: "3", title: "Itens do pedido", caption: "\(lines.count) item(ns)")
            ForEach(lines) { line in
                Divider().overlay(ITEDStyle.blue.opacity(0.20))
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .top, spacing: 10) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(line.product.descricao)
                                .font(.subheadline.bold())
                                .lineLimit(2)
                            Text("\(Decimal.parse(line.product.preco).currency)/\(line.product.unidade)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 6)
                        Button {
                            removalCandidateID = line.id
                        } label: {
                            Image(systemName: "trash")
                                .font(.subheadline.weight(.semibold))
                                .frame(width: 34, height: 34)
                        }
                        .buttonStyle(.bordered)
                        .buttonBorderShape(.circle)
                        .tint(.red)
                        .accessibilityLabel("Remover \(line.product.descricao)")
                    }
                    HStack(spacing: 7) {
                        Text("Qtd.")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                        Button {
                            lines.adjustQuantity(for: line.id, by: -1)
                        } label: {
                            Image(systemName: "minus")
                                .font(.caption.weight(.bold))
                                .frame(width: 20, height: 20)
                        }
                        .buttonStyle(.bordered)
                        .buttonBorderShape(.circle)
                        .controlSize(.small)
                        .accessibilityLabel("Diminuir quantidade de \(line.product.descricao)")
                        TextField(
                            "Qtd.",
                            value: quantityBinding(for: line.id),
                            format: .number.precision(.fractionLength(0...3))
                        )
                        .keyboardType(.decimalPad)
                        .multilineTextAlignment(.center)
                        .textFieldStyle(.roundedBorder)
                        .controlSize(.small)
                        .frame(width: 62)
                        Button {
                            lines.adjustQuantity(for: line.id, by: 1)
                        } label: {
                            Image(systemName: "plus")
                                .font(.caption.weight(.bold))
                                .frame(width: 20, height: 20)
                        }
                        .buttonStyle(.borderedProminent)
                        .buttonBorderShape(.circle)
                        .controlSize(.small)
                        .tint(ITEDStyle.orange)
                        .accessibilityLabel("Aumentar quantidade de \(line.product.descricao)")
                        Spacer()
                        VStack(alignment: .trailing, spacing: 1) {
                            Text("Subtotal").font(.caption2).foregroundStyle(.secondary)
                            Text(line.total.currency).font(.subheadline.bold())
                        }
                    }
                }
            }
        }
    }

    private var commercialSection: some View {
        OrderSectionCard(accent: ITEDStyle.orange) {
            OrderStepHeader(step: "4", title: "Condições comerciais", accent: ITEDStyle.orange)
            Menu {
                ForEach(options?.payments ?? []) { item in
                    Button(item.descricao) { payment = item }
                }
            } label: {
                OrderMenuLabel(
                    title: "Forma de pagamento",
                    value: payment?.descricao ?? "Selecione",
                    icon: "creditcard",
                    accent: ITEDStyle.orange
                )
            }
            .buttonStyle(.plain)
            if !(options?.conditions.isEmpty ?? true) {
                Menu {
                    ForEach(options?.conditions ?? []) { item in
                        Button(item.descricao) { condition = item }
                    }
                } label: {
                    OrderMenuLabel(
                        title: "Condição de pagamento",
                        value: condition?.descricao ?? options?.conditions.first?.descricao ?? "Selecione",
                        icon: "calendar.badge.clock",
                        accent: ITEDStyle.orange
                    )
                }
                .buttonStyle(.plain)
            }
            VStack(alignment: .leading, spacing: 8) {
                Text("Prioridade")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(options?.priorities ?? []) { item in
                            Button {
                                priority = item
                            } label: {
                                Text(item.descricao)
                                    .font(.subheadline.weight(.semibold))
                                    .foregroundStyle(priority == item ? Color.white : Color.primary)
                                    .padding(.horizontal, 14)
                                    .frame(minHeight: 36)
                                    .background(
                                        priority == item ? ITEDStyle.orange : Color.primary.opacity(0.07),
                                        in: Capsule()
                                    )
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            Divider().overlay(ITEDStyle.orange.opacity(0.22))
            Toggle("Informar entrega prevista", isOn: $hasDeliveryDate)
            if hasDeliveryDate {
                DatePicker("Data", selection: $deliveryDate, displayedComponents: .date)
                    .padding(.top, 4)
                HStack(spacing: 8) {
                    ForEach([5, 10, 15], id: \.self) { days in
                        Button("+\(days) dias") {
                            deliveryDate = Calendar.current.date(byAdding: .day, value: days, to: Date()) ?? Date()
                        }
                        .buttonStyle(.bordered)
                        .frame(maxWidth: .infinity)
                    }
                }
            }
            TextField("Observações", text: $notes, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(3...6)
                .padding(.top, 6)
        }
    }

    private var submitSection: some View {
        OrderSectionCard(accent: ITEDStyle.orange) {
            HStack {
                Text("TOTAL DO PEDIDO").font(.caption.weight(.semibold))
                Spacer()
                Text(total.currency).font(.title2.bold())
            }
            Button { Task { await submit() } } label: {
                Label("Enviar pedido", systemImage: "paperplane.fill")
                    .frame(maxWidth: .infinity, minHeight: 28)
            }
            .itedPrimaryButton()
            .disabled(client == nil || payment == nil || priority == nil || lines.isEmpty)
            .padding(.top, 6)
        }
    }

    private func loadOptions() async {
        await model.perform {
            options = try await model.orderOptions()
            priority = options?.priorities.first
            condition = options?.conditions.first
        }
    }

    private func searchClients(_ query: String) {
        searchTask?.cancel()
        guard query.count >= 2 else { clientResults = []; return }
        searchTask = Task {
            try? await Task.sleep(for: .milliseconds(350))
            guard !Task.isCancelled else { return }
            do { clientResults = try await model.clients(query: query) }
            catch { model.errorMessage = error.localizedDescription }
        }
    }

    private func searchProducts() async {
        guard let client else {
            model.errorMessage = "Selecione o cliente antes de buscar produtos."; return
        }
        await model.perform {
            products = try await model.orderProducts(
                client: client, query: productQuery,
                categoryID: category?.id, featured: featuredOnly
            )
        }
    }

    private func add(_ product: Product) {
        guard product.podeVender else {
            model.errorMessage = "\(product.descricao): \(product.bloqueios.joined(separator: ", "))"; return
        }
        if let index = lines.firstIndex(where: { $0.id == product.id }) { lines[index].quantity += 1 }
        else { lines.append(OrderDraftLine(product: product, quantity: 1)) }
        productQuery = ""
        products = []
        category = nil
        featuredOnly = false
    }

    private var selectedProductFilterLabel: String {
        if featuredOnly { return "Produtos em destaque" }
        return category?.nome ?? "Categoria dos produtos"
    }

    private func quantityBinding(for productID: Int) -> Binding<Decimal> {
        Binding(
            get: { lines.first { $0.id == productID }?.quantity ?? 0.001 },
            set: { lines.updateQuantity(for: productID, to: $0) }
        )
    }

    private var removalConfirmationPresented: Binding<Bool> {
        Binding(
            get: { removalCandidateID != nil },
            set: { if !$0 { removalCandidateID = nil } }
        )
    }

    private func submit() async {
        guard let client, let payment, let priority else { return }
        await model.perform {
            let result = try await model.submitOrder(
                client: client, lines: lines, payment: payment, condition: condition,
                priority: priority, deliveryDate: hasDeliveryDate ? deliveryDate : nil, notes: notes
            )
            switch result {
            case let .sent(order): model.successMessage = "Pedido #\(order.numeroPedido) enviado ao iTED."
            case .queued: model.successMessage = "Pedido salvo offline. Ele será enviado na próxima sincronização."
            }
            dismiss()
        }
    }
}
