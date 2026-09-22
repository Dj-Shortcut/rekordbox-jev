import Foundation
import Darwin
import SwiftUI
import AppKit

/// Controls the existing signed host. The widget never reads the Keychain.
@MainActor
final class JevProbeControls: ObservableObject {
    @Published private(set) var running = false
    @Published private(set) var busy = false
    @Published private(set) var status = ""
    private var timer: Timer?
    private var checking = false
    private var generation = 0

    init() {
        Task { [weak self] in await self?.refresh() }
        timer = Timer.scheduledTimer(withTimeInterval:0.5, repeats:true) { [weak self] _ in
            Task { @MainActor [weak self] in await self?.refresh() }
        }
    }
    deinit { timer?.invalidate() }

    private func refresh() async {
        guard !checking, !busy else { return }
        checking = true
        let observedGeneration = generation
        defer { checking = false }
        do {
            let active = try await Task.detached(priority:.utility) { try JevProbeSocket.active() }.value
            guard generation == observedGeneration else { return }
            let previouslyRunning = running
            running = active
            if active { status = "" }
            else if previouslyRunning { status = "DJ Jev gestopt · muziek blijft spelen" }
        } catch {
            guard generation == observedGeneration else { return }
            // Unknown connectivity must not present Start while a set may run.
            status = running ? "Bridge niet bereikbaar · de set kan nog draaien" : "Bridge niet bereikbaar"
        }
    }

    func toggle() {
        guard !busy else { return }
        if running { stop() } else { start() }
    }
    func start() {
        guard !running, !busy else { return }
        generation += 1
        busy = true
        status = "Bestaande verbinding controleren…"
        Task { [weak self] in
            guard let self else { return }
            do {
                let needsStart = try await Task.detached(priority:.userInitiated) {
                    try JevProbeSocket.prepareStart()
                }.value
                if needsStart {
                    self.status = "Rekordbox naar voren brengen…"
                    let rekordboxPID = try await self.bringExistingRekordboxForward()
                    // Confirm focus after the asynchronous readiness check, just
                    // before starting. A successful activate() request alone is
                    // not evidence that Rekordbox became the foreground app.
                    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == rekordboxPID else {
                        throw JevProbeError("Rekordbox kwam niet vooraan; de set is niet gestart.")
                    }
                    try await Task.detached(priority:.userInitiated) {
                        try JevProbeSocket.startPrepared()
                    }.value
                }
                self.running = true
                self.status = ""
            } catch let error as JevProbeError {
                self.status = error.message
            } catch {
                self.status = "Start niet bevestigd; niet opnieuw gestart."
            }
            self.busy = false
            await self.refresh()
        }
    }
    private func bringExistingRekordboxForward() async throws -> pid_t {
        let applications = NSRunningApplication.runningApplications(withBundleIdentifier:"com.pioneerdj.rekordboxdj")
            .filter { !$0.isTerminated }
        guard applications.count == 1, let rekordbox = applications.first else {
            throw JevProbeError("Geen eenduidige draaiende Rekordbox gevonden; de set is niet gestart.")
        }
        let pid = rekordbox.processIdentifier
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == pid { return pid }
        // This runs only for an explicit Start. Polling/status updates and Stop
        // never steal focus back from another app the user chooses afterwards.
        NSApp.keyWindow?.resignKey()
        // On macOS 14+, ignoringOtherApps has no effect. If the widget owns
        // activation, explicitly yield it through AppKit's cooperative API.
        let activationRequested: Bool
        if NSApp.isActive {
            NSApp.yieldActivation(to:rekordbox)
            activationRequested = rekordbox.activate(from:NSRunningApplication.current, options:[.activateAllWindows])
        } else {
            activationRequested = rekordbox.activate(options:[.activateAllWindows])
        }
        guard activationRequested else {
            throw JevProbeError("Rekordbox kon niet naar voren worden gebracht; de set is niet gestart.")
        }
        let deadline = DispatchTime.now().uptimeNanoseconds + 2_000_000_000
        while DispatchTime.now().uptimeNanoseconds < deadline {
            if NSWorkspace.shared.frontmostApplication?.processIdentifier == pid { return pid }
            try await Task.sleep(nanoseconds:50_000_000)
        }
        throw JevProbeError("Rekordbox kwam niet vooraan; de set is niet gestart.")
    }
    func stop() {
        guard running, !busy else { return }
        generation += 1
        busy = true
        status = "Bediening stoppen…"
        Task { [weak self] in
            guard let self else { return }
            do {
                try await Task.detached(priority:.userInitiated) { try JevProbeSocket.stop() }.value
                self.status = "Stop gevraagd · muziek blijft spelen"
            } catch let error as JevProbeError {
                self.status = error.message
            } catch {
                self.status = "Stop nog niet bevestigd."
            }
            self.busy = false
            await self.refresh()
        }
    }
}

