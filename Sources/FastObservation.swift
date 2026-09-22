import AppKit
import Darwin

final class NativeInputProgress: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0
    var sent: Bool { lock.lock(); defer { lock.unlock() }; return value > 0 }
    var sequence: Int { lock.lock(); defer { lock.unlock() }; return value }
    func markSent() { lock.lock(); defer { lock.unlock() }; value += 1 }
}

struct LoadObservationStale: Error {
    let ageMS: Double
}

struct LaunchTimingMiss: Error {
    let dueNS: UInt64
    let checkedNS: UInt64
    let phase: String
    var latenessMS: Double { (Double(checkedNS)-Double(dueNS))/1e6 }
}

func requireLaunchTiming(dueNS: UInt64, checkedNS: UInt64, phase: String) throws {
    // The existing 80 ms cutoff is unchanged. Checking an early timestamp must
    // never authorize an early key either.
    guard checkedNS >= dueNS, checkedNS-dueNS < 80_000_000 else {
        throw LaunchTimingMiss(dueNS:dueNS,checkedNS:checkedNS,phase:phase)
    }
}

// Expensive capture/AX work finishes BEFORE the intended beat. Only the final
// liveness/focus/deadline check and the already prepared event remain at post.
func schedulePreparedLaunch<T>(dueNS: UInt64,
    now: () -> UInt64 = { DispatchTime.now().uptimeNanoseconds },
    wait: (UInt64) async throws -> Void = { try await Task.sleep(nanoseconds:$0) },
    prepare: () async throws -> Void,
    post: () async throws -> T) async throws -> T {
    let preparationLead: UInt64 = 600_000_000
    let prepareAt = dueNS > preparationLead ? dueNS-preparationLead : 0
    var current = now()
    if current < prepareAt { try await wait(prepareAt-current) }
    try await prepare()
    current = now()
    while current < dueNS {
        try await wait(dueNS-current)
        current = now()
    }
    try requireLaunchTiming(dueNS:dueNS,checkedNS:current,phase:"wake")
    return try await post()
}

func retryUndispatchedLaunch<T>(maxAttempts: Int = 4, eventSequence: () -> Int,
    onMiss: (Int,LaunchTimingMiss) -> Void = { _,_ in },
    operation: (Int) async throws -> T) async throws -> T {
    for attempt in 1...max(1,maxAttempts) {
        let before = eventSequence()
        do { return try await operation(attempt) }
        catch let error as LaunchTimingMiss {
            onMiss(attempt,error)
            guard eventSequence() == before else {
                throw BridgeError("Play werd al verstuurd; de start wordt niet herhaald.")
            }
            if attempt >= max(1,maxAttempts) { throw error }
        }
    }
    fatalError("Bounded launch loop must return or throw")
}

func nextLaunchDue(markers: [Double], bpm: Double, cueOffset: Double,
                   sampledAt: UInt64, now: UInt64) throws -> UInt64 {
    guard now >= sampledAt, bpm.isFinite, (60...200).contains(bpm),
          cueOffset.isFinite, (0...2).contains(cueOffset),
          markers.allSatisfy({$0.isFinite}) else { throw BridgeError("Ongeldige startplanning.") }
    let gaps = zip(markers,markers.dropFirst()).map{$1-$0}.filter{$0>20}.sorted()
    guard gaps.count >= 2 else { throw BridgeError("Te weinig rode markeringen voor startplanning.") }
    let pixelsPerSecond = gaps[gaps.count/2]*bpm/240
    let age = Double(now-sampledAt)/1e9
    guard let marker = markers.first(where:{($0-636)/pixelsPerSecond-cueOffset-age > 0.08}) else {
        throw BridgeError("Geen toekomstige maatgrens zichtbaar; niets gestart.")
    }
    let offset = (marker-636)/pixelsPerSecond-cueOffset
    guard offset-age > 0, offset-age < 4.2 else { throw BridgeError("Ongeldige startdeadline.") }
    // Anchor to the actual capture time, not a second, later reading of now.
    return sampledAt+UInt64(offset*1e9)
}

// A prior scroll does not forbid recapturing before the next input. A NEW event
// in this attempt does: never repeat a load/click after partial execution.
func retryUndispatchedLoadInput<T>(maxAttempts: Int = 3, eventSequence: () -> Int,
                                  operation: (Int) async throws -> T) async throws -> T {
    for attempt in 1...max(1,maxAttempts) {
        let before = eventSequence()
        do { return try await operation(attempt) }
        catch let error as LoadObservationStale {
            guard eventSequence() == before else {
                throw BridgeError("Nieuwe bediening werd al verstuurd; verouderde waarneming niet opnieuw uitgevoerd.")
            }
            if attempt >= max(1,maxAttempts) { throw error }
        }
    }
    fatalError("Bounded load guard loop must return or throw")
}

