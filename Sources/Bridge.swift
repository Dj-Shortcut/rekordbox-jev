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
enum NativeProcessRole {
    case host, worker, demoControl, demoObserver
    init(arguments: [String]) throws {
        let choices: [(String,NativeProcessRole)] = [("--control-worker",.worker),
            ("--demo-control",.demoControl),("--demo-observer",.demoObserver)]
        let selected = choices.filter { arguments.contains($0.0) }
        guard selected.count <= 1 else { throw BridgeError("Kies precies één native procesrol.") }
        self = selected.first?.1 ?? .host
    }
    var socketFile: String {
        switch self {
        case .host: return "control.sock"
        case .worker: return "control-worker.sock"
        case .demoControl: return "demo-control.sock"
        case .demoObserver: return "demo-observer.sock"
        }
    }
    var lockFile: String { self == .host ? "server.lock" : socketFile.replacingOccurrences(of:".sock",with:".lock") }
    var demoName: String? {
        switch self { case .demoControl: return "control"; case .demoObserver: return "observer"; default: return nil }
    }
    func requireCommand(_ command: String) throws {
        if self != .host && command.hasPrefix("dj") {
            throw BridgeError("Dit fysieke proces heeft geen sleuteltoegang of DJ-sessiebeheer.")
        }
        if self == .demoObserver && !["status","observe","quit"].contains(command) {
            throw BridgeError("Demo-observer is alleen-lezen; alleen status, observe en quit zijn toegestaan.")
        }
    }
}
let nativeProcessRole: NativeProcessRole = {
    do { return try NativeProcessRole(arguments:CommandLine.arguments) }
    catch { fputs("\(error)\n",stderr); exit(2) }
}()
let isControlWorker = nativeProcessRole != .host
let socketPath = socketDirectory+"/"+nativeProcessRole.socketFile