private struct JevProbeError: Error {
    let message: String
    let hostRejected: Bool
    init(_ message: String, hostRejected: Bool = false) {
        self.message = message
        self.hostRejected = hostRejected
    }
}

private enum JevProbeSocket {
    private static let maximumBytes = 4_000_000
    private static var now: Double {
        Double(DispatchTime.now().uptimeNanoseconds) / 1_000_000_000
    }

    static func active() throws -> Bool {
        let initial = try request("status", timeout:3)
        guard let active = initial["autonomousMixing"] as? Bool else {
            throw JevProbeError("De processtatus is onbekend.")
        }
        return active
    }

    static func prepareStart() throws -> Bool {
        let initial = try request("status", timeout:3)
        guard let version = initial["protocolVersion"] as? Int, version >= 3 else {
            throw JevProbeError("Deze Bridge ondersteunt de set niet; protocol 3 is vereist.")
        }
        guard let active = initial["autonomousMixing"] as? Bool else {
            throw JevProbeError("De processtatus is onbekend; geen tweede set gestart.")
        }
        // Reopening the widget attaches to the running set, without another start.
        if active { return false }
        // Noninteractive: cached key, or a clear error. Never djAuthorize.
        let ready = try request("djReady", timeout:10)
        guard ready["credentialAvailable"] as? Bool == true else {
            throw JevProbeError("De bestaande sleutel is niet beschikbaar; geen wachtwoord gevraagd.")
        }
        return true
    }

    static func startPrepared() throws {
        let started: [String:Any]
        do { started = try request("djStart", timeout:10) }
        catch let error as JevProbeError {
            if error.hostRejected { throw error }
            throw JevProbeError("Start niet bevestigd; de set kan al lopen. Niet opnieuw gestart.")
        }
        guard started["started"] as? Bool == true else {
            throw JevProbeError("Start niet bevestigd; de set kan al lopen. Niet opnieuw gestart.")
        }
    }

    static func stop() throws {
        let result = try request("djStop", timeout:3)
        guard result["stopRequested"] as? Bool == true else {
            throw JevProbeError("Stop niet bevestigd; de set kan nog draaien.")
        }
    }

    private static func wait(_ fd: Int32, events: Int16, deadline: Double) throws {
        while true {
            let remaining = deadline - now
            guard remaining > 0 else { throw JevProbeError("De Bridge antwoordt niet tijdig.") }
            var descriptor = pollfd(fd: fd, events: events, revents: 0)
            let milliseconds = Int32(max(1, min(remaining * 1000, Double(Int32.max))))
            let count = Darwin.poll(&descriptor, 1, milliseconds)
            if count < 0 && errno == EINTR { continue }
            guard count >= 0 else { throw JevProbeError("De lokale Bridge-verbinding is onderbroken.") }
            if count == 0 { continue }
            if descriptor.revents & (events | Int16(POLLHUP)) != 0 { return }
            throw JevProbeError("De lokale Bridge-verbinding is onderbroken.")
        }
    }