enum NativeInputScope {
    static let storage = TaskLocal<NativeInputProgress?>(wrappedValue:nil)
    static var progress: NativeInputProgress? { storage.get() }
}

func mayReportNoInput(explicit: Bool = false) -> Bool {
    NativeInputScope.progress.map { !$0.sent } ?? explicit
}

func markNativeInputSent() { NativeInputScope.progress?.markSent() }

// Scope is per request, not a process-wide flag: concurrent read-only requests
// cannot reset another request's record of physical input already dispatched.
func withNativeInputTracking<T>(_ operation: () async throws -> T) async throws -> T {
    let progress = NativeInputProgress()
    return try await NativeInputScope.storage.withValue(progress) {
        do { return try await operation() }
        catch let error as PreDispatchRejection {
            guard !progress.sent else {
                throw BridgeError("Er is al bediening verstuurd; niet automatisch herhaald. \(error.description)")
            }
            throw error
        }
    }
}

func inputAccessFailure(code: String, message: String, retryable: Bool,
                        explicitNoInput: Bool = false) -> Error {
    if mayReportNoInput(explicit:explicitNoInput) {
        return PreDispatchRejection(code:code,description:message,retryable:retryable)
    }
    return BridgeError(message)
}

func observationFresh(sampledAt: UInt64, now: UInt64) -> Bool {
    now >= sampledAt && now-sampledAt <= 750_000_000
}

let guardIdentityRegions = [CGRect(x:35,y:25,width:320,height:25),
    CGRect(x:45,y:174,width:430,height:20),CGRect(x:731,y:174,width:430,height:20)]

// Exact comparison, never a tolerant image similarity score. Fresh dynamic
// pixels are captured regardless; unchanged title/header pixels can reuse their
// existing OCR meaning within one bounded native gesture.
func guardIdentityUnchanged(_ previous: CGImage, _ current: CGImage) -> Bool {
    observationRegionsUnchanged(previous,current,regions:guardIdentityRegions)
}

func observationRegionsUnchanged(_ previous: CGImage, _ current: CGImage, regions: [CGRect]) -> Bool {
    guard previous.width == 1272, previous.height == 768,
          current.width == 1272, current.height == 768 else { return false }
    for region in regions {
        guard let oldCrop = previous.cropping(to:region), let newCrop = current.cropping(to:region) else { return false }
        let old = NSBitmapImageRep(cgImage:oldCrop), new = NSBitmapImageRep(cgImage:newCrop)
        guard old.bitsPerPixel == new.bitsPerPixel, old.bitsPerPixel % 8 == 0,
              old.pixelsWide == new.pixelsWide, old.pixelsHigh == new.pixelsHigh,
              let oldBytes = old.bitmapData, let newBytes = new.bitmapData else { return false }
        let count = old.pixelsWide*old.bitsPerPixel/8
        for row in 0..<old.pixelsHigh {
            if memcmp(oldBytes+row*old.bytesPerRow,newBytes+row*new.bytesPerRow,count) != 0 { return false }
        }
    }
    return true
}

final class ObservationTextCache {
    let regions: [CGRect]
    private var image: CGImage?
    private var value: [TextToken] = []
    private(set) var reused = false
    init(regions: [CGRect]) { self.regions = regions }
    func tokens(for current: CGImage) -> [TextToken]? {
        guard let image, observationRegionsUnchanged(image,current,regions:regions) else {
            reused = false
            return nil
        }
        reused = true
        return value
    }
    func store(_ tokens: [TextToken], image: CGImage) {
        self.image = image; self.value = tokens; reused = false
    }
}

let observationHeaderRegion = CGRect(x:35,y:25,width:320,height:25)

func recognizedLayoutHeader(_ tokens: [TextToken]) -> Bool {
    func text(_ rect: CGRect) -> String {
        tokens.filter { rect.contains(CGPoint(x:$0.rect.midX,y:$0.rect.midY)) }
            .sorted { $0.rect.minX < $1.rect.minX }.map(\.text).joined(separator:" ")
    }
    return text(CGRect(x:35,y:25,width:250,height:25)).uppercased().contains("PERFORMANCE") &&
        text(CGRect(x:160,y:25,width:160,height:25)).contains("2Deck Horizontal")
}

final class ValidatedHeaderCache {
    private let cache = ObservationTextCache(regions:[observationHeaderRegion])
    func tokens(for image: CGImage) -> [TextToken]? { cache.tokens(for:image) }
    func resolve(_ tokens: [TextToken], image: CGImage) -> [TextToken] {
        guard image.width == 1272 && image.height == 768 else { return tokens }
        func inHeader(_ token: TextToken) -> Bool {
            observationHeaderRegion.contains(CGPoint(x:token.rect.midX,y:token.rect.midY))
        }
        if let header = cache.tokens(for:image) {
            return tokens.filter { !inHeader($0) } + header
        }
        let header = tokens.filter(inHeader)
        // Never preserve an invalid OCR result. A later observation of those
        // same pixels must be free to recover a readable, valid header.
        if recognizedLayoutHeader(header) { cache.store(header,image:image) }
        return tokens
    }
}

