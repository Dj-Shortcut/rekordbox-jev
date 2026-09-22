import AppKit
import Security
import LocalAuthentication

// Every mutable credential field is protected by credentialLock. The blocking
// Keychain API runs exclusively on credentialQueue, never in the request task.
private final class DJCredentialAccess: @unchecked Sendable {
    private var secret: String?
    private let credentialQueue = DispatchQueue(label:"local.rekordbox.bridge.credentials", qos:.userInitiated)
    private let credentialLock = NSLock()
    private final class AuthorizationAttempt: @unchecked Sendable {
        // Accessed only while the owning DJCredentialAccess holds credentialLock.
        var continuation: CheckedContinuation<Void,Error>?
        let deadline = DispatchTime.now()+8
        init(_ continuation: CheckedContinuation<Void,Error>) { self.continuation = continuation }
    }
    private var authorizationAttempt: AuthorizationAttempt?

    func authorize(interactive: Bool) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void,Error>) in
            credentialLock.lock()
            if secret != nil {
                credentialLock.unlock()
                continuation.resume(returning:())
                return
            }
            guard authorizationAttempt == nil else {
                credentialLock.unlock()
                continuation.resume(throwing:BridgeError("De bestaande sleutelhangercontrole loopt nog; geen nieuwe controle gestart."))
                return
            }
            let attempt = AuthorizationAttempt(continuation)
            authorizationAttempt = attempt
            credentialLock.unlock()
            // SecItemCopyMatching is synchronous and cannot be cancelled by a Swift
            // task timeout. Keep it off the main thread, with at most one read in flight.
            credentialQueue.async {
                let result = Result { try Self.readCredential(interactive:interactive) }
                self.finishAuthorization(attempt, result:result)
            }
            DispatchQueue.global(qos:.userInitiated).asyncAfter(deadline:attempt.deadline) {
                self.expireAuthorization(attempt)
            }
        }
    }
    private static var authorizationTimeout: BridgeError {
        BridgeError("Sleutelhanger reageert niet tijdig. Geen nieuwe DJ-sessie gestart; de controle loopt nog op de achtergrond.")
    }
    private func expireAuthorization(_ attempt: AuthorizationAttempt) {
        credentialLock.lock()
        guard authorizationAttempt === attempt else { credentialLock.unlock(); return }
        let continuation = attempt.continuation
        attempt.continuation = nil
        // Retain the in-flight attempt: a timeout must not enqueue another Keychain read.
        credentialLock.unlock()
        continuation?.resume(throwing:Self.authorizationTimeout)
    }
    private func finishAuthorization(_ attempt: AuthorizationAttempt, result: Result<String,Error>) {
        credentialLock.lock()
        guard authorizationAttempt === attempt else { credentialLock.unlock(); return }
        if case let .success(key) = result { secret = key }
        let continuation = attempt.continuation
        attempt.continuation = nil
        authorizationAttempt = nil
        let expired = DispatchTime.now().uptimeNanoseconds >= attempt.deadline.uptimeNanoseconds
        credentialLock.unlock()
        // A late success may prepare a future explicit start. It never resumes a
        // timed-out start successfully, even if the timeout callback was delayed.
        if expired { continuation?.resume(throwing:Self.authorizationTimeout) }
        else { continuation?.resume(with:result.map { _ in () }) }
    }
    private static func readCredential(interactive: Bool) throws -> String {
        // The existing item is in the legacy login keychain. Its UI setting is
        // process-wide, so every Bridge credential read stays on credentialQueue.
        // Denying interaction does not grant access or modify the item's ACL.
        var previousInteraction: DarwinBoolean = false
        var restoreInteraction = false
        if !interactive {
            let previousStatus = SecKeychainGetUserInteractionAllowed(&previousInteraction)
            guard previousStatus == errSecSuccess else {
                throw BridgeError("Sleutelhangercontrole niet gestart (macOS \(previousStatus)).")
            }
            let quietStatus = SecKeychainSetUserInteractionAllowed(false)
            guard quietStatus == errSecSuccess else {
                throw BridgeError("Sleutelhanger kon niet zonder dialoog worden gecontroleerd (macOS \(quietStatus)).")
            }
            restoreInteraction = true
        }
        defer {
            if restoreInteraction { _ = SecKeychainSetUserInteractionAllowed(previousInteraction.boolValue) }
        }
        var query: [String:Any] = [
            kSecClass as String:kSecClassGenericPassword,
            kSecAttrService as String:"local.rekordbox.jev-widget.typesafe",
            kSecAttrAccount as String:"TypeSafe API",
            kSecReturnData as String:true,
            kSecMatchLimit as String:kSecMatchLimitOne
        ]
        if !interactive {
            let context = LAContext(); context.interactionNotAllowed = true
            query[kSecUseAuthenticationContext as String] = context
            query[kSecUseAuthenticationUI as String] = kSecUseAuthenticationUIFail
        }
        var value: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary,&value)
        guard status == errSecSuccess, let data = value as? Data,
              let key = String(data:data,encoding:.utf8), !key.isEmpty, key.utf8.count <= 4096,
              !key.unicodeScalars.contains(where:{CharacterSet.controlCharacters.contains($0)}) else {
            throw BridgeError("Bestaande TypeSafe-sleutel nog niet toegankelijk voor de vaste Bridge (macOS \(status)). Er is geen nieuwe sleutel gevraagd.")
        }
        return key
    }
    func cachedSecret() -> String? {
        credentialLock.lock()
        defer { credentialLock.unlock() }
        return secret
    }
}

