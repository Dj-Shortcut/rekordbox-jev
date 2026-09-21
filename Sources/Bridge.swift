import AppKit
import ApplicationServices
import ScreenCaptureKit
import Vision
import CoreMIDI
import Carbon
import Darwin

// Local control transport. No Jev calls, audio processing, or autonomous mixing yet.
let rekordboxID = "com.pioneerdj.rekordboxdj"
let allowedMusic = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Music/Music/26", isDirectory: true)
let socketDirectory = "/private/tmp/rekordbox-bridge-\(getuid())"
let socketPath = socketDirectory + "/control.sock"

struct BridgeError: Error, CustomStringConvertible {
    let description: String
    init(_ message: String) { description = message }
}
struct TextToken {
    let text: String
    let confidence: Float
    let rect: CGRect
    var json: [String: Any] {
        ["text": text, "confidence": confidence, "x": rect.minX, "y": rect.minY,
         "width": rect.width, "height": rect.height]
    }
}
struct Observation {
    let image: CGImage
    let tokens: [TextToken]
    let window: SCWindow
    let sampledAt: UInt64
    let elapsedMS: Double
    func text(in rect: CGRect) -> String {
        tokens.filter { rect.contains(CGPoint(x: $0.rect.midX, y: $0.rect.midY)) }
            .sorted { $0.rect.minX < $1.rect.minX }.map(\.text).joined(separator: " ")
    }
    var calibrated: Bool {
        image.width == 1272 && image.height == 768 &&
        text(in: CGRect(x: 35, y: 25, width: 250, height: 25)).uppercased().contains("PERFORMANCE") &&
        text(in: CGRect(x: 160, y: 25, width: 160, height: 25)).contains("2Deck Horizontal")
    }
    func playing(deck: Int) -> Bool? {
        guard calibrated else { return nil }
        let bitmap = NSBitmapImageRep(cgImage: image)
        let x = deck == 1 ? 448 : 703
        var green = 0
        for py in 343...357 { for px in (x-7)...(x+7) {
            guard let c = bitmap.colorAt(x:px, y:py)?.usingColorSpace(.deviceRGB) else { continue }
            if c.greenComponent > 0.28 && c.greenComponent > c.redComponent*1.5 && c.greenComponent > c.blueComponent*1.2 { green += 1 }
        }}
        return green >= 4
    }
    func fader(deck: Int) -> Double? {
        guard calibrated else { return nil }
        let bitmap = NSBitmapImageRep(cgImage: image)
        let x = deck == 1 ? 613 : 659
        var bestY = 322, best = 0.0
        for y in 320...359 {
            var score = 0.0
            for px in (x-8)...(x+8) {
                guard let c = bitmap.colorAt(x:px,y:y)?.usingColorSpace(.deviceRGB) else { continue }
                score += Double(min(c.redComponent, c.greenComponent, c.blueComponent))
            }
            if score > best { best = score; bestY = y }
        }
        guard best > 3 else { return nil }
        return min(1,max(0,Double(357-bestY)/35))
    }
    var json: [String: Any] {
        var data: [String: Any] = [
            "sampledAtMonotonicNS": sampledAt, "observationMS": elapsedMS,
            "window": ["id": window.windowID, "title": window.title ?? "", "width": image.width, "height": image.height],
            "layoutCalibrated": calibrated, "tokens": tokens.map(\.json),
            "playing": NSNull(), "playingNote": "A single OCR frame does not establish playback or beat phase."
        ]
        if calibrated {
            data["playingIndicators"] = ["deck1": playing(deck:1) as Any, "deck2": playing(deck:2) as Any]
            data["faders"] = ["deck1": fader(deck:1) as Any, "deck2": fader(deck:2) as Any]
            data["decks"] = [
                ["deck": 1, "title": text(in: CGRect(x: 45,y: 174,width: 430,height: 20)),
                 "metadata": text(in: CGRect(x: 45,y: 193,width: 435,height: 20)),
                 "displayedBPM": text(in: CGRect(x: 480,y: 300,width: 70,height: 23))],
                ["deck": 2, "title": text(in: CGRect(x: 731,y: 174,width: 430,height: 20)),
                 "metadata": text(in: CGRect(x: 731,y: 193,width: 435,height: 20)),
                 "displayedBPM": text(in: CGRect(x: 740,y: 300,width: 70,height: 23))]
            ]
            data["browserHeading"] = text(in: CGRect(x: 235,y: 429,width: 450,height: 24))
        }
        return data
    }
}

