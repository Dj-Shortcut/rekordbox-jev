import AppKit
import SwiftUI
import Security
import LocalAuthentication

private enum JevKeychain {
    static var query: [String: Any] { [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "local.rekordbox.jev-widget.typesafe",
        kSecAttrAccount as String: "TypeSafe API"
    ] }
    static func error(_ status: OSStatus) -> NSError {
        NSError(domain:"JevKeychain",code:Int(status),userInfo:[NSLocalizedDescriptionKey:
            (SecCopyErrorMessageString(status,nil) as String?) ?? "Sleutelhanger niet beschikbaar (\(status))."])
    }
    static func exists() -> Bool {
        var request = query
        request[kSecReturnAttributes as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        let context = LAContext()
        context.interactionNotAllowed = true
        request[kSecUseAuthenticationContext as String] = context
        let status = SecItemCopyMatching(request as CFDictionary,nil)
        return status == errSecSuccess || status == errSecInteractionNotAllowed
    }
    static func save(_ secret: String) throws {
        let data = Data(secret.utf8)
        let status = SecItemUpdate(query as CFDictionary,[kSecValueData as String:data] as CFDictionary)
        if status == errSecItemNotFound {
            var request = query
            request[kSecValueData as String] = data
            request[kSecAttrLabel as String] = "Jev Widget — TypeSafe API"
            let added = SecItemAdd(request as CFDictionary,nil)
            guard added == errSecSuccess else { throw error(added) }
        } else if status != errSecSuccess { throw error(status) }
    }
    static func load() throws -> String {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        // A DJ test must not interrupt playback with a password dialog.
        // This does not change or relax the Keychain item's access permissions.
        let context = LAContext()
        context.interactionNotAllowed = true
        request[kSecUseAuthenticationContext as String] = context
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary,&result)
        if status == errSecInteractionNotAllowed || status == errSecAuthFailed {
            throw NSError(domain:"JevKeychain",code:Int(status),userInfo:[NSLocalizedDescriptionKey:
                "macOS geeft de bewaarde sleutel niet automatisch vrij. Proef niet gestart; er wordt geen wachtwoordvenster geopend."])
        }
        guard status == errSecSuccess else { throw error(status) }
        guard let data = result as? Data, let secret = String(data:data,encoding:.utf8), !secret.isEmpty else {
            throw error(errSecDecode)
        }
        return secret
    }
}

final class JevControls: ObservableObject {
    @Published var keyInput = ""
    @Published var hasKey = false
    @Published var editingKey = true
    @Published var savingKey = false
    @Published var running = false
    @Published var status = "Plak hieronder eenmalig je TypeSafe-sleutel."
    let root: URL
    private var process: Process?
    private var stopRequested = false
    private var sessionSecret: String?