// The stable signed Bridge owns the credential and child process. The widget only reads events.
final class DJSessionHost {
    static let shared = DJSessionHost()
    private let credentials = DJCredentialAccess()
    private var process: Process?
    var running: Bool { process?.isRunning == true }

    func authorize(interactive: Bool) async throws {
        try await credentials.authorize(interactive:interactive)
    }
    func start() async throws {
        guard !running else { throw BridgeError("Er draait al een DJ-sessie.") }
        try await authorize(interactive:false)
        try Task.checkCancellation()
        guard !running else { throw BridgeError("Er draait al een DJ-sessie.") }
        guard let secret = credentials.cachedSecret() else { throw BridgeError("Geen sleutel beschikbaar.") }
        let root = Bundle.main.bundleURL.deletingLastPathComponent()
        let task = Process()
        let python = ["/opt/homebrew/bin/python3","/usr/local/bin/python3","/usr/bin/python3"].first {
            FileManager.default.isExecutableFile(atPath:$0)
        }!
        task.executableURL = URL(fileURLWithPath:python)
        task.arguments = [root.appendingPathComponent("scripts/jev_reactive.py").path,"--key-stdin","--dj-test"]
        task.currentDirectoryURL = root
        var environment = ProcessInfo.processInfo.environment
        environment.removeValue(forKey:"TYPESAFE_API_KEY")
        task.environment = environment
        let input = Pipe(), output = Pipe()
        task.standardInput = input; task.standardOutput = output; task.standardError = output
        try task.run(); process = task
        // No credential in argv, environment, output, or disk files.
        try input.fileHandleForWriting.write(contentsOf:Data((secret+"\n").utf8))
        try input.fileHandleForWriting.close()
        DispatchQueue.global(qos:.utility).async {
            var tail = Data()
            while true {
                let chunk = output.fileHandleForReading.availableData
                if chunk.isEmpty { break }
                tail.append(chunk)
                if tail.count > 131072 { tail = Data(tail.suffix(65536)) }
            }
            task.waitUntilExit()
            let text = (String(data:tail,encoding:.utf8) ?? "").replacingOccurrences(of:secret,with:"[verborgen]")
            let result:[String:Any] = ["exitCode":task.terminationStatus,"output":text,"finishedAt":Date().timeIntervalSince1970]
            let path = root.appendingPathComponent("evidence/native-session-result.json")
            if let data = try? JSONSerialization.data(withJSONObject:result,options:.prettyPrinted) {
                try? data.write(to:path,options:.atomic)
            }
        }
    }
    func stop() { if running { process?.interrupt() } }
}