func app() throws -> NSRunningApplication {
    guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: rekordboxID).first else {
        throw BridgeError("Rekordbox is niet geopend.")
    }
    return app
}
func requireInputAccess() throws {
    guard AXIsProcessTrusted() else { throw BridgeError("Toegankelijkheid ontbreekt voor Rekordbox Bridge.") }
    let rb = try app()
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == rb.processIdentifier else {
        throw BridgeError("Rekordbox moet vooraan staan; er is niets verstuurd.")
    }
    let ax = AXUIElementCreateApplication(rb.processIdentifier)
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(ax, kAXFocusedWindowAttribute as CFString, &value) == .success,
          let value else { throw BridgeError("Het actieve Rekordbox-venster is niet leesbaar.") }
    let window = value as! AXUIElement
    var title: CFTypeRef?
    AXUIElementCopyAttributeValue(window, kAXTitleAttribute as CFString, &title)
    guard (title as? String)?.lowercased() == "rekordbox" else {
        throw BridgeError("Sluit eerst het dialoogvenster in Rekordbox.")
    }
}

func status() -> [String: Any] {
    let rb = NSRunningApplication.runningApplications(withBundleIdentifier: rekordboxID).first
    var sources: [String] = []
    for i in 0..<MIDIGetNumberOfSources() {
        var name: Unmanaged<CFString>?
        if MIDIObjectGetStringProperty(MIDIGetSource(i), kMIDIPropertyDisplayName, &name) == noErr,
           let name { sources.append(name.takeRetainedValue() as String) }
    }
    return ["accessibility": AXIsProcessTrusted(), "screenRecording": CGPreflightScreenCaptureAccess(),
            "rekordboxRunning": rb != nil,
            "rekordboxFrontmost": rb != nil && NSWorkspace.shared.frontmostApplication?.processIdentifier == rb?.processIdentifier,
            "allowedMusicFolder": allowedMusic.path, "midiSources": sources,
            "autonomousMixing": false, "protocolVersion": 1, "bridgePID": getpid()]
}

func observe() async throws -> Observation {
    guard CGPreflightScreenCaptureAccess() else {
        throw BridgeError("Schermopname ontbreekt voor Rekordbox Bridge.")
    }
    let start = DispatchTime.now().uptimeNanoseconds
    let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
    guard let window = content.windows.first(where: {
        $0.owningApplication?.bundleIdentifier == rekordboxID && $0.title?.lowercased() == "rekordbox"
    }) else { throw BridgeError("Het hoofdvenster van Rekordbox is niet zichtbaar.") }
    let filter = SCContentFilter(desktopIndependentWindow: window)
    let config = SCStreamConfiguration()
    let sameAspect = abs(window.frame.width/window.frame.height - 1272.0/768.0) < 0.005
    config.width = sameAspect ? 1272 : Int(window.frame.width)
    config.height = sameAspect ? 768 : Int(window.frame.height)
    config.showsCursor = false
    config.ignoreShadowsSingleWindow = true
    let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
    let sampledAt = DispatchTime.now().uptimeNanoseconds
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.recognitionLanguages = ["en-US", "nl-NL"]
    try VNImageRequestHandler(cgImage: image).perform([request])
    let tokens = (request.results ?? []).compactMap { observation -> TextToken? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let b = observation.boundingBox
        return TextToken(text: candidate.string, confidence: candidate.confidence,
            rect: CGRect(x: b.minX * Double(image.width), y: (1-b.maxY) * Double(image.height),
                         width: b.width * Double(image.width), height: b.height * Double(image.height)))
    }
    return Observation(image: image, tokens: tokens, window: window, sampledAt: sampledAt,
        elapsedMS: Double(DispatchTime.now().uptimeNanoseconds-start)/1_000_000)
}