    private static func request(_ command: String, timeout: Double) throws -> [String: Any] {
        guard ["status", "djReady", "djStart", "djStop"].contains(command) else {
            throw JevProbeError("Onbekende setaanvraag geweigerd.")
        }
        let deadline = now + timeout
        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw JevProbeError("De lokale Bridge-verbinding kon niet worden geopend.") }
        defer { Darwin.close(fd) }
        var noSignal: Int32 = 1
        guard setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size)) == 0,
              fcntl(fd, F_SETFL, O_NONBLOCK) == 0 else {
            throw JevProbeError("De lokale Bridge-verbinding kon niet worden ingesteld.")
        }
        let path = "/private/tmp/rekordbox-bridge-\(getuid())/control.sock"
        let bytes = Array(path.utf8) + [UInt8(0)]
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        guard bytes.count <= MemoryLayout.size(ofValue: address.sun_path) else {
            throw JevProbeError("Het lokale Bridge-adres is ongeldig.")
        }
        withUnsafeMutableBytes(of: &address.sun_path) { destination in
            for (index, byte) in bytes.enumerated() { destination[index] = byte }
        }
        let connected = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        if connected != 0 {
            guard errno == EINPROGRESS || errno == EAGAIN else {
                throw JevProbeError("Rekordbox Bridge is niet bereikbaar; er is geen app gestart.")
            }
            try wait(fd, events: Int16(POLLOUT), deadline: deadline)
            var socketError: Int32 = 0
            var size = socklen_t(MemoryLayout<Int32>.size)
            guard getsockopt(fd, SOL_SOCKET, SO_ERROR, &socketError, &size) == 0, socketError == 0 else {
                throw JevProbeError("Rekordbox Bridge is niet bereikbaar; er is geen app gestart.")
            }
        }
        var data = try JSONSerialization.data(withJSONObject: ["command": command, "clientPID": getpid()])
        data.append(10)
        var sent = 0
        while sent < data.count {
            try wait(fd, events: Int16(POLLOUT), deadline: deadline)
            let count = data.withUnsafeBytes { buffer in
                Darwin.send(fd, buffer.baseAddress!.advanced(by: sent), data.count - sent, 0)
            }
            if count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) { continue }
            guard count > 0 else { throw JevProbeError("De Bridge-aanvraag kon niet worden afgeleverd.") }
            sent += count
        }
        var response = Data()
        var buffer = [UInt8](repeating: 0, count: 16_384)
        while response.firstIndex(of: 10) == nil {
            try wait(fd, events: Int16(POLLIN), deadline: deadline)
            let count = buffer.withUnsafeMutableBytes { Darwin.recv(fd, $0.baseAddress, $0.count, 0) }
            if count < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) { continue }
            guard count > 0 else { throw JevProbeError("De Bridge sloot de verbinding zonder volledig antwoord.") }
            guard response.count + count <= maximumBytes else { throw JevProbeError("Het Bridge-antwoord is te groot.") }
            response.append(contentsOf: buffer.prefix(count))
        }
        guard let newline = response.firstIndex(of: 10),
              let envelope = (try? JSONSerialization.jsonObject(with: response.prefix(upTo: newline))) as? [String: Any] else {
            throw JevProbeError("De Bridge gaf geen geldig antwoord.")
        }
        guard envelope["ok"] as? Bool == true else {
            // Fixed summaries avoid displaying unknown server text or secret material.
            let hostMessage = (envelope["error"] as? String ?? "").lowercased()
            let summary: String
            if hostMessage.contains("sleutel") || hostMessage.contains("keychain") {
                summary = "De Bridge meldt dat de bestaande sleutel nog niet beschikbaar is; geen nieuwe sleutel gevraagd."
            } else if hostMessage.contains("al een dj-sessie") {
                summary = "De Bridge meldt dat er al een DJ-sessie loopt; geen tweede gestart."
            } else {
                summary = "De Bridge heeft de aanvraag \(command) geweigerd; de handeling is niet bevestigd."
            }
            throw JevProbeError(summary, hostRejected: true)
        }
        guard let result = envelope["result"] as? [String: Any] else {
            throw JevProbeError("Het Bridge-antwoord bevat geen leesbaar resultaat.")
        }
        return result
    }
}