struct StoppedDeckCloseSpec {
    let deck: Int
    var playingDeck: Int { 3-deck }
    var endpoint: Double { deck == 1 ? 1 : 0 }
    init(_ request: [String:Any]) throws {
        guard let deck = request["deck"] as? Int, [1,2].contains(deck),
              let client = request["clientPID"] as? Int32, client > 1,
              let deadline = request["notAfterMonotonicNS"] as? UInt64, deadline > 0,
              let titles = request["expectedTracks"] as? [String:String],
              Set(titles.keys) == Set(["1","2"]),
              titles.values.allSatisfy({!$0.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty}),
              request["value"] == nil, request["crossfader"] == nil else {
            throw BridgeError("closeStoppedDeck vereist één gestopt deck, beide verwachte titels, aanvrager en deadline; een vrije faderwaarde is niet toegestaan.")
        }
        self.deck = deck
    }
}

struct SilentDeckOpenSpec {
    let deck: Int
    var endpoint: Double { deck == 1 ? 0 : 1 }
    init(_ request: [String:Any]) throws {
        guard let deck = request["deck"] as? Int, [1,2].contains(deck),
              let client = request["clientPID"] as? Int32, client > 1,
              let deadline = request["notAfterMonotonicNS"] as? UInt64, deadline > 0,
              let titles = request["expectedTracks"] as? [String:String],
              Set(titles.keys) == Set(["1","2"]),
              titles.values.allSatisfy({!$0.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty}),
              trackIdentityText(titles[String(deck)]!).trimmingCharacters(in:CharacterSet(charactersIn:". ")) != "not loaded",
              request["value"] == nil, request["crossfader"] == nil else {
            throw BridgeError("openSilentDeck vereist een gekozen geladen deck, beide verwachte titels, aanvrager en deadline; een vrije faderwaarde is niet toegestaan.")
        }
        self.deck = deck
    }
}

func silentDeckOpenAllowed(playing: [String:Bool], selectedChannel: Double?,
                          crossfader: Double?, normalAssignments: Bool) -> Bool {
    guard playing["1"] == false, playing["2"] == false, normalAssignments,
          let selectedChannel, selectedChannel.isFinite, (0.9...1).contains(selectedChannel),
          let crossfader, crossfader.isFinite, (0...1).contains(crossfader) else { return false }
    return true
}

func stoppedDeckCloseAllowed(deck: Int, targetPlaying: Bool?, otherPlaying: Bool?,
                             playingChannel: Double?, crossfader: Double?, normalAssignments: Bool) -> Bool {
    guard [1,2].contains(deck), targetPlaying == false, otherPlaying == true,
          normalAssignments, let playingChannel, playingChannel.isFinite,
          (0.9...1).contains(playingChannel), let crossfader, crossfader.isFinite,
          (0...1).contains(crossfader) else { return false }
    return true
}

struct ReadbackAssessment {
    let confirmed: Bool
    let mayWaitForRender: Bool
    let reasons: [String]
}

struct ReadbackOutcome<Frame> {
    let frame: Frame
    let assessment: ReadbackAssessment
    let attempts: Int
    let elapsedMS: Double
}

// The input closure runs exactly once. Only read() is inside the bounded poll.
// This distinction prevents delayed UI rendering from repeating a real input.
func dispatchThenReadback<Frame>(maxAttempts: Int = 6, timeoutNS: UInt64 = 2_500_000_000,
    dispatch: () async throws -> Void, read: () async throws -> Frame,
    assess: (Frame) -> ReadbackAssessment,
    now: () -> UInt64 = { DispatchTime.now().uptimeNanoseconds },
    pause: (UInt64) async throws -> Void = { try await Task.sleep(nanoseconds:$0) }) async throws -> ReadbackOutcome<Frame> {
    try await dispatch()
    return try await pollReadback(maxAttempts:maxAttempts,timeoutNS:timeoutNS,read:read,assess:assess,now:now,pause:pause)
}