// Reads the user's currently selected keyboard mapping without editing it.
final class MappingReader: NSObject, XMLParserDelegate {
    var rows: [[String: String]] = []
    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?,
                qualifiedName qName: String?, attributes: [String: String]) {
        if elementName == "MAPPING" { rows.append(attributes) }
    }
}
func mappings() throws -> [[String: String]] {
    let path = NSHomeDirectory() + "/Library/Application Support/Pioneer/rekordbox6/KeyMappings/rekordbox_0000000000000.mappings"
    let parser = XMLParser(data: try Data(contentsOf: URL(fileURLWithPath: path)))
    let reader = MappingReader()
    parser.delegate = reader
    guard parser.parse() else { throw BridgeError("Sneltoetsbestand is niet leesbaar.") }
    return reader.rows
}
// Only these non-destructive deck operations are exposed in step 1.
let commands: [String: String] = [
    "deck1.playPause": "3006", "deck2.playPause": "3106",
    "deck1.cue": "3007", "deck2.cue": "3107",
    "deck1.start": "3057", "deck2.start": "3157",
    "deck1.sync": "304b", "deck2.sync": "314b",
    "deck1.master": "304c", "deck2.master": "314c",
    "deck1.masterTempo": "304d", "deck2.masterTempo": "314d",
    "deck1.tempoUp": "305b", "deck1.tempoDown": "305e",
    "deck2.tempoUp": "315b", "deck2.tempoDown": "315e",
    "deck1.loopToggle": "30a5", "deck2.loopToggle": "31a5",
    "deck1.loopExit": "300c", "deck2.loopExit": "310c"
]
func keyCode(for text: String) throws -> CGKeyCode {
    let special: [String: CGKeyCode] = ["cursor left": 123,"cursor right":124,"cursor down":125,
                                       "cursor up":126,"spacebar":49,"return":36]
    if let code = special[text] { return code }
    // Resolve letters from the actual keyboard layout (including AZERTY).
    guard let source = TISCopyCurrentKeyboardLayoutInputSource()?.takeRetainedValue(),
          let raw = TISGetInputSourceProperty(source, kTISPropertyUnicodeKeyLayoutData) else {
        throw BridgeError("Toetsenbordindeling is niet beschikbaar.")
    }
    let data = unsafeBitCast(raw, to: CFData.self)
    let layout = UnsafeRawPointer(CFDataGetBytePtr(data)).assumingMemoryBound(to: UCKeyboardLayout.self)
    for code in 0..<128 {
        var dead: UInt32 = 0
        var length = 0
        var chars = [UniChar](repeating: 0, count: 8)
        let error = UCKeyTranslate(layout, UInt16(code), UInt16(kUCKeyActionDown), 0,
            UInt32(LMGetKbdType()), OptionBits(kUCKeyTranslateNoDeadKeysBit), &dead, 8, &length, &chars)
        if error == noErr, String(utf16CodeUnits: chars, count: length).lowercased() == text.lowercased() {
            return CGKeyCode(code)
        }
    }
    throw BridgeError("Geen fysieke toets gevonden voor \(text).")
}
func sendKey(_ description: String) async throws {
    try requireInputAccess()
    let parts = description.lowercased().components(separatedBy: " + ")
    guard let key = parts.last else { throw BridgeError("Lege sneltoets.") }
    var flags = CGEventFlags()
    for part in parts.dropLast() {
        switch part {
        case "command": flags.insert(.maskCommand)
        case "shift": flags.insert(.maskShift)
        case "ctrl": flags.insert(.maskControl)
        case "option": flags.insert(.maskAlternate)
        default: throw BridgeError("Onbekende toetsmodifier: \(part)")
        }
    }
    let code = try keyCode(for: key)
    let pid = try app().processIdentifier
    guard let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true),
          let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false) else {
        throw BridgeError("Toetsgebeurtenis kon niet worden gemaakt.")
    }
    down.flags = flags; up.flags = flags
    down.postToPid(pid)
    try await Task.sleep(nanoseconds: 20_000_000)
    up.postToPid(pid)
}

