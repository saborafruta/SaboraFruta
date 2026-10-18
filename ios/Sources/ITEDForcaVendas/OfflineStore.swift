import Foundation

@MainActor
struct OfflineStore {
    private let defaults = UserDefaults.standard
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    private func scope(_ session: UserSession) -> String {
        "\(session.context.id).\(session.usuarioEmail.lowercased())"
    }

    func snapshot(for session: UserSession) -> OfflineSnapshot? {
        guard let data = defaults.data(forKey: "ited.offline.snapshot.\(scope(session))") else { return nil }
        return try? decoder.decode(OfflineSnapshot.self, from: data)
    }

    func save(snapshot: OfflineSnapshot, for session: UserSession) {
        guard let data = try? encoder.encode(snapshot) else { return }
        defaults.set(data, forKey: "ited.offline.snapshot.\(scope(session))")
    }

    func queuedOrders() -> [QueuedOrder] {
        guard let data = defaults.data(forKey: "ited.offline.orders") else { return [] }
        return (try? decoder.decode([QueuedOrder].self, from: data)) ?? []
    }

    func queuedOrders(for session: UserSession) -> [QueuedOrder] {
        queuedOrders().filter {
            $0.contextKey == session.context.id &&
            $0.userEmail == session.usuarioEmail.lowercased()
        }
    }

    func enqueue(_ order: QueuedOrder) {
        var orders = queuedOrders()
        orders.append(order)
        save(orders: orders)
    }

    func removeQueuedOrder(id: UUID) {
        save(orders: queuedOrders().filter { $0.id != id })
    }

    private func save(orders: [QueuedOrder]) {
        guard let data = try? encoder.encode(orders) else { return }
        defaults.set(data, forKey: "ited.offline.orders")
    }
}