    init(root: URL) {
        self.root = root
        DispatchQueue.global(qos:.userInitiated).async { [weak self] in
            let present = JevKeychain.exists()
            DispatchQueue.main.async {
                guard let self = self, !self.savingKey, !self.running else { return }
                self.hasKey = present
                if present && self.keyInput.isEmpty {
                    self.editingKey = false
                    self.status = "Klaar om te starten."
                }
            }
        }
    }
    static func validSecret(_ secret: String) -> Bool {
        !secret.isEmpty && secret.utf8.count <= 4096 && !secret.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) }
    }
    func saveKey() {
        guard !running, !savingKey else { return }
        let secret = keyInput.trimmingCharacters(in:.whitespacesAndNewlines)
        guard Self.validSecret(secret) else { status = "Plak één geldige API-sleutel."; return }
        keyInput = ""; savingKey = true; status = "Bewaren in macOS Sleutelhanger…"
        DispatchQueue.global(qos:.userInitiated).async { [weak self] in
            do {
                try JevKeychain.save(secret)
                DispatchQueue.main.async {
                    self?.savingKey = false; self?.hasKey = true; self?.editingKey = false
                    self?.sessionSecret = secret
                    self?.status = "Sleutel bewaard. Klik op Start proef."
                }
            } catch {
                let message = error.localizedDescription
                DispatchQueue.main.async { self?.savingKey = false; self?.status = message }
            }
        }
    }
    func start() {
        guard hasKey, !running, !savingKey else { return }
        running = true; stopRequested = false; status = "Sleutel ophalen…"
        if let secret = sessionSecret {
            activateRekordbox(secret:secret)
            return
        }
        DispatchQueue.global(qos:.userInitiated).async { [weak self] in
            do {
                let secret = try JevKeychain.load()
                DispatchQueue.main.async {
                    guard let self = self else { return }
                    if self.stopRequested { self.running = false; return }
                    self.sessionSecret = secret
                    self.activateRekordbox(secret:secret)
                }
            } catch {
                let message = error.localizedDescription
                DispatchQueue.main.async { self?.running = false; self?.status = message }
            }
        }
    }
    private func activateRekordbox(secret: String) {
        status = "Rekordbox openen…"
        let config = NSWorkspace.OpenConfiguration()
        config.activates = true
        NSWorkspace.shared.openApplication(at:URL(fileURLWithPath:"/Applications/rekordbox 6/rekordbox.app"),configuration:config) { [weak self] _, error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                if self.stopRequested { self.running = false; return }
                if let error = error { self.running = false; self.status = error.localizedDescription; return }
                self.launch(secret:secret)
            }
        }
    }
    private func launch(secret: String) {
        let candidates = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        guard let python = candidates.first(where:{FileManager.default.isExecutableFile(atPath:$0)}) else {
            running = false; status = "Python ontbreekt op deze Mac."; return
        }
        let task = Process()
        task.executableURL = URL(fileURLWithPath:python)
        task.arguments = [root.appendingPathComponent("scripts/jev_reactive.py").path,"--key-stdin","--dj-test"]
        task.currentDirectoryURL = root
        var environment = ProcessInfo.processInfo.environment
        environment.removeValue(forKey:"TYPESAFE_API_KEY")
        task.environment = environment
        let input = Pipe(), output = Pipe()
        task.standardInput = input; task.standardOutput = output; task.standardError = output
        do { try task.run() }
        catch { running = false; status = error.localizedDescription; return }
        process = task
        status = "DJ-sessie loopt · blijft verder mixen"
        // Credentials travel only through the anonymous stdin pipe, never argv or a file.
        DispatchQueue.global(qos:.userInitiated).async { [weak self] in
            do {
                try input.fileHandleForWriting.write(contentsOf:Data((secret+"\n").utf8))
                try input.fileHandleForWriting.close()
            } catch {
                try? input.fileHandleForWriting.close()
                if task.isRunning { task.interrupt() }
            }
            let data = output.fileHandleForReading.readDataToEndOfFile()
            task.waitUntilExit()
            let text = (String(data:data,encoding:.utf8) ?? "").replacingOccurrences(of:secret,with:"[verborgen]")
            let code = task.terminationStatus
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.running = false; self.process = nil
                self.status = self.stopRequested ? "Proef gestopt. Je kunt opnieuw starten." : Self.summary(text,code:code)
            }
        }
    }
    func stop() {
        guard running else { return }
        stopRequested = true
        status = "Stop gevraagd; er worden geen nieuwe keuzes gestart."
        if let process = process, process.isRunning { process.interrupt() }
    }
    static func summary(_ output: String, code: Int32) -> String {
        let records = output.split(separator:"\n").compactMap { line -> [String:Any]? in
            guard let data = String(line).data(using:.utf8) else { return nil }
            return (try? JSONSerialization.jsonObject(with:data)) as? [String:Any]
        }
        if let error = records.reversed().compactMap({$0["error"] as? String}).first { return error }
        if code == 130 { return "Proef gestopt. Je kunt opnieuw starten." }
        if code != 0 { return "Proef niet gestart of onderbroken (code \(code)). Je kunt opnieuw proberen." }
        if let execution = records.last?["execution"] as? [String:Any],
           let state = execution["status"] as? String, !["verified","wait"].contains(state) {
            return "Proef afgerond: \((execution["message"] as? String) ?? state)"
        }
        return "Proef afgerond. De antwoorden staan hieronder."
    }
}

struct JevControlView: View {
    @ObservedObject var controls: JevControls
    let onStart: () -> Void
    var body: some View {
        VStack(alignment:.leading,spacing:9) {
            if controls.editingKey {
                Text("Eenmalig verbinden").font(.system(size:12,weight:.semibold))
                HStack {
                    SecureField("TypeSafe API-sleutel",text:$controls.keyInput)
                        .textFieldStyle(.roundedBorder).onSubmit { controls.saveKey() }
                        .disabled(controls.savingKey || controls.running)
                    Button("Plak") { controls.keyInput = NSPasteboard.general.string(forType:.string) ?? "" }
                        .disabled(controls.savingKey || controls.running)
                }
                Text("Bewaar je sleutel veilig in macOS Sleutelhanger.")
                    .font(.system(size:10)).foregroundStyle(.secondary)
                HStack {
                    Button("Bewaar sleutel") { controls.saveKey() }
                        .disabled(controls.keyInput.isEmpty || controls.savingKey || controls.running)
                    if controls.hasKey { Button("Annuleer") { controls.keyInput = ""; controls.editingKey = false } }
                }
            }
            HStack {
                Button { onStart(); controls.start() } label: { Label("Start DJ",systemImage:"play.fill") }
                    .buttonStyle(.borderedProminent).tint(Color(red:0.2,green:0.65,blue:0.5))
                    .disabled(!controls.hasKey || controls.running || controls.savingKey || controls.editingKey)
                if controls.running { Button("Stop",role:.destructive) { controls.stop() } }
                Spacer()
                if controls.hasKey && !controls.editingKey {
                    Button { controls.editingKey = true } label: { Image(systemName:"key.fill") }
                        .buttonStyle(.plain).help("Sleutel wijzigen").disabled(controls.running)
                }
            }
            Text(controls.status).font(.system(size:11)).foregroundStyle(.secondary).textSelection(.enabled)
        }.padding(12).background(Color.white.opacity(0.045),in:RoundedRectangle(cornerRadius:12))
    }
}