struct BridgeError: Error, CustomStringConvertible {
    let description: String
    init(_ message: String) { description = message }
}
// Only construct this before the first input event of a command. A failure after
// even one relative gesture must never advertise that the command can be retried.
struct PreDispatchRejection: Error, CustomStringConvertible {
    let code: String
    let description: String
    let retryable: Bool
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
    let captureMS: Double
    let ocrMS: Double
    let identityReused: Bool
    let titleOCRReused: Bool
    let bpmOCRReused: Bool
    var validationAttempts = 1
    var validationMS = 0.0
    func text(in rect: CGRect) -> String {
        tokens.filter { rect.contains(CGPoint(x: $0.rect.midX, y: $0.rect.midY)) }
            .sorted { $0.rect.minX < $1.rect.minX }.map(\.text).joined(separator: " ")
    }
    var calibrated: Bool {
        image.width == 1272 && image.height == 768 &&
        recognizedLayoutHeader(tokens)
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
    var mixerJSON: [String: Any] {
        guard calibrated else { return [:] }
        let bitmap = NSBitmapImageRep(cgImage:image)
        let vision = MixerVision(bitmap:bitmap)
        func rgb(_ x: Int, _ y: Int) -> NSColor? {
            bitmap.colorAt(x:x,y:y)?.usingColorSpace(.deviceRGB)
        }
        func blue(_ x: Int, _ y: Int) -> Bool {
            guard let c = rgb(x,y) else { return false }
            return c.blueComponent > 0.3 && c.blueComponent > c.redComponent*1.7 && c.greenComponent > 0.2
        }
        var assignments: [String:String] = [:]
        var sync: [String:Bool] = [:], master: [String:Bool] = [:]
        // Keep the calibrated ReadFrame thresholds, but use this observation's
        // own image instead of reading a separately saved screenshot from disk.
        for deck in 1...2 {
            let left = blue(deck == 1 ? 531 : 553,379)
            let right = blue(deck == 1 ? 709 : 730,379)
            assignments[String(deck)] = left && !right ? "left" : right && !left ? "right" : !left && !right ? "unassigned" : "unknown"
            let origin = deck == 1 ? 535 : 1220
            var blueCount = 0, orangeCount = 0, whiteCount = 0
            for x in origin...min(origin+40,1271) {
                for y in 177...195 { if blue(x,y) { blueCount += 1 } }
                if x <= origin+25 {
                    for y in 178...194 {
                        if let c = rgb(x,y), min(c.redComponent,c.greenComponent,c.blueComponent) > 0.70 { whiteCount += 1 }
                    }
                }
                for y in 198...209 {
                    if let c = rgb(x,y), c.redComponent > 0.4 && c.greenComponent > 0.2 && c.redComponent > c.blueComponent*2 { orangeCount += 1 }
                }
            }
            sync[String(deck)] = blueCount > 8 || whiteCount > 8
            master[String(deck)] = orangeCount > 6
        }
        var result: [String:Any] = [
            "source":"Rekordbox window pixels, same captured frame as OCR",
            "crossfader_position":vision.crossfader as Any? ?? NSNull(),
            "deck_assignments":assignments, "beat_sync_lit":sync, "master_lit":master,
            "note":"Position and assignment are visual measurements; crossfader curve and actual audible output are not measured."
        ]
        result.merge(vision.json) { _,new in new }
        return result
    }
    var json: [String: Any] {
        var data: [String: Any] = [
            "sampledAtMonotonicNS": sampledAt, "observationMS": elapsedMS,
            "timing":["captureMS":captureMS,"ocrMS":ocrMS,"identityReused":identityReused,"titleOCRReused":titleOCRReused,
                      "bpmOCRReused":bpmOCRReused,
                      "validationAttempts":validationAttempts,"validationMS":validationMS],
            "window": ["id": window.windowID, "title": window.title ?? "", "width": image.width, "height": image.height],
            "layoutCalibrated": calibrated, "tokens": tokens.map(\.json),
            "playing": NSNull(), "playingNote": "A single OCR frame does not establish playback or beat phase."
        ]
        if calibrated {
            data["mixer"] = mixerJSON
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
func requireInputAccess(recoverablePreDispatch: Bool = false) throws {
    func blocked(_ code: String, _ message: String, retryable: Bool = true) -> Error {
        inputAccessFailure(code:code,message:message,retryable:retryable,explicitNoInput:recoverablePreDispatch)
    }
    guard AXIsProcessTrusted() else {
        throw blocked("accessibility_missing","Toegankelijkheid ontbreekt voor Rekordbox Bridge.",retryable:false)
    }
    guard let rb = NSRunningApplication.runningApplications(withBundleIdentifier:rekordboxID).first else {
        throw blocked("rekordbox_not_running","Rekordbox is niet geopend.")
    }
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == rb.processIdentifier else {
        throw blocked("rekordbox_not_frontmost","Rekordbox moet vooraan staan; geen volgende bediening verstuurd.")
    }
    let ax = AXUIElementCreateApplication(rb.processIdentifier)
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(ax, kAXFocusedWindowAttribute as CFString, &value) == .success,
          let value else { throw blocked("focused_window_unreadable","Het actieve Rekordbox-venster is niet leesbaar.") }
    let window = value as! AXUIElement
    var title: CFTypeRef?
    AXUIElementCopyAttributeValue(window, kAXTitleAttribute as CFString, &title)
    guard (title as? String)?.lowercased() == "rekordbox" else {
        throw blocked("rekordbox_dialog_open","Sluit eerst het dialoogvenster in Rekordbox.")
    }
}

func requireLiveControlRequest(_ request: [String:Any]) throws {
    if let supplied = request["notAfterMonotonicNS"] {
        guard let deadline = supplied as? UInt64, deadline > 0,
              DispatchTime.now().uptimeNanoseconds <= deadline else {
            throw BridgeError("Lokale deadline verstreken of ongeldig; geen verdere bediening.")
        }
    }
    guard let supplied = request["clientPID"] else { return }
    guard let pid = supplied as? Int32, pid > 1 else { throw BridgeError("Ongeldige aanvrager; geen bediening.") }
    guard !FileManager.default.fileExists(atPath:socketDirectory+"/stop-\(pid)") else {
        throw BridgeError("Stop gevraagd; geen verdere bediening uitgevoerd.")
    }
    guard kill(pid,0) == 0 || errno == EPERM else {
        throw BridgeError("Aanvrager gestopt; geen verdere bediening uitgevoerd.")
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
            "autonomousMixing": isControlWorker ? false : DJSessionHost.shared.running,
            "controlWorker":isControlWorker,"demoRole":nativeProcessRole.demoName as Any? ?? NSNull(),
            "readOnly":nativeProcessRole == .demoObserver, "protocolVersion": 3, "bridgePID": getpid(),
            "contextualTransport": ["version":2,"emptyDeckLoad":true,"replaceStoppedLoad":true,"replaceStopped":true,"desiredPlayback":true,"folder26":true],
            "nativeMixGuards":["version":1,"freshAlignmentBeforeInput":true,"typedPreDispatchRejections":true,
                               "expectedTracksAndDeadline":true,"mixStep":true,"mixGesture":true,"closeStoppedDeck":true,"openSilentDeck":true], "inlineMixerObservation":true]
}

func observe(mixerOnly: Bool = false, reusingGuardIdentity reference: Observation? = nil) async throws -> Observation {
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
    if mixerOnly, let reference, reference.calibrated,
       guardIdentityUnchanged(reference.image,image) {
        // Tokens are reused only where new pixels are exactly identical to the
        // last OCR-confirmed header/title. Never expose cached BPM/browser facts
        // as a fresh observation. The guard's mixer/playback pixels are all new.
        let identityTokens = reference.tokens.filter { token in
            guardIdentityRegions.contains { $0.contains(CGPoint(x:token.rect.midX,y:token.rect.midY)) }
        }
        return Observation(image:image,tokens:identityTokens,window:window,sampledAt:sampledAt,
                           elapsedMS:Double(DispatchTime.now().uptimeNanoseconds-start)/1e6,
                           captureMS:Double(sampledAt-start)/1e6,ocrMS:0,identityReused:true,titleOCRReused:true,bpmOCRReused:false)
    }
    let tokens = try recognizeObservationTokens(image,mixerOnly:mixerOnly)
    return Observation(image: image, tokens: tokens, window: window, sampledAt: sampledAt,
        elapsedMS: Double(DispatchTime.now().uptimeNanoseconds-start)/1_000_000,
        captureMS:Double(sampledAt-start)/1e6,
        ocrMS:Double(DispatchTime.now().uptimeNanoseconds-sampledAt)/1e6,identityReused:false,
        titleOCRReused:observationTitleCache.reused,bpmOCRReused:observationBPMCache.reused)
}

func observationTextBounds(_ box: CGRect, crop: CGRect) -> CGRect {
    CGRect(x:crop.minX+box.minX*crop.width, y:crop.minY+(1-box.maxY)*crop.height,
           width:box.width*crop.width, height:box.height*crop.height)
}

let observationTitleCrops = [CGRect(x:50,y:174,width:425,height:20),CGRect(x:731,y:174,width:430,height:20)]
let observationTitleCache = ObservationTextCache(regions:observationTitleCrops)
let observationHeaderCache = ValidatedHeaderCache()
// Measured large white BPM glyphs only; exclude the lower pitch/synced-BPM row.
let observationBPMCrops = [CGRect(x:484,y:298,width:57,height:20),CGRect(x:743,y:298,width:60,height:20)]
let observationBPMCache = ObservationTextCache(regions:observationBPMCrops)
let observationMetadataCrops = [CGRect(x:45,y:193,width:435,height:20),CGRect(x:731,y:193,width:435,height:20)]
let observationBrowserCrop = CGRect(x:0,y:400,width:1272,height:355)

func recognizeTextRegion(_ image: CGImage, _ area: CGRect) throws -> [TextToken] {
    guard let input = image.cropping(to:area) else { throw BridgeError("OCR-uitsnede is ongeldig.") }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.recognitionLanguages = ["en-US", "nl-NL"]
    try VNImageRequestHandler(cgImage:input,orientation:.up).perform([request])
    return (request.results ?? []).compactMap { observation -> TextToken? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        return TextToken(text:candidate.string,confidence:candidate.confidence,
                         rect:observationTextBounds(observation.boundingBox,crop:area))
    }
}

func recognizeObservationTokens(_ image: CGImage, mixerOnly: Bool) throws -> [TextToken] {
    let knownSize = image.width == 1272 && image.height == 768
    func recognize(_ area: CGRect) throws -> [TextToken] {
        try recognizeTextRegion(image,area)
    }
    // A broad waveform-heavy fast crop caused Vision to recognize upright UI
    // text upside down (including clocks). Fast mode only recognizes text strips;
    // it never asks OCR to infer text direction from the waveform illustration.
    // Full reads add only the library area. Header/deck fields below are already
    // recognized separately, so repeating whole-window OCR adds no evidence.
    var tokens = mixerOnly && knownSize ? [] : try recognize(knownSize ? observationBrowserCrop :
        CGRect(x:0,y:0,width:image.width,height:image.height))
    if knownSize {
        tokens.removeAll { observationHeaderRegion.contains(CGPoint(x:$0.rect.midX,y:$0.rect.midY)) }
        if let header = observationHeaderCache.tokens(for:image) { tokens += header }
        else {
            tokens += observationHeaderCache.resolve(try recognize(observationHeaderRegion),image:image)
        }
        // Use the same isolated metadata inputs in full/fast reads. Keep clocks
        // uncached: both deck time values must come from each captured frame.
        tokens.removeAll { token in
            observationMetadataCrops.contains { $0.contains(CGPoint(x:token.rect.midX,y:token.rect.midY)) }
        }
        for area in observationMetadataCrops { tokens += try recognize(area) }
        // Recognize each title independently with identical input in normal and
        // fast observations. Album-art edges, scrolling waveforms and library
        // OCR must not contribute spurious title characters such as "(".
        let titleFields = [CGRect(x:45,y:174,width:430,height:20),CGRect(x:731,y:174,width:430,height:20)]
        tokens.removeAll { token in
            titleFields.contains { $0.contains(CGPoint(x:token.rect.midX,y:token.rect.midY)) }
        }
        if let titles = observationTitleCache.tokens(for:image) { tokens += titles }
        else {
            var titles: [TextToken] = []
            for area in observationTitleCrops { titles += try recognize(area) }
            observationTitleCache.store(titles,image:image)
            tokens += titles
        }
        let bpmFields = [CGRect(x:480,y:300,width:70,height:23),CGRect(x:740,y:300,width:70,height:23)]
        tokens.removeAll { token in
            bpmFields.contains { $0.contains(CGPoint(x:token.rect.midX,y:token.rect.midY)) }
        }
        if let bpms = observationBPMCache.tokens(for:image) { tokens += bpms }
        else {
            var bpms: [TextToken] = []
            for area in observationBPMCrops { bpms += try recognize(area) }
            observationBPMCache.store(bpms,image:image)
            tokens += bpms
        }
    }
    return tokens
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
                                       "cursor up":126,"spacebar":49,"return":36,"escape":53]
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
struct PreparedKeyEvent {
    let pid: pid_t
    let down: CGEvent
    let up: CGEvent
}

func prepareKeyEvent(_ description: String) throws -> PreparedKeyEvent {
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
    return PreparedKeyEvent(pid:pid,down:down,up:up)
}

@discardableResult
func sendPreparedKey(_ event: PreparedKeyEvent, preDispatch: (() throws -> Void)? = nil) async throws -> UInt64 {
    try preDispatch?()
    markNativeInputSent()
    let sentAt = DispatchTime.now().uptimeNanoseconds
    event.down.postToPid(event.pid)
    defer { event.up.postToPid(event.pid) }
    try await Task.sleep(nanoseconds: 20_000_000)
    return sentAt
}

func sendKey(_ description: String, preDispatch: (() throws -> Void)? = nil) async throws {
    try requireInputAccess()
    let event = try prepareKeyEvent(description)
    try await sendPreparedKey(event,preDispatch:preDispatch)
}

func pointer(_ point: CGPoint, observation: Observation, dragTo: CGPoint? = nil, clickCount: Int = 1,
             preDispatch: (() throws -> Void)? = nil) async throws {
    try requireInputAccess()
    guard observation.calibrated else { throw BridgeError("Onbekende vensterindeling.") }
    func screen(_ p: CGPoint) -> CGPoint {
        CGPoint(x:observation.window.frame.minX + p.x/1272 * observation.window.frame.width,
                y:observation.window.frame.minY + p.y/768 * observation.window.frame.height)
    }
    func emit(_ type: CGEventType, _ location: CGPoint, count: Int = 1) {
        let event = CGEvent(mouseEventSource:nil, mouseType:type, mouseCursorPosition:screen(location), mouseButton:.left)
        event?.setIntegerValueField(.mouseEventClickState,value:Int64(count))
        event?.post(tap:.cghidEventTap)
    }
    try preDispatch?()
    markNativeInputSent()
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
    } else {
        emit(.leftMouseUp,point)
        if clickCount == 2 {
            try await Task.sleep(nanoseconds:50_000_000)
            emit(.leftMouseDown,point,count:2)
            try await Task.sleep(nanoseconds:30_000_000)
            emit(.leftMouseUp,point,count:2)
        }
    }
}

func checkedObservation(recoverablePreDispatch: Bool = false, mixerOnly: Bool = false,
                        reusingGuardIdentity reference: Observation? = nil) async throws -> Observation {
    try requireInputAccess(recoverablePreDispatch:recoverablePreDispatch)
    let validationStarted = DispatchTime.now().uptimeNanoseconds
    // Vision's first recognition can be slower than its warm calls. Recapture
    // without input at most twice; never relabel an old frame as fresh or relax
    // the 750 ms deadline used by red-marker/fader dispatch guards.
    for attempt in 1...3 {
        try requireInputAccess(recoverablePreDispatch:recoverablePreDispatch)
        var observation = try await observe(mixerOnly:mixerOnly,reusingGuardIdentity:reference)
        guard observation.calibrated else { throw BridgeError("De huidige lay-out is nog niet gekalibreerd.") }
        let now = DispatchTime.now().uptimeNanoseconds
        if observationFresh(sampledAt:observation.sampledAt,now:now) {
            observation.validationAttempts = attempt
            observation.validationMS = Double(now-validationStarted)/1e6
            return observation
        }
    }
    if mayReportNoInput(explicit:recoverablePreDispatch) {
        throw PreDispatchRejection(code:"observation_stale",description:"Drie verse opnames waren te langzaam voor bediening; er is niets verstuurd.",retryable:true)
    }
    throw BridgeError("Drie verse opnames waren te langzaam voor de 750 ms bedieningsgrens.")
}

func requireAligned(_ observation: Observation, recoverablePreDispatch: Bool = false) throws {
    let vision = MixerVision(bitmap:NSBitmapImageRep(cgImage:observation.image))
    guard vision.aligned == true else {
        if recoverablePreDispatch {
            throw PreDispatchRejection(code:"alignment_not_confirmed",description:"Rode maatmarkeringen niet gelijk of onleesbaar; er is niets verstuurd.",retryable:true)
        }
        throw BridgeError("Rode maatmarkeringen niet gelijk of onleesbaar; geen faderbeweging.")
    }
}
func validateMixDispatch(_ request: [String:Any], observation: Observation) throws {
    try requireLiveControlRequest(request)
    let now = DispatchTime.now().uptimeNanoseconds
    if let supplied = request["notAfterMonotonicNS"] {
        guard let deadline = supplied as? UInt64, deadline > 0 else {
            throw PreDispatchRejection(code:"invalid_deadline",description:"Ongeldige lokale deadline; er is niets verstuurd.",retryable:false)
        }
        guard now <= deadline else {
            throw PreDispatchRejection(code:"deadline_expired",description:"Lokale deadline verstreken; er is niets verstuurd.",retryable:false)
        }
    }
    guard now >= observation.sampledAt, now-observation.sampledAt <= 750_000_000 else {
        throw PreDispatchRejection(code:"observation_stale",description:"Waarneming is te oud voor bediening; er is niets verstuurd.",retryable:true)
    }
    if let supplied = request["expectedTracks"] {
        guard let titles = supplied as? [String:String], Set(titles.keys) == Set(["1","2"]),
              titles.values.allSatisfy({!$0.isEmpty}) else {
            throw PreDispatchRejection(code:"invalid_expected_tracks",description:"Ongeldige trackcontrole; er is niets verstuurd.",retryable:false)
        }
        guard (1...2).allSatisfy({deckTitle(observation,$0) == titles[String($0)]}) else {
            throw PreDispatchRejection(code:"tracks_changed",description:"Geladen muziek gewijzigd; er is niets verstuurd.",retryable:false)
        }
    }
}
func deckTitle(_ observation: Observation, _ deck: Int) -> String {
    observation.text(in:deck == 1 ? CGRect(x:45,y:174,width:430,height:20) : CGRect(x:731,y:174,width:430,height:20))
        .trimmingCharacters(in:CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn:"|")))
}
func browserWheelEvent(pixels: Int32, point: CGPoint) -> CGEvent? {
    let event = CGEvent(scrollWheelEvent2Source:nil,units:.pixel,wheelCount:1,wheel1:pixels,wheel2:0,wheel3:0)
    event?.location = point
    return event
}

func scrollBrowser(_ observation: Observation, pixels: Int32, preDispatch: (() throws -> Void)? = nil) async throws {
    try requireInputAccess()
    guard observation.calibrated, observation.text(in:CGRect(x:235,y:429,width:450,height:24)) == "26" else {
        throw BridgeError("Map 26 is niet meer geopend.")
    }
    let point = CGPoint(x:observation.window.frame.minX+850.0/1272*observation.window.frame.width,
                        y:observation.window.frame.minY+570.0/768*observation.window.frame.height)
    guard let move = CGEvent(mouseEventSource:nil,mouseType:.mouseMoved,mouseCursorPosition:point,mouseButton:.left),
          let wheel = browserWheelEvent(pixels:pixels,point:point) else {
        throw BridgeError("Browser-scrollgebeurtenis kon niet worden gemaakt; niets verstuurd.")
    }
    try preDispatch?()
    markNativeInputSent()
    move.post(tap:.cghidEventTap)
    // Do not depend on asynchronous mouseMoved updating the global cursor before
    // the wheel is constructed; otherwise the last EQ knob can receive it.
    wheel.post(tap:.cghidEventTap)
    try await Task.sleep(nanoseconds:100_000_000)
}
func handle(_ request: [String: Any]) async throws -> [String: Any] {
    try await withNativeInputTracking { try await handleRequest(request) }
}

private func handleRequest(_ request: [String: Any]) async throws -> [String: Any] {
    try nativeProcessRole.requireCommand(request["command"] as? String ?? "")
    if let command = request["command"] as? String,
       !["status","observe","capabilities","quit"].contains(command), !command.hasPrefix("dj") {
        try requireLiveControlRequest(request)
    }
    switch request["command"] as? String {
    case "status": return status()
    case "djReady":
        try await DJSessionHost.shared.authorize(interactive:false)
        return ["credentialAvailable":true]
    case "djAuthorize":
        try await DJSessionHost.shared.authorize(interactive:true)
        return ["credentialAvailable":true]
    case "djStart":
        try await DJSessionHost.shared.start()
        return ["started":true]
    case "djStop":
        DJSessionHost.shared.stop()
        return ["stopRequested":true]
    case "activate":
        try app().activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
        return ["activationRequested":true]
    case "quit":
        if !isControlWorker { DJSessionHost.shared.stop() }
        DispatchQueue.main.asyncAfter(deadline:.now()+0.2) { NSApplication.shared.terminate(nil) }
        return ["quitting":true]
    case "observe":
        let observation = try await observe(mixerOnly:request["fast"] as? Bool == true)
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
    case "openFolder26":
        return try await openMusicFolder26(request)
    case "loadChosenTrack":
        return try await loadChosenTrack(request)
    case "setPlayback":
        return try await setDesiredPlayback(request)
    case "loadVisible", "loadTrack":
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
        var before = try await checkedObservation()
        guard before.text(in:CGRect(x:235,y:429,width:450,height:24)) == "26", before.playing(deck:deck) == false else {
            throw BridgeError("Open map 26 en gebruik een stilstaand doeldeck.")
        }
        func matching(_ image: Observation) -> [TextToken] {
            image.tokens.filter { $0.rect.minY > 460 && $0.rect.maxY < 739 &&
                $0.rect.minX >= 540 && $0.rect.maxX < 891 && $0.confidence>0.8 &&
                $0.text.compare(expected,options:[.caseInsensitive,.diacriticInsensitive]) == .orderedSame }
        }
        var matches = matching(before)
        if matches.isEmpty && request["command"] as? String == "loadTrack" {
            try await scrollBrowser(before,pixels:100000)
            var previous = ""
            for _ in 0..<40 {
                before = try await checkedObservation()
                guard before.playing(deck:deck) == false,
                      before.text(in:CGRect(x:235,y:429,width:450,height:24)) == "26" else {
                    throw BridgeError("Laadtoestand gewijzigd; geen track geladen.")
                }
                matches = matching(before)
                if !matches.isEmpty { break }
                let signature = before.text(in:CGRect(x:350,y:465,width:500,height:260))
                if signature == previous { break }
                previous = signature
                try await scrollBrowser(before,pixels:-220)
            }
        }
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
        let before = try await checkedObservation(recoverablePreDispatch:true,mixerOnly:true)
        try requireAligned(before,recoverablePreDispatch:true)
        let x = deck == 1 ? 613.0 : 659.0
        let target = CGPoint(x:x,y:357-35*value)
        if let current = before.fader(deck:deck) {
            try await pointer(CGPoint(x:x,y:357-35*current),observation:before,dragTo:target,
                              preDispatch:{ try validateMixDispatch(request,observation:before) })
        } else { throw BridgeError("De faderstand is niet betrouwbaar afgelezen.") }
        let after = try await observe(mixerOnly:true)
        let measured = after.fader(deck:deck)
        return ["dispatched":true,"verified": measured.map{abs($0-value)<0.08} ?? false,
                "requested":value,"measured":measured as Any,"after":after.json]
    case "eqReset":
        guard let deck = request["deck"] as? Int, [1,2].contains(deck) else { throw BridgeError("Ongeldig deck.") }
        let bands = request["bands"] as? [String] ?? ["high","mid","low","trim"]
        let ys = ["high":225.0,"mid":254.0,"low":283.0,"trim":195.0]
        guard !bands.isEmpty, bands.allSatisfy({ys[$0] != nil}) else { throw BridgeError("Onbekende EQ-band.") }
        var before = try await checkedObservation(recoverablePreDispatch:true,mixerOnly:true)
        let expected = deckTitle(before,deck)
        for band in bands {
            guard deckTitle(before,deck) == expected else { throw BridgeError("Track gewijzigd tijdens EQ-reset.") }
            try await pointer(CGPoint(x:deck == 1 ? 613 : 659,y:ys[band]!),observation:before,clickCount:2,
                              preDispatch:{try requireLiveControlRequest(request)})
            before = try await checkedObservation(mixerOnly:true)
            // The event can precede Rekordbox's next rendered frame. Observe again;
            // never repeat the reset gesture just because the first image is old.
            for _ in 0..<3 {
                if MixerVision(bitmap:NSBitmapImageRep(cgImage:before.image)).neutral(deck,band) == true { break }
                try await Task.sleep(nanoseconds:50_000_000)
                before = try await checkedObservation(mixerOnly:true)
                guard deckTitle(before,deck) == expected else { throw BridgeError("Track gewijzigd tijdens EQ-reset.") }
            }
            guard MixerVision(bitmap:NSBitmapImageRep(cgImage:before.image)).neutral(deck,band) == true else {
                throw BridgeError("EQ-reset niet bevestigd; overgang niet klaar.")
            }
        }
        return ["dispatched":true,"verified":true,"bands":bands,"after":before.json]
    case "eqPair":
        guard let outgoing = request["outgoing"] as? Int, let incoming = request["incoming"] as? Int,
              Set([outgoing,incoming]) == Set([1,2]), let pixels = request["pixels"] as? Double,
              pixels.isFinite, pixels > 0, pixels <= 5 else { throw BridgeError("Ongeldige gekoppelde EQ-stap.") }
        let before = try await checkedObservation(recoverablePreDispatch:true,mixerOnly:true)
        try requireAligned(before,recoverablePreDispatch:true)
        guard before.playing(deck:1) == true, before.playing(deck:2) == true else { throw BridgeError("Beide decks moeten spelen.") }
        let started = DispatchTime.now().uptimeNanoseconds
        let outX = outgoing == 1 ? 613.0 : 659.0, inX = incoming == 1 ? 613.0 : 659.0
        // One pointer: decreasing the outgoing bass precedes increasing incoming bass.
        // Both small gestures are one operation, without a network request between them.
        try await pointer(CGPoint(x:outX,y:283),observation:before,dragTo:CGPoint(x:outX,y:283+pixels),
                          preDispatch:{ try validateMixDispatch(request,observation:before) })
        try await pointer(CGPoint(x:inX,y:283),observation:before,dragTo:CGPoint(x:inX,y:283-pixels),
                          preDispatch:{try requireLiveControlRequest(request)})
        let after = try await observe(mixerOnly:true)
        return ["dispatched":true,"verified":false,"pairMS":Double(DispatchTime.now().uptimeNanoseconds-started)/1e6,
                "note":"Paired GUI gestures; no claim of identical audio loudness.","after":after.json]
    case "mixStep":
        // Validate the complete operation before any input. The single native
        // pointer performs these small gestures serially, without API waits.
        var crossfader: Double?
        if let supplied = request["crossfader"] {
            guard let value = supplied as? Double, value.isFinite, (0...1).contains(value) else {
                throw BridgeError("mixStep crossfader vereist een waarde tussen 0 en 1.")
            }
            crossfader = value
        }
        let hasBass = ["outgoing","incoming","pixels"].contains { request[$0] != nil }
        var bass: (outgoing:Int,incoming:Int,pixels:Double)?
        if hasBass {
            guard let outgoing = request["outgoing"] as? Int, let incoming = request["incoming"] as? Int,
                  Set([outgoing,incoming]) == Set([1,2]), let pixels = request["pixels"] as? Double,
                  pixels.isFinite, pixels > 0, pixels <= 5 else {
                throw BridgeError("mixStep vereist twee verschillende decks en maximaal 5 pixels bass-overdracht.")
            }
            bass = (outgoing,incoming,pixels)
        }
        guard crossfader != nil || bass != nil else { throw BridgeError("mixStep bevat geen handeling.") }
        let before = try await checkedObservation(recoverablePreDispatch:true,mixerOnly:true)
        try requireAligned(before,recoverablePreDispatch:true)
        guard before.playing(deck:1) == true, before.playing(deck:2) == true else {
            throw BridgeError("Beide decks moeten spelen voor een mixstap.")
        }
        let started = DispatchTime.now().uptimeNanoseconds
        if let bass {
            let outX = bass.outgoing == 1 ? 613.0 : 659.0, inX = bass.incoming == 1 ? 613.0 : 659.0
            try await pointer(CGPoint(x:outX,y:283),observation:before,dragTo:CGPoint(x:outX,y:283+bass.pixels),
                              preDispatch:{ try validateMixDispatch(request,observation:before) })
            // From this point on, no error is a retryable pre-dispatch rejection.
            try await pointer(CGPoint(x:inX,y:283),observation:before,dragTo:CGPoint(x:inX,y:283-bass.pixels),
                              preDispatch:{try requireLiveControlRequest(request)})
        }
        if let value = crossfader {
            if bass == nil {
                try await pointer(CGPoint(x:585+101*value,y:385),observation:before,
                                  preDispatch:{ try validateMixDispatch(request,observation:before) })
            } else {
                try await pointer(CGPoint(x:585+101*value,y:385),observation:before,
                                  preDispatch:{try requireLiveControlRequest(request)})
            }
        }
        let after = try await observe(mixerOnly:true)
        let measured = MixerVision(bitmap:NSBitmapImageRep(cgImage:after.image)).crossfader
        let crossVerified = crossfader.flatMap { target in measured.map { abs($0-target) <= 0.04 } }
        return ["dispatched":true,"commandsSent":true,"verified":bass == nil && crossVerified == true,
                "crossfaderVerified":crossVerified as Any? ?? NSNull(),
                "measuredCrossfader":measured as Any? ?? NSNull(),
                "combinedMS":Double(DispatchTime.now().uptimeNanoseconds-started)/1e6,
                "note":"Small sequential local gestures on one pointer, not simultaneous events. EQ dB and audio loudness are not measured.",
                "after":after.json]
    case "mixGesture":
        return try await performMixGesture(request)
    case "closeStoppedDeck":
        return try await closeStoppedDeck(request)
    case "openSilentDeck":
        return try await openSilentDeck(request)
    case "launchAligned":
        guard let outgoing = request["outgoing"] as? Int, let incoming = request["incoming"] as? Int,
              Set([outgoing,incoming]) == Set([1,2]), let bpm = request["bpm"] as? Double,
              bpm.isFinite, (60...200).contains(bpm), let cueOffset = request["cueOffsetSeconds"] as? Double,
              cueOffset.isFinite, (0...2).contains(cueOffset),
              let clientPID = request["clientPID"] as? Int32, clientPID > 1,
              let titles = request["expectedTracks"] as? [String:String] else { throw BridgeError("Ongeldige lokale startplanning.") }
        let key = try mappings().first { $0["commandId"] == (incoming == 1 ? "3006" : "3106") }?["key"]
        guard let key else { throw BridgeError("Play-sneltoets ontbreekt.") }
        var scheduling: [[String:Any]] = []
        var sentAt: UInt64?, scheduledDue: UInt64?
        defer {
            let trace: [String:Any] = ["incoming":incoming,"outgoing":outgoing,
                "expectedTracks":titles,"attempts":scheduling,
                "commandsSent":NativeInputScope.progress?.sent ?? false,
                "sentAtMonotonicNS":sentAt as Any? ?? NSNull(),
                "dueMonotonicNS":scheduledDue as Any? ?? NSNull()]
            if let data = try? JSONSerialization.data(withJSONObject:trace,options:[.sortedKeys]) {
                try? data.write(to:URL(fileURLWithPath:socketDirectory+"/native-launch-progress.json"),options:.atomic)
            }
        }
        func validateStart(_ frame: Observation) throws {
            guard frame.playing(deck:outgoing) == true, frame.playing(deck:incoming) == false,
                  (1...2).allSatisfy({deckTitle(frame,$0) == titles[String($0)]}) else {
                throw BridgeError("Starttoestand gewijzigd.")
            }
            let vision = MixerVision(bitmap:NSBitmapImageRep(cgImage:frame.image))
            guard let cross = vision.crossfader, outgoing == 1 ? cross < 0.04 : cross > 0.96 else {
                throw BridgeError("Binnenkomend deck moet onhoorbaar zijn voor lokaal geplande start.")
            }
        }
        do {
            sentAt = try await retryUndispatchedLaunch(eventSequence:{NativeInputScope.progress?.sequence ?? 0},
                onMiss:{attempt,miss in
                    scheduling.append(["attempt":attempt,"phase":miss.phase,"result":"missed_without_input",
                        "dueMonotonicNS":miss.dueNS,"checkedMonotonicNS":miss.checkedNS,"latenessMS":miss.latenessMS])
                }) { attempt in
                try requireLiveControlRequest(request)
                let before = try await checkedObservation(recoverablePreDispatch:true,mixerOnly:true)
                try validateStart(before)
                let markers = MixerVision(bitmap:NSBitmapImageRep(cgImage:before.image)).markers(outgoing)
                let due = try nextLaunchDue(markers:markers,bpm:bpm,cueOffset:cueOffset,
                    sampledAt:before.sampledAt,now:DispatchTime.now().uptimeNanoseconds)
                scheduledDue = due
                if let requestDeadline = request["notAfterMonotonicNS"] as? UInt64, due > requestDeadline {
                    throw PreDispatchRejection(code:"launch_request_deadline",description:"Volgende maatgrens valt buiten de aanvraagdeadline; niets gestart.",retryable:true)
                }
                let event = try prepareKeyEvent(key)
                var fresh = before
                scheduling.append(["attempt":attempt,"phase":"planned","sampledAtMonotonicNS":before.sampledAt,
                    "dueMonotonicNS":due,"plannedAtMonotonicNS":DispatchTime.now().uptimeNanoseconds])
                return try await schedulePreparedLaunch(dueNS:due,prepare:{
                    try requireLiveControlRequest(request)
                    fresh = try await checkedObservation(recoverablePreDispatch:true,mixerOnly:true)
                    try validateStart(fresh)
                    // AX can take tens of milliseconds. Do it before the final
                    // wait instead of consuming the lateness allowance after it.
                    try requireInputAccess()
                    scheduling.append(["attempt":attempt,"phase":"ready","sampledAtMonotonicNS":fresh.sampledAt,
                        "readyAtMonotonicNS":DispatchTime.now().uptimeNanoseconds,"dueMonotonicNS":due])
                },post:{
                    let actual = try await sendPreparedKey(event,preDispatch:{
                        try requireLiveControlRequest(request)
                        guard kill(clientPID,0) == 0 else { throw BridgeError("Aanvrager gestopt; geplande start geannuleerd.") }
                        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == event.pid else {
                            throw inputAccessFailure(code:"rekordbox_not_frontmost",
                                message:"Rekordbox moet vooraan staan; geen start verstuurd.",retryable:true)
                        }
                        let dispatch = DispatchTime.now().uptimeNanoseconds
                        guard observationFresh(sampledAt:fresh.sampledAt,now:dispatch) else {
                            throw LaunchTimingMiss(dueNS:due,checkedNS:dispatch,phase:"freshness_at_post")
                        }
                        try requireLaunchTiming(dueNS:due,checkedNS:dispatch,phase:"immediate_post")
                    })
                    scheduling.append(["attempt":attempt,"phase":"posted","sentAtMonotonicNS":actual,
                        "dueMonotonicNS":due,"latenessMS":(Double(actual)-Double(due))/1e6])
                    return actual
                })
            }
        } catch let miss as LaunchTimingMiss {
            throw PreDispatchRejection(code:"launch_deadline_missed",
                description:"Lokale startdeadline na vier verse planningen gemist (\(Int(miss.latenessMS)) ms); geen Play verstuurd.",retryable:true)
        }
        let after = try await observe(mixerOnly:true)
        return ["dispatched":true,"commandsSent":true,"verified":false,
                "latenessMS":(Double(sentAt!)-Double(scheduledDue!))/1e6,"scheduling":scheduling,
                "redAlignedAfter":MixerVision(bitmap:NSBitmapImageRep(cgImage:after.image)).aligned as Any? ?? NSNull(),
                "after":after.json]
    case "eq":
        guard let deck = request["deck"] as? Int, [1,2].contains(deck),
              let band = request["band"] as? String, let y = ["high":225.0,"mid":254.0,"low":283.0,"trim":195.0][band],
              let pixels = request["pixels"] as? Double, pixels.isFinite, abs(pixels)<=40 else {
            throw BridgeError("EQ vereist deck, band (high/mid/low/trim) en pixels (-40…40).")
        }
        let before = try await checkedObservation(recoverablePreDispatch:true,mixerOnly:true)
        let x = deck == 1 ? 613.0 : 659.0
        try await pointer(CGPoint(x:x,y:y),observation:before,dragTo:CGPoint(x:x,y:y+pixels),
                          preDispatch:{try requireLiveControlRequest(request)})
        return ["dispatched":true,"verified":false,"after":try await observe(mixerOnly:true).json]
    case "crossfader":
        guard let value = request["value"] as? Double, value.isFinite, (0...1).contains(value) else {
            throw BridgeError("Crossfader vereist value tussen 0 en 1.")
        }
        let before = try await checkedObservation(recoverablePreDispatch:true,mixerOnly:true)
        try requireAligned(before,recoverablePreDispatch:true)
        try await pointer(CGPoint(x:585+101*value,y:385),observation:before,
                          preDispatch:{ try validateMixDispatch(request,observation:before) })
        return ["dispatched":true,"verified":false,"after":try await observe(mixerOnly:true).json]
    case "action":
        guard let action = request["action"] as? String, let id = commands[action],
              let key = try mappings().first(where: { $0["commandId"] == id })?["key"] else {
            throw BridgeError("Onbekende of niet toegewezen actie.")
        }
        guard let expected = request["expectedTrack"] as? String, !expected.isEmpty else {
            throw BridgeError("expectedTrack is verplicht om het bedoelde deck te controleren.")
        }
        let before = try await checkedObservation(recoverablePreDispatch:true,mixerOnly:true)
        guard before.calibrated else { throw BridgeError("De huidige lay-out is nog niet gekalibreerd.") }
        let region = action.hasPrefix("deck1.") ? CGRect(x:45,y:174,width:430,height:20) : CGRect(x:731,y:174,width:430,height:20)
        let title = before.text(in: region).trimmingCharacters(in:CharacterSet.whitespacesAndNewlines.union(CharacterSet(charactersIn:"|")))
        guard title.compare(expected, options: [.caseInsensitive, .diacriticInsensitive]) == .orderedSame else {
            throw BridgeError("De geladen track wijkt af: \(title)")
        }
        let ageMS = Double(DispatchTime.now().uptimeNanoseconds-before.sampledAt)/1_000_000
        guard ageMS <= 750 else { throw BridgeError("Waarneming is te oud (\(Int(ageMS)) ms); niets verstuurd.") }
        let started = DispatchTime.now().uptimeNanoseconds
        try requireLiveControlRequest(request)
        try await sendKey(key)
        let after = try await observe(mixerOnly:true)
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
        let lock = open(socketDirectory+"/"+nativeProcessRole.lockFile,O_CREAT | O_RDWR,0o600)
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
                } catch let error as PreDispatchRejection {
                    result = ["ok":false,"error":error.description,"errorKind":"pre_dispatch_guard",
                              "code":error.code,"commandsSent":false,"retryable":error.retryable]
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

#if !NATIVE_TRANSPORT_TESTS
@main enum BridgeMain {
static func main() throws {
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

}
}
#endif
