import Foundation
import LocalAuthentication
import Observation

enum AppPhase: Sendable {
    case login
    case chooseContext
    case locked
    case ready
}

enum AppTheme: String, CaseIterable, Identifiable {
    case system = "Sistema"
    case light = "Claro"
    case dark = "Escuro"
    var id: String { rawValue }
}

@MainActor
@Observable
final class AppModel {
    var phase: AppPhase = .login
    var session: UserSession?
    var contexts: [WorkContext] = []
    var theme: AppTheme = .system {
        didSet { UserDefaults.standard.set(theme.rawValue, forKey: "ited.theme") }
    }
    var isBusy = false
    var errorMessage: String?
    var successMessage: String?
    var lastSync: Date?
    var pendingOrderCount = 0

    let api = APIClient()
    private let credentials = CredentialStore()
    private let offline = OfflineStore()
    private var pendingTokens: AuthTokens?
    private var pendingUserName = ""
    private var pendingUserEmail = ""
    private var pendingRemember = true

    init() {
        if let savedTheme = UserDefaults.standard.string(forKey: "ited.theme"),
           let theme = AppTheme(rawValue: savedTheme) {
            self.theme = theme
        }
        if let restored = credentials.load() {
            session = restored
            lastSync = offline.snapshot(for: restored)?.synchronizedAt
            pendingOrderCount = offline.queuedOrders(for: restored).count
            phase = .locked
        }
    }

    var colorScheme: ColorSchemeChoice {
        switch theme {
        case .system: .system
        case .light: .light
        case .dark: .dark
        }
    }

    func login(email: String, password: String, remember: Bool) async {
        await perform {
            let tokens = try await api.login(email: email, password: password)
            let result = try await api.contexts(accessToken: tokens.access)
            pendingTokens = tokens
            pendingUserName = result.0
            pendingUserEmail = result.1
            pendingRemember = remember
            contexts = result.2
            guard let first = contexts.first else { throw APIError.http(403, "Nenhuma filial liberada para este usuário.") }
            if contexts.count == 1 { selectContext(first, remember: remember) }
            else { phase = .chooseContext }
        }
    }

    func selectContext(_ context: WorkContext, remember: Bool? = nil) {
        if var current = session {
            if let persisted = credentials.load(),
               persisted.usuarioEmail.caseInsensitiveCompare(current.usuarioEmail) == .orderedSame {
                current.tokens = persisted.tokens
            }
            current.context = context
            session = current
        } else if let tokens = pendingTokens {
            session = UserSession(tokens: tokens, usuarioNome: pendingUserName, usuarioEmail: pendingUserEmail, context: context)
        }
        guard let session else { return }
        if remember ?? pendingRemember { credentials.save(session) }
        let snapshot = offline.snapshot(for: session)
        lastSync = snapshot?.synchronizedAt
        pendingOrderCount = offline.queuedOrders(for: session).count
        phase = .ready
    }

    func switchContext() async {
        guard let session else { return }
        await perform {
            let result = try await api.contexts(session: session)
            contexts = result.2
            phase = .chooseContext
        }
    }