// Read-only phase, also used after a completed multi-input gesture. The caller
// cannot supply an input closure here, so settling never repeats the gesture.
func pollReadback<Frame>(maxAttempts: Int = 6, timeoutNS: UInt64 = 2_500_000_000,
    read: () async throws -> Frame, assess: (Frame) -> ReadbackAssessment,
    now: () -> UInt64 = { DispatchTime.now().uptimeNanoseconds },
    pause: (UInt64) async throws -> Void = { try await Task.sleep(nanoseconds:$0) }) async throws -> ReadbackOutcome<Frame> {
    let start = now()
    var frame = try await read()
    var assessment = assess(frame)
    var attempts = 1
    while !assessment.confirmed && assessment.mayWaitForRender && attempts < max(1,maxAttempts) && now()-start < timeoutNS {
        try await pause(60_000_000)
        attempts += 1
        do { frame = try await read() }
        catch {
            assessment = ReadbackAssessment(confirmed:false,mayWaitForRender:false,
                reasons:assessment.reasons+["observation_failed: \(error)"])
            break
        }
        assessment = assess(frame)
    }
    return ReadbackOutcome(frame:frame,assessment:assessment,attempts:attempts,
                           elapsedMS:Double(now()-start)/1e6)
}

struct StoppedDeckCloseFacts {
    let calibrated: Bool
    let titles: [String:String]
    let targetPlaying: Bool?
    let otherPlaying: Bool?
    let normalAssignments: Bool?
    let playingChannel: Double?
    let crossfader: Double?
}

func assessStoppedDeckClose(_ facts: StoppedDeckCloseFacts, endpoint: Double,
                           expectedTitles: [String:String]) -> ReadbackAssessment {
    var reasons: [String] = []
    var contradiction = false
    if !facts.calibrated { reasons.append("layout_unreadable") }
    for deck in ["1","2"] {
        if let title = facts.titles[deck], !title.isEmpty {
            if title != expectedTitles[deck] { reasons.append("deck_\(deck)_title_changed"); contradiction = true }
        } else { reasons.append("deck_\(deck)_title_unreadable") }
    }
    if facts.targetPlaying == true { reasons.append("target_deck_started"); contradiction = true }
    else if facts.targetPlaying == nil { reasons.append("target_playback_unreadable") }
    if facts.otherPlaying == false { reasons.append("other_deck_stopped"); contradiction = true }
    else if facts.otherPlaying == nil { reasons.append("other_playback_unreadable") }
    if facts.normalAssignments == false { reasons.append("assignments_changed"); contradiction = true }
    else if facts.normalAssignments == nil { reasons.append("assignments_unreadable") }
    if let channel = facts.playingChannel, channel.isFinite {
        if channel < 0.9 || channel > 1 { reasons.append("playing_channel_not_open"); contradiction = true }
    } else { reasons.append("playing_channel_unreadable") }
    if let cross = facts.crossfader, cross.isFinite, (0...1).contains(cross) {
        if abs(cross-endpoint) > 0.02 { reasons.append("crossfader_not_at_endpoint") }
    } else { reasons.append("crossfader_unreadable") }
    return ReadbackAssessment(confirmed:reasons.isEmpty,mayWaitForRender:!contradiction,reasons:reasons)
}

func closeStoppedDeck(_ request: [String:Any]) async throws -> [String:Any] {
    let spec = try StoppedDeckCloseSpec(request)
    let started = DispatchTime.now().uptimeNanoseconds
    func check(_ frame: Observation) throws {
        try validateMixDispatch(request,observation:frame)
        let vision = MixerVision(bitmap:NSBitmapImageRep(cgImage:frame.image))
        guard stoppedDeckCloseAllowed(deck:spec.deck,targetPlaying:frame.playing(deck:spec.deck),
            otherPlaying:frame.playing(deck:spec.playingDeck),playingChannel:frame.fader(deck:spec.playingDeck),
            crossfader:vision.crossfader,
            normalAssignments:frame.mixerJSON["deck_assignments"] as? [String:String] == ["1":"left","2":"right"]) else {
            throw BridgeError("Sluiten vereist een bevestigd gestopt doeldeck en een spelend ander deck met open kanaal; niets verstuurd.")
        }
    }
    let before = try await checkedObservation(recoverablePreDispatch:true,mixerOnly:true)
    try check(before)
    let current = MixerVision(bitmap:NSBitmapImageRep(cgImage:before.image)).crossfader!
    if abs(current-spec.endpoint) <= 0.01 {
        return ["dispatched":false,"verified":true,"closedDeck":spec.deck,"after":before.json]
    }
    // Explicit staging primitive: the target is stopped. The only permitted
    // destination is the other, playing deck's extreme. Ordinary fader/mix
    // commands still require red-marker alignment and are not routed here.
    let expected = request["expectedTracks"] as! [String:String]
    let result = try await dispatchThenReadback(dispatch:{
        try await pointer(CGPoint(x:585+101*spec.endpoint,y:385),observation:before,
                          preDispatch:{try check(before)})
    },read:{try await observe(mixerOnly:true)},assess:{frame in
        let assignments = frame.mixerJSON["deck_assignments"] as? [String:String]
        let facts = StoppedDeckCloseFacts(calibrated:frame.calibrated,
            titles:["1":deckTitle(frame,1),"2":deckTitle(frame,2)],
            targetPlaying:frame.playing(deck:spec.deck),otherPlaying:frame.playing(deck:spec.playingDeck),
            normalAssignments:assignments.map { $0 == ["1":"left","2":"right"] },
            playingChannel:frame.fader(deck:spec.playingDeck),
            crossfader:MixerVision(bitmap:NSBitmapImageRep(cgImage:frame.image)).crossfader)
        return assessStoppedDeckClose(facts,endpoint:spec.endpoint,expectedTitles:expected)
    })
    let after = result.frame
    let measured = MixerVision(bitmap:NSBitmapImageRep(cgImage:after.image)).crossfader
    return ["dispatched":true,"verified":result.assessment.confirmed,"closedDeck":spec.deck,
            "pointerActions":1,"elapsedMS":Double(DispatchTime.now().uptimeNanoseconds-started)/1e6,
            "verification":["attempts":result.attempts,"elapsedMS":result.elapsedMS,
                "reasons":result.assessment.reasons,"renderWaitExhausted":!result.assessment.confirmed && result.assessment.mayWaitForRender,
                "targetPlaying":after.playing(deck:spec.deck) as Any? ?? NSNull(),
                "otherPlaying":after.playing(deck:spec.playingDeck) as Any? ?? NSNull(),
                "actualTitles":["1":deckTitle(after,1),"2":deckTitle(after,2)],"expectedTitles":expected],
            "requestedCrossfader":spec.endpoint,"measuredCrossfader":measured as Any? ?? NSNull(),
            "after":after.json]
}