func pointer(_ point: CGPoint, observation: Observation, dragTo: CGPoint? = nil) async throws {
    try requireInputAccess()
    guard observation.calibrated else { throw BridgeError("Onbekende vensterindeling.") }
    func screen(_ p: CGPoint) -> CGPoint {
        CGPoint(x:observation.window.frame.minX + p.x/1272 * observation.window.frame.width,
                y:observation.window.frame.minY + p.y/768 * observation.window.frame.height)
    }
    func emit(_ type: CGEventType, _ location: CGPoint) {
        CGEvent(mouseEventSource:nil, mouseType:type, mouseCursorPosition:screen(location), mouseButton:.left)?.post(tap:.cghidEventTap)
    }
    emit(.mouseMoved,point)
    emit(.leftMouseDown,point)
    // Native GUI controls need a real press interval, unlike zero-duration synthetic clicks.
    try await Task.sleep(nanoseconds:30_000_000)
    if let target = dragTo {
        for step in 1...12 {
            emit(.leftMouseDragged,CGPoint(x:point.x+(target.x-point.x)*Double(step)/12,
                                          y:point.y+(target.y-point.y)*Double(step)/12))
            try await Task.sleep(nanoseconds:8_000_000)
        }
        emit(.leftMouseUp,target)
    } else { emit(.leftMouseUp,point) }
}

func checkedObservation() async throws -> Observation {
    try requireInputAccess()
    let observation = try await observe()
    guard observation.calibrated else { throw BridgeError("De huidige lay-out is nog niet gekalibreerd.") }
    guard Double(DispatchTime.now().uptimeNanoseconds-observation.sampledAt)/1_000_000 <= 750 else {
        throw BridgeError("Waarneming is te oud; probeer een nieuwe waarneming.")
    }
    return observation
}