    func unlock() async {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            phase = .ready
            return
        }
        do {
            if try await context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Acessar o iTED Força de Vendas") {
                phase = .ready
            }
        } catch {
            errorMessage = "Não foi possível confirmar sua identidade."
        }
    }

    func synchronize() async {
        guard let session else { return }
        await perform {
            async let clientsRequest = api.clients(session: session, fullSync: true)
            async let optionsRequest = api.orderOptions(session: session)
            let (clients, options) = try await (clientsRequest, optionsRequest)
            var catalogs: [String: [Product]] = [:]
            for group in Dictionary(grouping: clients, by: { $0.tabelaPrecoId }).values {
                guard let representative = group.first else { continue }
                catalogs[String(representative.tabelaPrecoId)] = try await api.orderProducts(
                    session: session, clientID: representative.id, fullSync: true
                )
            }
            let now = Date()
            offline.save(
                snapshot: OfflineSnapshot(
                    clients: clients, options: options, catalogs: catalogs, synchronizedAt: now
                ),
                for: session
            )
            var sent = 0
            for order in offline.queuedOrders(for: session) {
                do {
                    _ = try await api.createOrder(session: session, queued: order)
                    offline.removeQueuedOrder(id: order.id)
                    sent += 1
                } catch APIError.offline {
                    break
                }
            }
            lastSync = now
            pendingOrderCount = offline.queuedOrders(for: session).count
            successMessage = sent == 0
                ? "Clientes, produtos e opções atualizados para uso offline."
                : "Dados atualizados e \(sent) pedido(s) pendente(s) enviado(s)."
        }
    }

    func clients(query: String) async throws -> [Client] {
        guard let session else { return [] }
        do { return try await api.clients(session: session, query: query) }
        catch APIError.offline {
            guard let cached = offline.snapshot(for: session)?.clients else { throw APIError.offline }
            let term = query.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current)
            return cached.filter { client in
                term.isEmpty || [client.nome, client.cpfCnpj, client.cidadeUf].contains { value in
                    value.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current)
                        .localizedCaseInsensitiveContains(term)
                }
            }.prefix(50).map { $0 }
        }
    }

    func orderOptions() async throws -> OrderOptions {
        guard let session else { throw APIError.invalidResponse }
        do { return try await api.orderOptions(session: session) }
        catch APIError.offline {
            guard let cached = offline.snapshot(for: session)?.options else { throw APIError.offline }
            return cached
        }
    }

    func orderProducts(client: Client, query: String, categoryID: Int?, featured: Bool) async throws -> [Product] {
        guard let session else { return [] }
        do {
            return try await api.orderProducts(
                session: session, clientID: client.id, query: query,
                categoryID: categoryID, featured: featured
            )
        } catch APIError.offline {
            guard let products = offline.snapshot(for: session)?.catalogs[String(client.tabelaPrecoId)] else {
                throw APIError.offline
            }
            let term = query.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current)
            return products.filter { product in
                let matchesQuery = term.isEmpty || [product.descricao, product.codigo].contains { value in
                    value.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current)
                        .localizedCaseInsensitiveContains(term)
                }
                let matchesFilter = featured ? product.destaque : (categoryID == nil || product.categoriaId == categoryID)
                return matchesQuery && matchesFilter
            }.prefix(50).map { $0 }
        }
    }

    func submitOrder(
        client: Client, lines: [OrderDraftLine], payment: PaymentOption,
        condition: PaymentCondition?, priority: OrderPriority, deliveryDate: Date?, notes: String
    ) async throws -> OrderSubmission {
        guard let session else { throw APIError.invalidResponse }
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        let order = QueuedOrder(
            id: UUID(), contextKey: session.context.id, userEmail: session.usuarioEmail.lowercased(),
            idempotencyKey: UUID().uuidString, clientId: client.id, paymentId: payment.id,
            conditionId: condition?.id,
            paymentTermDays: condition?.diasPrimeiraParcela ?? client.prazoPagamentoDias,
            priority: priority.codigo, deliveryDate: deliveryDate.map(formatter.string), notes: notes,
            items: lines.map { QueuedOrderItem(productId: $0.product.id, quantity: $0.quantity.apiString) },
            createdAt: Date()
        )
        do { return .sent(try await api.createOrder(session: session, queued: order)) }
        catch APIError.offline {
            offline.enqueue(order)
            pendingOrderCount = offline.queuedOrders(for: session).count
            return .queued
        }
    }

    func logout() {
        credentials.clear()
        Task { await api.clearTokens() }
        session = nil
        contexts = []
        phase = .login
    }

    func perform(_ operation: () async throws -> Void) async {
        isBusy = true
        errorMessage = nil
        defer { isBusy = false }
        do { try await operation() }
        catch { errorMessage = error.localizedDescription }
    }
}

enum ColorSchemeChoice { case system, light, dark }