struct SilentDeckOpenFacts {
    let calibrated: Bool
    let titles: [String:String]
    let playing: [String:Bool]
    let normalAssignments: Bool?
    let selectedChannel: Double?
    let crossfader: Double?
}

func assessSilentDeckOpen(_ facts: SilentDeckOpenFacts, endpoint: Double,
                          expectedTitles: [String:String]) -> ReadbackAssessment {
    var reasons: [String] = []
    var contradiction = false
    if !facts.calibrated { reasons.append("layout_unreadable") }
    for deck in ["1","2"] {
        if let title = facts.titles[deck], !title.isEmpty {
            if title != expectedTitles[deck] { reasons.append("deck_\(deck)_title_changed"); contradiction = true }
        } else { reasons.append("deck_\(deck)_title_unreadable") }
        if facts.playing[deck] == true { reasons.append("deck_\(deck)_started"); contradiction = true }
        else if facts.playing[deck] == nil { reasons.append("deck_\(deck)_playback_unreadable") }
    }
    if facts.normalAssignments == false { reasons.append("assignments_changed"); contradiction = true }
    else if facts.normalAssignments == nil { reasons.append("assignments_unreadable") }
    if let channel = facts.selectedChannel, channel.isFinite {
        if !(0.9...1).contains(channel) { reasons.append("selected_channel_not_open"); contradiction = true }
    } else { reasons.append("selected_channel_unreadable") }
    if let cross = facts.crossfader, cross.isFinite, (0...1).contains(cross) {
        if abs(cross-endpoint) > 0.02 { reasons.append("crossfader_not_at_endpoint") }
    } else { reasons.append("crossfader_unreadable") }
    return ReadbackAssessment(confirmed:reasons.isEmpty,mayWaitForRender:!contradiction,reasons:reasons)
}

