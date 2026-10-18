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

    let api = APIClient()
    private let credentials = CredentialStore()
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
            lastSync = Date()
        }
    }

    func selectContext(_ context: WorkContext, remember: Bool? = nil) {
        if var current = session {
            current.context = context
            session = current
        } else if let tokens = pendingTokens {
            session = UserSession(tokens: tokens, usuarioNome: pendingUserName, usuarioEmail: pendingUserEmail, context: context)
        }
        guard let session else { return }
        if remember ?? pendingRemember { credentials.save(session) }
        phase = .ready
    }

    func switchContext() async {
        guard let session else { return }
        await perform {
            let result = try await api.contexts(accessToken: session.tokens.access)
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
            _ = try await api.contexts(accessToken: session.tokens.access)
            lastSync = Date()
            successMessage = "Dados sincronizados com o iTED."
        }
    }

    func logout() {
        credentials.clear()
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