func handle(_ request: [String: Any]) async throws -> [String: Any] {
    switch request["command"] as? String {
    case "status": return status()
    case "activate":
        try app().activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
        return ["activationRequested":true]
    case "quit":
        DispatchQueue.main.asyncAfter(deadline:.now()+0.2) { NSApplication.shared.terminate(nil) }
        return ["quitting":true]
    case "observe":
        let observation = try await observe()
        if request["saveImage"] as? Bool == true {
            let output = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("evidence/native-snapshot.png")
            try NSBitmapImageRep(cgImage:observation.image).representation(using:.png,properties:[:])?.write(to:output)
        }
        return observation.json
    case "browse":
        let before = try await checkedObservation()
        guard before.text(in:CGRect(x:235,y:429,width:450,height:24)) == "26" else {
            throw BridgeError("Selectie is alleen toegestaan binnen de geopende map 26.")
        }
        guard let direction = request["direction"] as? String, ["next","previous"].contains(direction) else {
            throw BridgeError("Verwacht next of previous.")
        }
        try await sendKey(direction == "next" ? "cursor down" : "cursor up")
        return ["dispatched":true,"verified":false,"after":try await observe().json]
    case "loadVisible":
        guard let deck = request["deck"] as? Int, [1,2].contains(deck),
              let filename = request["file"] as? String, URL(fileURLWithPath:filename).lastPathComponent == filename else {
            throw BridgeError("Verwacht deck en een bestandsnaam uit map 26.")
        }
        let file = allowedMusic.appendingPathComponent(filename).resolvingSymlinksInPath()
        guard file.deletingLastPathComponent() == allowedMusic.resolvingSymlinksInPath(),
              FileManager.default.fileExists(atPath:file.path) else { throw BridgeError("Track valt buiten map 26.") }
        let manifest = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("evidence/inventory.json")
        let inventory = try JSONSerialization.jsonObject(with:Data(contentsOf:manifest)) as? [String:Any]
        guard let tracks = inventory?["tracks"] as? [[String:Any]],
              let track = tracks.first(where:{$0["file"] as? String == filename}) else {
            throw BridgeError("Track is nog niet geïnventariseerd.")
        }
        let expected = track["title"] as? String ?? file.deletingPathExtension().lastPathComponent
        let before = try await checkedObservation()
        guard before.text(in:CGRect(x:235,y:429,width:450,height:24)) == "26", before.playing(deck:deck) == false else {
            throw BridgeError("Open map 26 en gebruik een stilstaand doeldeck.")
        }
        let matches = before.tokens.filter { $0.rect.minY > 460 && $0.rect.maxY < 739 && $0.confidence>0.8 &&
            $0.text.compare(expected,options:[.caseInsensitive,.diacriticInsensitive]) == .orderedSame }
        guard matches.count == 1, let row = matches.first else {
            throw BridgeError("De tracktitel is niet uniek en volledig zichtbaar in de lijst.")
        }
        try await pointer(CGPoint(x:row.rect.midX,y:row.rect.midY),observation:before)
        try await sendKey(deck == 1 ? "shift + cursor left" : "shift + cursor right")
        let after = try await observe()
        let titleRegion = deck == 1 ? CGRect(x:45,y:174,width:430,height:20) : CGRect(x:731,y:174,width:430,height:20)
        let title = after.text(in:titleRegion).trimmingCharacters(in:CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn:"|")))
        return ["dispatched":true,"verified":title.compare(expected,options:[.caseInsensitive,.diacriticInsensitive]) == .orderedSame,
                "loadedTitle":title,"expectedTitle":expected,"after":after.json]
    case "capabilities":
        let rows = try mappings()
        return ["actions": commands.sorted { $0.key < $1.key }.map { action, id in
            ["action": action, "commandID": id, "key": rows.first { $0["commandId"] == id }?["key"] ?? "",
             "nativeTransportVerified": false] as [String: Any]
        }, "pointerCommands": ["fader", "eq", "crossfader"],
           "loading":"loadVisible (only uniquely readable tracks in folder 26)",
           "unsupported": ["filter", "effects", "beat-phase readback"]]
    case "fader":
        guard let deck = request["deck"] as? Int, [1,2].contains(deck),
              let value = request["value"] as? Double, value.isFinite, (0...1).contains(value) else {
            throw BridgeError("Fader vereist deck 1 of 2 en value tussen 0 en 1.")
        }
        let before = try await checkedObservation()
        let x = deck == 1 ? 613.0 : 659.0
        let target = CGPoint(x:x,y:357-35*value)
        if let current = before.fader(deck:deck) {
            try await pointer(CGPoint(x:x,y:357-35*current),observation:before,dragTo:target)
        } else { throw BridgeError("De faderstand is niet betrouwbaar afgelezen.") }
        let after = try await observe()
        let measured = after.fader(deck:deck)
        return ["dispatched":true,"verified": measured.map{abs($0-value)<0.08} ?? false,
                "requested":value,"measured":measured as Any,"after":after.json]
    case "eq":
        guard let deck = request["deck"] as? Int, [1,2].contains(deck),
              let band = request["band"] as? String, let y = ["high":225.0,"mid":254.0,"low":283.0,"trim":195.0][band],
              let pixels = request["pixels"] as? Double, pixels.isFinite, abs(pixels)<=40 else {
            throw BridgeError("EQ vereist deck, band (high/mid/low/trim) en pixels (-40…40).")
        }
        let before = try await checkedObservation()
        let x = deck == 1 ? 613.0 : 659.0
        try await pointer(CGPoint(x:x,y:y),observation:before,dragTo:CGPoint(x:x,y:y+pixels))
        return ["dispatched":true,"verified":false,"after":try await observe().json]
    case "crossfader":
        guard let value = request["value"] as? Double, value.isFinite, (0...1).contains(value) else {
            throw BridgeError("Crossfader vereist value tussen 0 en 1.")
        }
        let before = try await checkedObservation()
        try await pointer(CGPoint(x:585+101*value,y:385),observation:before)
        return ["dispatched":true,"verified":false,"after":try await observe().json]
    case "action":
        guard let action = request["action"] as? String, let id = commands[action],
              let key = try mappings().first(where: { $0["commandId"] == id })?["key"] else {
            throw BridgeError("Onbekende of niet toegewezen actie.")
        }
        guard let expected = request["expectedTrack"] as? String, !expected.isEmpty else {
            throw BridgeError("expectedTrack is verplicht om het bedoelde deck te controleren.")
        }
        let before = try await observe()
        guard before.calibrated else { throw BridgeError("De huidige lay-out is nog niet gekalibreerd.") }
        let region = action.hasPrefix("deck1.") ? CGRect(x:45,y:174,width:430,height:20) : CGRect(x:731,y:174,width:430,height:20)
        let title = before.text(in: region).trimmingCharacters(in:CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn:"|")))
        guard title.compare(expected, options: [.caseInsensitive, .diacriticInsensitive]) == .orderedSame else {
            throw BridgeError("De geladen track wijkt af: \(title)")
        }
        let ageMS = Double(DispatchTime.now().uptimeNanoseconds-before.sampledAt)/1_000_000
        guard ageMS <= 750 else { throw BridgeError("Waarneming is te oud (\(Int(ageMS)) ms); niets verstuurd.") }
        let started = DispatchTime.now().uptimeNanoseconds
        try await sendKey(key)
        let after = try await observe()
        return ["action": action, "dispatched": true, "verified": false,
                "note": "Event sent; caller must verify the intended state change. Never retry a toggle blindly.",
                "elapsedMS": Double(DispatchTime.now().uptimeNanoseconds-started)/1_000_000,
                "after": after.json]
    default: throw BridgeError("Onbekend commando.")
    }
}