func openSilentDeck(_ request: [String:Any]) async throws -> [String:Any] {
    let spec = try SilentDeckOpenSpec(request)
    let expected = request["expectedTracks"] as! [String:String]
    let started = DispatchTime.now().uptimeNanoseconds
    func facts(_ frame: Observation) -> SilentDeckOpenFacts {
        let assignments = frame.mixerJSON["deck_assignments"] as? [String:String]
        var playing: [String:Bool] = [:]
        for deck in 1...2 { playing[String(deck)] = frame.playing(deck:deck) }
        return SilentDeckOpenFacts(calibrated:frame.calibrated,
            titles:["1":deckTitle(frame,1),"2":deckTitle(frame,2)],playing:playing,
            normalAssignments:assignments.map{$0 == ["1":"left","2":"right"]},
            selectedChannel:frame.fader(deck:spec.deck),
            crossfader:MixerVision(bitmap:NSBitmapImageRep(cgImage:frame.image)).crossfader)
    }
    func check(_ frame: Observation) throws {
        try validateMixDispatch(request,observation:frame)
        let state = facts(frame)
        guard state.calibrated, silentDeckOpenAllowed(playing:state.playing,selectedChannel:state.selectedChannel,
            crossfader:state.crossfader,normalAssignments:state.normalAssignments == true) else {
            throw BridgeError("Een stille route openen vereist beide decks bevestigd gestopt en het gekozen kanaal open; niets verstuurd.")
        }
    }
    let before = try await checkedObservation(recoverablePreDispatch:true,mixerOnly:true)
    try check(before)
    if assessSilentDeckOpen(facts(before),endpoint:spec.endpoint,expectedTitles:expected).confirmed {
        return ["dispatched":false,"commandsSent":false,"verified":true,"openedDeck":spec.deck,"after":before.json]
    }
    // This opens a caller-selected route only while BOTH decks are stopped.
    // It never starts playback or bypasses any ordinary mixing/red-marker guard.
    let result = try await dispatchThenReadback(dispatch:{
        try await pointer(CGPoint(x:585+101*spec.endpoint,y:385),observation:before,
                          preDispatch:{try check(before)})
    },read:{try await observe(mixerOnly:true)},assess:{frame in
        assessSilentDeckOpen(facts(frame),endpoint:spec.endpoint,expectedTitles:expected)
    })
    let after = result.frame, state = facts(after)
    return ["dispatched":true,"commandsSent":true,"verified":result.assessment.confirmed,"openedDeck":spec.deck,
        "pointerActions":1,"elapsedMS":Double(DispatchTime.now().uptimeNanoseconds-started)/1e6,
        "verification":["attempts":result.attempts,"elapsedMS":result.elapsedMS,
            "reasons":result.assessment.reasons,"renderWaitExhausted":!result.assessment.confirmed && result.assessment.mayWaitForRender,
            "targetPlaying":state.playing[String(spec.deck)] as Any? ?? NSNull(),
            "otherPlaying":state.playing[String(3-spec.deck)] as Any? ?? NSNull(),
            "actualTitles":state.titles,"expectedTitles":expected],
        "requestedCrossfader":spec.endpoint,"measuredCrossfader":state.crossfader as Any? ?? NSNull(),"after":after.json]
}

// A bounded local execution primitive. Targets and duration come from the
// caller; this code makes no musical choices and never calls an AI service.
struct MixGestureSpec {
    let duration: Double
    let crossfader: Double?
    let outgoing: Int?
    let incoming: Int?
    let bassPixels: Double
    let clientPID: Int32
    let deadline: UInt64

    init(_ request: [String:Any]) throws {
        guard let duration = request["durationSeconds"] as? Double,
              duration.isFinite, (0.25...12).contains(duration),
              let client = request["clientPID"] as? Int32, client > 1,
              let deadline = request["notAfterMonotonicNS"] as? UInt64, deadline > 0,
              let tracks = request["expectedTracks"] as? [String:String],
              Set(tracks.keys) == Set(["1","2"]),
              tracks.values.allSatisfy({!$0.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty}) else {
            throw BridgeError("mixGesture vereist duur 0,25–12 s, beide verwachte titels, levende aanvrager en deadline.")
        }
        self.duration = duration; self.clientPID = client; self.deadline = deadline
        if let supplied = request["crossfader"] {
            guard let value = supplied as? Double, value.isFinite, (0...1).contains(value) else {
                throw BridgeError("mixGesture crossfader moet tussen 0 en 1 liggen.")
            }
            self.crossfader = value
        } else { self.crossfader = nil }
        if let supplied = request["bassPixels"] {
            guard let pixels = supplied as? Double, pixels.isFinite, pixels > 0, pixels <= 40,
                  let outgoing = request["outgoing"] as? Int,
                  let incoming = request["incoming"] as? Int,
                  Set([outgoing,incoming]) == Set([1,2]) else {
                throw BridgeError("mixGesture bass vereist verschillende decks en 0–40 positieve pixels.")
            }
            self.bassPixels = pixels; self.outgoing = outgoing; self.incoming = incoming
        } else {
            guard request["outgoing"] == nil && request["incoming"] == nil else {
                throw BridgeError("mixGesture decks vereisen ook bassPixels.")
            }
            self.bassPixels = 0; self.outgoing = nil; self.incoming = nil
        }
        guard self.crossfader != nil || self.bassPixels > 0 else {
            throw BridgeError("mixGesture bevat geen gevraagde beweging.")
        }
    }
}

struct MixGestureReadbackFacts {
    let calibrated: Bool
    let titles: [String:String]
    let playing: [String:Bool]
    let aligned: Bool?
    let crossfader: Double?
    let outgoingBass: Double?
    let incomingBass: Double?
}

struct MixGestureAssessment {
    let readback: ReadbackAssessment
    let crossVerified: Bool
    let bassVerified: Bool
}

