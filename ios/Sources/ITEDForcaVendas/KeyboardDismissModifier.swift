import SwiftUI
import UIKit

private struct KeyboardDismissInstaller: UIViewRepresentable {
    func makeUIView(context: Context) -> KeyboardDismissView {
        KeyboardDismissView()
    }

    func updateUIView(_ uiView: KeyboardDismissView, context: Context) {}

    static func dismantleUIView(_ uiView: KeyboardDismissView, coordinator: ()) {
        uiView.detach()
    }
}

private final class KeyboardDismissView: UIView, UIGestureRecognizerDelegate {
    private weak var installedWindow: UIWindow?
    private lazy var dismissRecognizer: UITapGestureRecognizer = {
        let recognizer = UITapGestureRecognizer(target: self, action: #selector(dismissKeyboard))
        recognizer.cancelsTouchesInView = false
        recognizer.delegate = self
        return recognizer
    }()

    override func didMoveToWindow() {
        super.didMoveToWindow()
        guard installedWindow !== window else { return }
        detach()
        installedWindow = window
        window?.addGestureRecognizer(dismissRecognizer)
    }

    func detach() {
        installedWindow?.removeGestureRecognizer(dismissRecognizer)
        installedWindow = nil
    }

    @objc private func dismissKeyboard() {
        installedWindow?.endEditing(true)
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
        var touchedView = touch.view
        while let current = touchedView {
            if current is UITextField || current is UITextView { return false }
            touchedView = current.superview
        }
        return true
    }
}

extension View {
    /// Fecha o teclado ao tocar fora de um campo sem impedir botões, menus ou rolagem.
    func dismissKeyboardOnBackgroundTap() -> some View {
        background(KeyboardDismissInstaller().frame(width: 0, height: 0))
    }
}