func runServer() {
    do {
        try FileManager.default.createDirectory(atPath: socketDirectory, withIntermediateDirectories: true,
                                               attributes: [.posixPermissions: 0o700])
        let lock = open(socketDirectory + "/server.lock", O_CREAT | O_RDWR, 0o600)
        guard lock >= 0, flock(lock, LOCK_EX | LOCK_NB) == 0 else { exit(0) }
        defer { close(lock) }
        let server = socket(AF_UNIX, SOCK_STREAM, 0)
        guard server >= 0 else { throw BridgeError("Socket kon niet worden gemaakt.") }
        defer { close(server); unlink(socketPath) }
        unlink(socketPath)
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        withUnsafeMutableBytes(of: &address.sun_path) { bytes in
            socketPath.utf8CString.withUnsafeBytes { bytes.copyBytes(from: $0) }
        }
        let bound = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                bind(server, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard bound == 0, listen(server, 4) == 0 else { throw BridgeError("Socket kon niet worden geopend.") }
        chmod(socketPath, 0o600)
        while true {
            let client = accept(server, nil, nil)
            if client < 0 { continue }
            var timeout = timeval(tv_sec: 5, tv_usec: 0)
            setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
            var noSignal: Int32 = 1
            setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout<Int32>.size))
            var bytes = [UInt8](repeating: 0, count: 16384)
            var data = Data()
            while data.count < 16384 {
                let n = read(client, &bytes, min(bytes.count, 16384-data.count))
                if n <= 0 { break }
                data.append(contentsOf: bytes.prefix(n))
                if data.contains(10) { break }
            }
            let semaphore = DispatchSemaphore(value: 0)
            Task { @MainActor in
                let result: [String: Any]
                do {
                    guard let request = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                        throw BridgeError("Verwacht een JSON-object.")
                    }
                    result = ["ok": true, "result": try await handle(request)]
                } catch { result = ["ok": false, "error": String(describing: error)] }
                var response = (try? JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])) ?? Data()
                response.append(10)
                response.withUnsafeBytes { buffer in
                    var offset = 0
                    while offset < buffer.count {
                        let n = write(client, buffer.baseAddress!.advanced(by: offset), buffer.count-offset)
                        if n <= 0 { break }; offset += n
                    }
                }
                close(client)
                semaphore.signal()
            }
            semaphore.wait()
        }
    } catch {
        fputs("\(error)\n", stderr)
        exit(1)
    }
}

if CommandLine.arguments.contains("--diagnose") {
    let data = try JSONSerialization.data(withJSONObject: status(), options: [.prettyPrinted,.sortedKeys])
    print(String(decoding: data, as: UTF8.self))
} else if CommandLine.arguments.contains("--capabilities") {
    print(String(decoding: try JSONSerialization.data(withJSONObject: ["mappings": mappings()], options: .prettyPrinted), as: UTF8.self))
} else {
    let application = NSApplication.shared
    application.setActivationPolicy(.accessory)
    DispatchQueue.global(qos: .userInteractive).async { runServer() }
    application.run()
}