func assessMixGesture(_ facts: MixGestureReadbackFacts, expectedTitles: [String:String],
                      targetCross: Double?, startOut: Double?, startIn: Double?) -> MixGestureAssessment {
    var reasons: [String] = []
    var contradiction = false
    if !facts.calibrated { reasons.append("layout_unreadable") }
    for deck in ["1","2"] {
        if let title = facts.titles[deck], !title.isEmpty {
            if title != expectedTitles[deck] { reasons.append("deck_\(deck)_title_changed"); contradiction = true }
        } else { reasons.append("deck_\(deck)_title_unreadable") }
        if facts.playing[deck] == false { reasons.append("deck_\(deck)_stopped"); contradiction = true }
        else if facts.playing[deck] == nil { reasons.append("deck_\(deck)_playback_unreadable") }
    }
    if facts.aligned == false { reasons.append("red_markers_misaligned"); contradiction = true }
    else if facts.aligned == nil { reasons.append("red_markers_unreadable") }
    var crossVerified = true
    if let target = targetCross {
        if let cross = facts.crossfader, cross.isFinite, (0...1).contains(cross) {
            crossVerified = abs(cross-target) <= 0.04
            if !crossVerified { reasons.append("crossfader_not_at_target") }
        } else { crossVerified = false; reasons.append("crossfader_unreadable") }
    }
    var bassVerified = true
    if let oldOut = startOut, let oldIn = startIn {
        if let outgoing = facts.outgoingBass, outgoing.isFinite,
           let incoming = facts.incomingBass, incoming.isFinite {
            bassVerified = outgoing < oldOut-0.01 && incoming > oldIn+0.01 && incoming <= 0.08
            if incoming > 0.08 { reasons.append("incoming_bass_above_neutral"); contradiction = true }
            else if !bassVerified { reasons.append("bass_direction_not_confirmed") }
        } else { bassVerified = false; reasons.append("bass_position_unreadable") }
    }
    return MixGestureAssessment(readback:ReadbackAssessment(confirmed:reasons.isEmpty,
        mayWaitForRender:!contradiction,reasons:reasons),crossVerified:crossVerified,bassVerified:bassVerified)
}

func performMixGesture(_ request: [String:Any]) async throws -> [String:Any] {
    let spec = try MixGestureSpec(request)
    let started = DispatchTime.now().uptimeNanoseconds
    var dispatched = false
    var completed = 0
    var appliedBass = 0.0
    func live() throws {
        try requireLiveControlRequest(request)
        guard kill(spec.clientPID,0) == 0 || errno == EPERM else {
            throw BridgeError("Aanvrager gestopt; lokale beweging afgebroken.")
        }
        guard DispatchTime.now().uptimeNanoseconds <= spec.deadline else {
            throw BridgeError("Deadline verstreken; lokale beweging afgebroken.")
        }
    }
    func guardFrame(_ frame: Observation) throws {
        try live()
        try validateMixDispatch(request,observation:frame)
        try requireAligned(frame,recoverablePreDispatch:!dispatched)
        guard frame.playing(deck:1) == true, frame.playing(deck:2) == true else {
            throw BridgeError("Beide decks moeten blijven spelen tijdens een mixbeweging.")
        }
    }
    let first = try await checkedObservation(recoverablePreDispatch:true,mixerOnly:true)
    try guardFrame(first)
    let firstVision = MixerVision(bitmap:NSBitmapImageRep(cgImage:first.image))
    let startCross = firstVision.crossfader
    if spec.crossfader != nil && startCross == nil { throw BridgeError("Crossfaderstand is onbekend; niets verstuurd.") }
    let startOut = spec.outgoing.flatMap { firstVision.eqPosition($0,"low") }
    let startIn = spec.incoming.flatMap { firstVision.eqPosition($0,"low") }
    if spec.bassPixels > 0 && (startOut == nil || startIn == nil) {
        throw BridgeError("Bass-knopstand is onbekend; niets verstuurd.")
    }
    let crossSteps = spec.crossfader.map { Int(ceil(abs($0-startCross!)/0.10)) } ?? 0
    let bassSteps = Int(ceil(spec.bassPixels/5))
    let steps = max(1,crossSteps,bassSteps,Int(ceil(spec.duration/0.5)))
    var current = first
    do {
        for step in 1...steps {
            if step > 1 { current = try await checkedObservation(mixerOnly:true,reusingGuardIdentity:current) }
            try guardFrame(current)
            if let outgoing = spec.outgoing, let incoming = spec.incoming {
                let vision = MixerVision(bitmap:NSBitmapImageRep(cgImage:current.image))
                guard let outPosition = vision.eqPosition(outgoing,"low"),
                      let inPosition = vision.eqPosition(incoming,"low"),
                      outPosition > -0.97, inPosition < -0.04,
                      vision.neutral(incoming,"low") == false else {
                    throw BridgeError("Bass-overdracht bereikt een grens of de stand is onbekend; geen extra EQ-beweging.")
                }
                let pixels = spec.bassPixels/Double(steps)
                let outX = outgoing == 1 ? 613.0 : 659.0
                let inX = incoming == 1 ? 613.0 : 659.0
                try await pointer(CGPoint(x:outX,y:283),observation:current,
                    dragTo:CGPoint(x:outX,y:283+pixels),preDispatch:{try guardFrame(current)})
                dispatched = true
                // The one physical pointer performs paired changes sequentially,
                // within this local operation. There is no network wait between.
                try await pointer(CGPoint(x:inX,y:283),observation:current,
                    dragTo:CGPoint(x:inX,y:283-pixels),preDispatch:{try guardFrame(current)})
                appliedBass += pixels
            }
            if let target = spec.crossfader, let origin = startCross {
                // A combined operation gets a new guard frame after the EQ pair,
                // so a slow render never turns into a stale fader movement.
                if spec.bassPixels > 0 {
                    current = try await checkedObservation(mixerOnly:true,reusingGuardIdentity:current)
                }
                try guardFrame(current)
                let value = origin+(target-origin)*Double(step)/Double(steps)
                try await pointer(CGPoint(x:585+101*value,y:385),observation:current,
                                  preDispatch:{try guardFrame(current)})
                dispatched = true
            }
            completed = step
            // Duration is a minimum interpolation interval, not a timing promise.
            // Slow capture/input is reflected in elapsedMS and the hard deadline.
            let due = started+UInt64(spec.duration*Double(step)/Double(steps)*1e9)
            let now = DispatchTime.now().uptimeNanoseconds
            if due > now { try await Task.sleep(nanoseconds:due-now) }
            try live()
        }
    } catch let error as PreDispatchRejection {
        if dispatched { throw BridgeError("Lokale beweging gedeeltelijk uitgevoerd (\(completed)/\(steps)); \(error.description)") }
        throw error
    } catch {
        if dispatched { throw BridgeError("Lokale beweging gedeeltelijk uitgevoerd (\(completed)/\(steps)); \(error)") }
        throw error
    }
    let expected = request["expectedTracks"] as! [String:String]
    func facts(_ frame: Observation) -> MixGestureReadbackFacts {
        let vision = MixerVision(bitmap:NSBitmapImageRep(cgImage:frame.image))
        var playing: [String:Bool] = [:]
        for deck in 1...2 { if let value = frame.playing(deck:deck) { playing[String(deck)] = value } }
        return MixGestureReadbackFacts(calibrated:frame.calibrated,
            titles:["1":deckTitle(frame,1),"2":deckTitle(frame,2)],playing:playing,
            aligned:frame.calibrated ? vision.aligned : nil,
            crossfader:vision.crossfader,
            outgoingBass:spec.outgoing.flatMap { vision.eqPosition($0,"low") },
            incomingBass:spec.incoming.flatMap { vision.eqPosition($0,"low") })
    }
    let settled = try await pollReadback(read:{try await observe(mixerOnly:true)},assess:{ frame in
        assessMixGesture(facts(frame),expectedTitles:expected,targetCross:spec.crossfader,
                         startOut:startOut,startIn:startIn).readback
    })
    let after = settled.frame
    let finalFacts = facts(after)
    let assessment = assessMixGesture(finalFacts,expectedTitles:expected,targetCross:spec.crossfader,
                                     startOut:startOut,startIn:startIn)
    return ["dispatched":dispatched,"commandsSent":dispatched,
        "verified":settled.assessment.confirmed,"crossfaderVerified":assessment.crossVerified,
        "bassDirectionVerified":assessment.bassVerified,"appliedBassPixels":appliedBass,
        "stepsCompleted":completed,"stepsRequested":steps,
        "elapsedMS":Double(DispatchTime.now().uptimeNanoseconds-started)/1e6,
        "verification":["attempts":settled.attempts,"elapsedMS":settled.elapsedMS,
            "reasons":settled.assessment.reasons,
            "renderWaitExhausted":!settled.assessment.confirmed && settled.assessment.mayWaitForRender,
            "aligned":finalFacts.aligned as Any? ?? NSNull(),"playing":finalFacts.playing,
            "actualTitles":finalFacts.titles,"expectedTitles":expected,
            "measuredCrossfader":finalFacts.crossfader as Any? ?? NSNull(),
            "measuredBass":["outgoing":finalFacts.outgoingBass as Any? ?? NSNull(),
                            "incoming":finalFacts.incomingBass as Any? ?? NSNull()]],
        "note":"Local interpolation with a fresh image guard per step. Paired inputs are sequential. EQ angle is not dB or audio loudness. Duration is best effort.",
        "after":after.json]
}
