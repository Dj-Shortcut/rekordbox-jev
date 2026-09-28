import AppKit
import ApplicationServices
import Darwin

// Native transport contains no model policy, credential access or autonomous
// loop. Replacing a track requires an explicitly expected stopped, closed deck.
private func transportTitle(_ value: String) -> String {
    value.folding(options:[.caseInsensitive,.diacriticInsensitive,.widthInsensitive],
                  locale:Locale(identifier:"en_US_POSIX"))
        .split(whereSeparator: { $0.isWhitespace }).joined(separator:" ")
}

// Matches the demo state's identity contract. No punctuation, spelling,
// diacritics or remix suffix is guessed; the raw observed title is preserved.
func trackIdentityText(_ value: String) -> String {
    value.precomposedStringWithCanonicalMapping
        .folding(options:[.caseInsensitive],locale:Locale(identifier:"en_US_POSIX"))
        .split(whereSeparator:{$0.isWhitespace}).joined(separator:" ")
}

struct FolderTrackIdentity {
    let file: String
    let aliases: [String]
    init(file: String, title: String?) {
        self.file = file
        let stem = URL(fileURLWithPath:file).deletingPathExtension().lastPathComponent
        aliases = [title ?? "",stem].map(trackIdentityText).filter{!$0.isEmpty}
    }
}

struct ChosenTrackIdentity {
    let file: String
    let candidates: [FolderTrackIdentity]
    func match(_ displayed: String) -> String? {
        let observed = trackIdentityText(displayed)
        guard !observed.isEmpty else { return nil }
        let exact = Set(candidates.filter{$0.aliases.contains(observed)}.map(\.file))
        if !exact.isEmpty { return exact == Set([file]) ? "exact" : nil }
        var prefix = observed
        if prefix.hasSuffix("…") { prefix.removeLast() }
        else if prefix.hasSuffix("...") { prefix.removeLast(3) }
        else if prefix.hasSuffix("..") { prefix.removeLast(2) }
        prefix = prefix.trimmingCharacters(in:.whitespacesAndNewlines)
        guard prefix.count >= 32 else { return nil }
        let matching = Set(candidates.filter { candidate in
            candidate.aliases.contains(where:{$0.hasPrefix(prefix)})
        }.map(\.file))
        return matching == Set([file]) ? "unique_display_prefix" : nil
    }
}

private func transportDeck(_ request: [String:Any]) throws -> Int {
    guard let deck = request["deck"] as? Int, [1,2].contains(deck) else {
        throw BridgeError("Verwacht deck 1 of 2.")
    }
    return deck
}

private func requireFreshTransport(_ observation: Observation, recoverablePreDispatch: Bool = false,
                                   retryableWithinLoad: Bool = false) throws {
    let now = DispatchTime.now().uptimeNanoseconds
    guard now >= observation.sampledAt, now-observation.sampledAt <= 750_000_000 else {
        if retryableWithinLoad {
            throw LoadObservationStale(ageMS:now >= observation.sampledAt ? Double(now-observation.sampledAt)/1e6 : -1)
        }
        if mayReportNoInput(explicit:recoverablePreDispatch) {
            throw PreDispatchRejection(code:"observation_stale",description:"Waarneming is te oud; er is geen laadbediening verstuurd.",retryable:true)
        }
        throw BridgeError("Waarneming is te oud; geen volgende laad- of afspeelhandeling verstuurd.")
    }
}

let loadBrowserHeadingRegion = CGRect(x:235,y:429,width:450,height:24)
let loadBrowserRowsRegion = CGRect(x:540,y:460,width:351,height:279)
let loadBrowserColumnsRegion = CGRect(x:235,y:451,width:1018,height:22)

func browserTitleColumn(_ tokens: [TextToken]) -> CGRect? {
    let headers = tokens.filter { $0.confidence > 0.8 &&
        loadBrowserColumnsRegion.contains(CGPoint(x:$0.rect.midX,y:$0.rect.midY)) }
    let titles = headers.filter { ["titel van muziekstuk","track title","title"].contains(trackIdentityText($0.text)) }
    guard titles.count == 1, let title = titles.first,
          let next = headers.filter({$0.rect.minX > title.rect.maxX && abs($0.rect.midY-title.rect.midY) < 5})
            .min(by:{$0.rect.minX < $1.rect.minX}) else { return nil }
    let left = floor(title.rect.minX)-6, right = floor(next.rect.minX)-6
    let top = ceil(title.rect.maxY)+2
    guard left >= 235, right-left >= 90, right <= 1253, top < 490 else { return nil }
    return CGRect(x:left,y:top,width:right-left,height:739-top)
}

func browserTitleTokens(_ tokens: [TextToken]) -> [TextToken] {
    guard let column = browserTitleColumn(tokens) else { return [] }
    return tokens.filter { $0.confidence > 0.8 && $0.rect.minY > column.minY &&
        $0.rect.maxY < column.maxY && $0.rect.minX >= column.minX && $0.rect.maxX < column.maxX }
        .sorted { $0.rect.minY < $1.rect.minY }
}

func loadBrowserPixelsUnchanged(_ recognized: CGImage, _ fresh: CGImage, requireRows: Bool) -> Bool {
    observationRegionsUnchanged(recognized,fresh,regions:requireRows
        ? [loadBrowserHeadingRegion,loadBrowserRowsRegion] : [loadBrowserHeadingRegion])
}

func loadBrowserRowRegion(_ row: CGRect, column: CGRect? = nil) -> CGRect {
    if let column {
        return CGRect(x:column.minX+2,y:max(column.minY,floor(row.midY)-8),
                      width:column.width-4,height:17).intersection(column)
    }
    // Actual title-cell body, excluding the rating column and cell separator.
    // The saved blinking-caret pair recognizes Vaal in both phases here.
    return CGRect(x:550,y:max(460,floor(row.midY)-8),width:333,height:17)
        .intersection(loadBrowserRowsRegion)
}

func browserRowSelected(_ image: CGImage, row: CGRect, column: CGRect? = nil) -> Bool {
    guard image.width == 1272 && image.height == 768 else { return false }
    let region = loadBrowserRowRegion(row,column:column)
    guard region.height >= 10 else { return false }
    let bitmap = NSBitmapImageRep(cgImage:image)
    var blue = 0, total = 0
    for y in Int(region.minY)..<Int(region.maxY) { for x in Int(region.minX)..<Int(region.maxX) {
        total += 1
        guard let c = bitmap.colorAt(x:x,y:y)?.usingColorSpace(.deviceRGB) else { continue }
        if c.blueComponent > 0.2 && c.blueComponent > c.greenComponent*1.3 && c.blueComponent > c.redComponent*1.7 { blue += 1 }
    }}
    // Selection is a blue row background, not a few colored title glyphs.
    return total > 0 && Double(blue)/Double(total) >= 0.45
}

// A repeated title-cell click can enter inline editing. An already selected
// row needs cancellation of any leftover editing context, not another click.
func establishBrowserSelection(alreadySelected: Bool,
    selectOnce: () async throws -> Void, cancelEditingOnce: () async throws -> Void) async throws {
    if alreadySelected { try await cancelEditingOnce() }
    else { try await selectOnce() }
}

// Browser text may be carried forward only after its entire relevant pixel
// region matches a new capture exactly. Dynamic deck facts remain newly read.
private func withConfirmedBrowserTokens(_ fresh: Observation, recognized: Observation,
                                        regions: [CGRect], currentRowTokens: [TextToken] = []) -> Observation {
    let browserTokens = recognized.tokens.filter { token in
        regions.contains { $0.contains(CGPoint(x:token.rect.midX,y:token.rect.midY)) }
    }
    let freshTokens = fresh.tokens.filter { token in
        !regions.contains { $0.contains(CGPoint(x:token.rect.midX,y:token.rect.midY)) }
    }
    return Observation(image:fresh.image,tokens:freshTokens+browserTokens+currentRowTokens,window:fresh.window,
        sampledAt:fresh.sampledAt,elapsedMS:fresh.elapsedMS,captureMS:fresh.captureMS,ocrMS:fresh.ocrMS,
        identityReused:fresh.identityReused,titleOCRReused:fresh.titleOCRReused,bpmOCRReused:fresh.bpmOCRReused,
        validationAttempts:fresh.validationAttempts,validationMS:fresh.validationMS)
}

private func requireFolder26(_ observation: Observation) throws {
    guard observation.calibrated,
          observation.text(in:CGRect(x:235,y:429,width:450,height:24)) == "26" else {
        throw BridgeError("De gekalibreerde browser toont map 26 niet.")
    }
}

private func requireEmptyStoppedDeck(_ observation: Observation, _ deck: Int) throws {
    let title = transportTitle(deckTitle(observation,deck))
        .trimmingCharacters(in:CharacterSet(charactersIn:". "))
    let metadata = observation.text(in:deck == 1
        ? CGRect(x:45,y:193,width:435,height:20)
        : CGRect(x:731,y:193,width:435,height:20))
    let bpm = observation.text(in:deck == 1
        ? CGRect(x:480,y:300,width:70,height:23)
        : CGRect(x:740,y:300,width:70,height:23))
    // "Not Loaded." was observed on the real empty deck. Missing OCR text is
    // never evidence that a deck is empty.
    guard title == "not loaded", metadata.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty,
          bpm.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty,
          observation.playing(deck:deck) == false else {
        throw BridgeError("Doeldeck is niet bevestigd leeg en gestopt; geen track geladen.")
    }
}

// Pure safety predicate shared with native regression tests. A missing visual
// measurement never establishes an inaudible route.
func replacementLoadAllowed(deck: Int, targetPlaying: Bool?, otherPlaying: Bool?,
                            targetFader: Double?, otherFader: Double?,
                            crossfader: Double?, normalAssignments: Bool,
                            allowSilentReplacement: Bool = false) -> Bool {
    guard [1,2].contains(deck), targetPlaying == false else { return false }
    if allowSilentReplacement { return otherPlaying == false }
    guard otherPlaying == true,
          normalAssignments, let targetFader, let otherFader, let crossfader,
          targetFader.isFinite, otherFader.isFinite, crossfader.isFinite,
          (0...1).contains(targetFader), (0...1).contains(otherFader),
          (0...1).contains(crossfader), otherFader >= 0.9 else { return false }
    let crossClosesTarget = deck == 1 ? crossfader >= 0.96 : crossfader <= 0.04
    let otherRouteOpen = deck == 1 ? crossfader >= 0.49 : crossfader <= 0.51
    return (targetFader <= 0.04 || crossClosesTarget) && otherRouteOpen
}

private func requireReplaceableDeck(_ observation: Observation, _ deck: Int, expected: String,
                                   silentOtherExpected: String? = nil) throws {
    let actual = transportTitle(deckTitle(observation,deck))
    guard !actual.isEmpty, actual == transportTitle(expected),
          actual.trimmingCharacters(in:CharacterSet(charactersIn:". ")) != "not loaded" else {
        throw BridgeError("De verwachte oude track is gewijzigd of niet leesbaar; niets vervangen.")
    }
    if let other = silentOtherExpected,
       transportTitle(deckTitle(observation,3-deck)) != transportTitle(other) {
        throw BridgeError("Andere decktitel gewijzigd tijdens stille vervanging; niets geladen.")
    }
    let vision = MixerVision(bitmap:NSBitmapImageRep(cgImage:observation.image))
    let assignments = observation.deckAssignments
    guard replacementLoadAllowed(deck:deck,targetPlaying:observation.playing(deck:deck),
        otherPlaying:observation.playing(deck:3-deck),targetFader:observation.fader(deck:deck),
        otherFader:observation.fader(deck:3-deck),crossfader:vision.crossfader,
        normalAssignments:assignments == ["1":"left","2":"right"],
        allowSilentReplacement:silentOtherExpected != nil) else {
        throw BridgeError("Vervangen vereist een gestopt en gesloten doeldeck terwijl het andere deck speelt met open route.")
    }
}

private func browserTitleRows(_ observation: Observation) -> [TextToken] {
    browserTitleTokens(observation.tokens)
}

struct BrowserScanProgress {
    private(set) var signature = ""
    private(set) var observations = 0
    private(set) var scrolls = 0
    private(set) var unchangedScrollAttempts = 0
    private(set) var firstTitle = ""
    private(set) var lastTitle = ""
    private(set) var pages: [[String:String]] = []

    mutating func observe(titles: [String]) -> Bool {
        observations += 1
        guard let first = titles.first, let last = titles.last else { return false }
        firstTitle = first; lastTitle = last
        let next = titles.map(transportTitle).joined(separator:"\n")
        let changed = next != signature
        if changed {
            signature = next
            pages.append(["first":first,"last":last])
        }
        return changed
    }
    mutating func finishScroll(changed: Bool) {
        scrolls += 1
        unchangedScrollAttempts = changed ? 0 : unchangedScrollAttempts+1
    }
    var stalled: Bool { unchangedScrollAttempts >= 3 }
}

private func uniqueChosenRow(_ observation: Observation, title: String, identity: ChosenTrackIdentity? = nil) -> TextToken? {
    uniqueChosenBrowserToken(browserTitleRows(observation),title:title,identity:identity)
}

func uniqueChosenBrowserToken(_ rows: [TextToken], title: String, identity: ChosenTrackIdentity? = nil) -> TextToken? {
    let wanted = transportTitle(title)
    if let identity, identity.match(title) == nil { return nil }
    let exact = rows.filter { row in row.confidence > 0.8 &&
        (identity.map{$0.match(row.text) != nil} ?? (transportTitle(row.text) == wanted)) }
    if exact.count == 1 { return exact[0] }
    return nil
}

func requireLoadTrackTitles(actual: [String:String], expected: [String:String], inputSent: Bool) throws {
    guard Set(expected.keys) == Set(["1","2"]) else {
        throw BridgeError("Ongeldige decktitelcontrole voor laden.")
    }
    guard (1...2).allSatisfy({transportTitle(actual[String($0)] ?? "") == transportTitle(expected[String($0)] ?? "")}) else {
        let message = "Geladen decktitels gewijzigd tijdens laden; geen volgende laadhandeling verstuurd."
        if !inputSent {
            throw PreDispatchRejection(code:"tracks_changed",description:message,retryable:true)
        }
        throw BridgeError(message)
    }
}

func loadChosenTrack(_ request: [String:Any]) async throws -> [String:Any] {
    var inputSent = false
    var guardTrace: [[String:Any]] = []
    let deck = try transportDeck(request)
    let replacing = request["replaceStopped"] as? Bool == true
    let expectedOld = request["expectedTrack"] as? String
    let allowSilent = request["allowSilentReplacement"] as? Bool == true
    let expectedOther = request["expectedOtherTrack"] as? String
    if allowSilent {
        guard replacing, let expectedOther, !transportTitle(expectedOther).isEmpty else {
            throw BridgeError("Stille vervanging vereist replaceStopped en expectedOtherTrack; beide decks moeten gestopt blijven.")
        }
    }
    if replacing {
        guard request["openingOnly"] as? Bool != true,
              let expectedOld, !transportTitle(expectedOld).isEmpty else {
            throw BridgeError("Vervangen vereist expectedTrack en mag geen openingsactie zijn.")
        }
    } else if let supplied = request["expectedTrack"] {
        guard let expected = supplied as? String,
              transportTitle(expected).trimmingCharacters(in:CharacterSet(charactersIn:". ")) == "not loaded" else {
            throw BridgeError("Deze laadactie vereist een verwacht leeg doeldeck.")
        }
    }
    guard let clientPID = request["clientPID"] as? Int32, clientPID > 1,
          let deadline = request["notAfterMonotonicNS"] as? UInt64, deadline > 0 else {
        throw BridgeError("Laden vereist een levende aanvrager en een lokale deadline.")
    }
    guard let filename = request["file"] as? String, !filename.isEmpty,
          URL(fileURLWithPath:filename).lastPathComponent == filename else {
        throw BridgeError("Verwacht een bestandsnaam uit map 26.")
    }
    let file = allowedMusic.appendingPathComponent(filename).resolvingSymlinksInPath()
    guard file.deletingLastPathComponent() == allowedMusic.resolvingSymlinksInPath(),
          FileManager.default.fileExists(atPath:file.path) else {
        throw BridgeError("Gekozen bestand is niet aanwezig binnen map 26.")
    }
    let manifest = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("evidence/inventory.json")
    guard let inventory = try JSONSerialization.jsonObject(with:Data(contentsOf:manifest)) as? [String:Any],
          let tracks = inventory["tracks"] as? [[String:Any]] else {
        throw BridgeError("Muziekinventaris is niet leesbaar.")
    }
    let matchingFiles = tracks.filter { $0["file"] as? String == filename }
    guard matchingFiles.count == 1 else { throw BridgeError("Bestand is niet uniek geïnventariseerd.") }
    func titleFor(_ track: [String:Any]) -> String {
        if let title = track["title"] as? String, !title.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty {
            return title
        }
        return (track["file"] as? String).map { URL(fileURLWithPath:$0).deletingPathExtension().lastPathComponent } ?? ""
    }
    let expected = titleFor(matchingFiles[0])
    let identity = ChosenTrackIdentity(file:filename,candidates:tracks.compactMap { track in
        guard let file = track["file"] as? String else { return nil }
        return FolderTrackIdentity(file:file,title:track["title"] as? String)
    })
    guard !trackIdentityText(expected).isEmpty, identity.match(expected) == "exact" else {
        throw BridgeError("Tracktitel is niet uniek in de volledige inventaris; titelherkenning kan het bestand niet bewijzen.")
    }
    func requireLiveRequest() throws {
        try requireLiveControlRequest(request)
        guard kill(clientPID,0) == 0 || errno == EPERM else {
            throw BridgeError("Aanvrager is gestopt; geen verdere laadbediening uitgevoerd.")
        }
        guard DispatchTime.now().uptimeNanoseconds < deadline else {
            throw BridgeError("Lokale laaddeadline verstreken; geen verdere laadbediening uitgevoerd.")
        }
    }
    func requireLoadObservation(_ image: Observation) throws {
        try requireLiveRequest()
        try requireFolder26(image)
        if let expectedTracks = request["expectedTracks"] as? [String:String] {
            try requireLoadTrackTitles(actual:["1":deckTitle(image,1),"2":deckTitle(image,2)],
                                       expected:expectedTracks,inputSent:inputSent)
        }
        if replacing {
            try requireReplaceableDeck(image,deck,expected:expectedOld!,
                                       silentOtherExpected:allowSilent ? expectedOther : nil)
        }
        else { try requireEmptyStoppedDeck(image,deck) }
        if request["openingOnly"] as? Bool == true {
            try requireEmptyStoppedDeck(image,3-deck)
        }
        try requireFreshTransport(image,recoverablePreDispatch:!inputSent,retryableWithinLoad:true)
    }
    func freshInputGuard(_ recognized: Observation, title: String? = nil, selected: Bool = false,
                         phase: String = "before_scroll") async throws -> Observation {
        var browser = recognized
        for attempt in 1...5 {
            try requireLiveRequest()
            let fresh = try await checkedObservation(recoverablePreDispatch:!inputSent,mixerOnly:true)
            guard let column = browserTitleColumn(browser.tokens) else {
                throw BridgeError("Titelkolom niet eenduidig leesbaar; geen laadbediening verstuurd.")
            }
            let row = title.flatMap { uniqueChosenRow(browser,title:$0,identity:identity) }
            let rowRegion = row.map { loadBrowserRowRegion($0.rect,column:column) }
            let headingSame = observationRegionsUnchanged(browser.image,fresh.image,regions:[loadBrowserHeadingRegion,loadBrowserColumnsRegion])
            let rowPixelsSame = rowRegion.map { observationRegionsUnchanged(browser.image,fresh.image,regions:[$0]) } ?? (title == nil)
            // Re-recognize the intended row in the *fresh* frame. Caret blinking
            // may alter pixels without altering title identity; neither old row
            // text nor a fuzzy title comparison establishes the current title.
            let currentTokens: [TextToken]
            if rowPixelsSame, let row { currentTokens = [row] }
            else { currentTokens = try rowRegion.map { try recognizeTextRegion(fresh.image,$0) } ?? [] }
            let currentRow = title.flatMap { uniqueChosenBrowserToken(currentTokens,title:$0,identity:identity) }
            let rowConfirmed = title == nil || currentRow != nil
            let selectionConfirmed = !selected || currentRow.map { browserRowSelected(fresh.image,row:$0.rect,column:column) } == true
            guardTrace.append(["phase":phase,"attempt":attempt,"recognizedSampledAtNS":browser.sampledAt,
                "freshSampledAtNS":fresh.sampledAt,"headingUnchanged":headingSame,"titleRowPixelsUnchanged":rowPixelsSame,
                "titleRowConfirmed":rowConfirmed,"currentRowOCR":currentTokens.map(\.text),
                "rowOCRReused":rowPixelsSame,"identityMatch":currentRow.flatMap { identity.match($0.text) } as Any? ?? NSNull(),
                "guardAgeMS":Double(DispatchTime.now().uptimeNanoseconds-fresh.sampledAt)/1e6,
                "titleFound":row != nil,"selectionConfirmed":selectionConfirmed,
                "rowY":row.map { $0.rect.midY } as Any? ?? NSNull(),"inputAlreadySent":inputSent])
            if headingSame && rowConfirmed && selectionConfirmed {
                let confirmed = withConfirmedBrowserTokens(fresh,recognized:browser,
                    regions:[loadBrowserHeadingRegion,loadBrowserColumnsRegion],currentRowTokens:currentTokens)
                try requireLoadObservation(confirmed)
                // Row OCR and replacement checks consume time after capture.
                // Recapture here, before hover/click, if they used the input reserve.
                let now = DispatchTime.now().uptimeNanoseconds
                guard observationHasInputBudget(sampledAt:confirmed.sampledAt,now:now,reserveNS:150_000_000) else {
                    throw LoadObservationStale(ageMS:Double(now-confirmed.sampledAt)/1e6)
                }
                return confirmed
            }
            if attempt == 1 {
                // Local diagnostic snapshots explain which pixels changed;
                // never infer a lost click or replay it from an old render.
                for (suffix,frame) in [("recognized",browser),("fresh",fresh)] {
                    let path = socketDirectory+"/native-load-guard-\(clientPID)-\(phase)-\(suffix).png"
                    try? NSBitmapImageRep(cgImage:frame.image).representation(using:.png,properties:[:])?
                        .write(to:URL(fileURLWithPath:path))
                }
            }
            if attempt < 5 {
                try await Task.sleep(nanoseconds:80_000_000)
                browser = try await observe()
                try requireFolder26(browser)
                // The next iteration identifies the unique title again and
                // derives a new row rectangle. No pointer/key occurs here.
            }
        }
        if !inputSent {
            throw PreDispatchRejection(code:"browser_unsettled",description:"Browserrij bleef onstabiel na vijf nieuwe controles; er is geen laadbediening verstuurd.",retryable:true)
        }
        throw BridgeError("Browserrij of selectie bleef onbevestigd na vijf nieuwe controles (\(phase)); geen volgende laadhandeling verstuurd.")
    }
    func immediateInputGuard(_ frame: Observation) throws {
        // Deck/title/route facts were checked on this immutable captured frame.
        // Re-analyzing the same pixels adds latency, not fresher evidence.
        try requireLiveRequest()
        try requireFreshTransport(frame,retryableWithinLoad:true)
    }
    func withFreshLoadInput<T>(_ browser: Observation, title: String? = nil, selected: Bool = false,
                              phase: String, operation: (Observation) async throws -> T) async throws -> T {
        do {
            return try await retryUndispatchedLoadInput(eventSequence:{NativeInputScope.progress?.sequence ?? 0}) { attempt in
                do {
                    let frame = try await freshInputGuard(browser,title:title,selected:selected,phase:phase)
                    return try await operation(frame)
                } catch let stale as LoadObservationStale {
                    guardTrace.append(["phase":phase,"freshnessAttempt":attempt,"staleAgeMS":stale.ageMS,
                        "inputAlreadySent":inputSent,"guardRetry":"new_capture_before_next_input"])
                    throw stale
                }
            }
        } catch let stale as LoadObservationStale {
            if !inputSent {
                throw PreDispatchRejection(code:"observation_stale",description:"Drie laadcontroles waren te oud; er is geen laadbediening verstuurd.",retryable:true)
            }
            throw BridgeError("Drie verse laadcontroles waren te oud (laatste \(Int(stale.ageMS)) ms, \(phase)); geen volgende laadhandeling verstuurd.")
        }
    }
    try requireLiveRequest()
    let lookupStarted = DispatchTime.now().uptimeNanoseconds
    var progress = BrowserScanProgress()
    var finished = false
    func publishProgress(_ phase: String) {
        let data: [String:Any] = ["clientPID":clientPID,"phase":phase,"target":expected,
            "firstVisibleTitle":progress.firstTitle,"lastVisibleTitle":progress.lastTitle,
            "observations":progress.observations,"scrolls":progress.scrolls,
            "guardChecks":guardTrace,
            "unchangedScrollAttempts":progress.unchangedScrollAttempts,
            "elapsedSeconds":Double(DispatchTime.now().uptimeNanoseconds-lookupStarted)/1e9]
        if let encoded = try? JSONSerialization.data(withJSONObject:data,options:[.sortedKeys]) {
            try? encoded.write(to:URL(fileURLWithPath:socketDirectory+"/native-load-progress.json"),options:.atomic)
        }
    }
    defer { if !finished { publishProgress("failed") } }
    var before = try await observe()
    try requireFolder26(before)
    guard browserTitleColumn(before.tokens) != nil else {
        throw PreDispatchRejection(code:"browser_columns_unknown",
            description:"Titelkolom niet eenduidig leesbaar; geen laadbediening verstuurd.",retryable:false)
    }
    _ = progress.observe(titles:browserTitleRows(before).map(\.text))
    publishProgress("searching")
    // The actual folder-26 view has no search field. Try the visible rows first,
    // then use a bounded scan of the actual browser. This is not a fast lookup;
    // timing and capture count are returned so its cost remains visible.
    var row = uniqueChosenRow(before,title:expected,identity:identity)
    if row == nil {
        var direction: Int32 = 100000
        // One repeated captured page can be an old rendered frame or a dropped
        // wheel event, not the end of the folder. Confirm that page without new
        // input, then retry scrolling; only three settled failed attempts stall.
        for _ in 0..<40 {
            try await withFreshLoadInput(before,phase:"before_scroll") { guardFrame in
                try await scrollBrowser(guardFrame,pixels:direction,preDispatch:{try immediateInputGuard(guardFrame)})
            }
            inputSent = true
            before = try await observe()
            try requireFolder26(before)
            var changed = progress.observe(titles:browserTitleRows(before).map(\.text))
            publishProgress("searching")
            row = uniqueChosenRow(before,title:expected,identity:identity)
            if row == nil && !changed && direction < 0 {
                for _ in 0..<2 {
                    try requireLiveRequest()
                    try await Task.sleep(nanoseconds:120_000_000)
                    before = try await observe()
                    try requireFolder26(before)
                    changed = progress.observe(titles:browserTitleRows(before).map(\.text)) || changed
                    publishProgress("waiting_for_browser")
                    row = uniqueChosenRow(before,title:expected,identity:identity)
                    if row != nil || changed { break }
                }
            }
            // A top-of-folder scroll from an already visible first page should
            // not count as a failed downward page movement.
            progress.finishScroll(changed:direction > 0 || changed)
            publishProgress("searching")
            if row != nil || progress.stalled { break }
            direction = -220
        }
    }
    let lookupSeconds = Double(DispatchTime.now().uptimeNanoseconds-lookupStarted)/1e9
    guard row != nil else {
        let range = "\(progress.firstTitle) – \(progress.lastTitle)"
        let reason = progress.stalled ? "Browser bleef na drie bevestigde scrollpogingen op dezelfde regels" : "Maximaal 40 browserstappen bereikt"
        throw BridgeError("\(reason) (\(range)); \(expected) niet uniek zichtbaar, niets geladen (\(progress.observations) waarnemingen, \(progress.scrolls) scrollpogingen).")
    }
    publishProgress("loading")
    try await withFreshLoadInput(before,title:expected,phase:"before_selection") { selectionGuard in
    guard let chosen = uniqueChosenRow(selectionGuard,title:expected,identity:identity) else {
        throw BridgeError("Gekozen titel is niet uniek in het verse selectievenster; niets geselecteerd.")
    }
    let alreadySelected = browserRowSelected(selectionGuard.image,row:chosen.rect,column:browserTitleColumn(selectionGuard.tokens))
    try await establishBrowserSelection(alreadySelected:alreadySelected,selectOnce:{
        try await pointer(CGPoint(x:chosen.rect.midX,y:chosen.rect.midY),observation:selectionGuard,
                          preDispatch:{try immediateInputGuard(selectionGuard)})
    },cancelEditingOnce:{
        guard try mappings().allSatisfy({ !["esc","escape"].contains(($0["key"] ?? "").lowercased()) }) else {
            throw BridgeError("Escape heeft een aangepaste Rekordbox-actie; bestaande selectie niet gewijzigd.")
        }
        try await sendKey("escape",preDispatch:{try immediateInputGuard(selectionGuard)})
    })
    inputSent = true
    guardTrace.append(["phase":"selection_context","alreadySelected":alreadySelected,
        "selectionClicks":alreadySelected ? 0 : 1,"editingCancelKeys":alreadySelected ? 1 : 0])
    }
    // Clicking may change focus and external input may have changed the deck.
    // Re-read after selection, before the only load shortcut is dispatched.
    before = try await observe()
    try requireFolder26(before)
    try await withFreshLoadInput(before,title:expected,selected:true,phase:"before_load") { loadGuard in
        try await sendKey(deck == 1 ? "shift + cursor left" : "shift + cursor right",
                          preDispatch:{try immediateInputGuard(loadGuard)})
    }
    var after = try await observe(mixerOnly:true)
    var confirmations = 0
    for attempt in 0..<5 {
        if attempt > 0 { after = try await observe(mixerOnly:true) }
        let matches = after.calibrated && identity.match(deckTitle(after,deck)) != nil
        confirmations = matches ? confirmations+1 : 0
        if confirmations >= 2 { break }
        try await Task.sleep(nanoseconds:60_000_000)
    }
    finished = true
    publishProgress(confirmations >= 2 ? "verified" : "unverified")
    return ["dispatched":true,"verified":confirmations >= 2,
            "expectedTitle":expected,"loadedTitle":deckTitle(after,deck),
            "identityMatch":identity.match(deckTitle(after,deck)) as Any? ?? NSNull(),
            "file":filename,"deck":deck,"lookup_observations":progress.observations,
            "lookup_scrolls":progress.scrolls,"lookup_pages":progress.pages,
            "search_seconds":lookupSeconds,"guardChecks":guardTrace,"after":after.json]
}

func nativeRemainingSeconds(metadata: String) -> Double? {
    // Only the explicit negative remaining-time clock establishes the end.
    // Elapsed 00:00.0, BPM, pitch, missing OCR and ambiguous clocks do not.
    let expression = try! NSRegularExpression(pattern:#"(?<![0-9:])[-−]([0-9]{2,3}):([0-5][0-9])[.,]([0-9])(?![0-9:.,])"#)
    let matches = expression.matches(in:metadata,range:NSRange(metadata.startIndex...,in:metadata))
    guard matches.count == 1, let match = matches.first else { return nil }
    let parts = (1...3).compactMap { index -> Double? in
        guard let range = Range(match.range(at:index),in:metadata) else { return nil }
        return Double(metadata[range])
    }
    guard parts.count == 3 else { return nil }
    return parts[0]*60+parts[1]+parts[2]/10
}

func endOnlyStopAllowed(desired: Bool, metadata: String) -> Bool {
    guard !desired, let remaining = nativeRemainingSeconds(metadata:metadata) else { return false }
    return remaining <= 0.1
}

func setDesiredPlayback(_ request: [String:Any]) async throws -> [String:Any] {
    let deck = try transportDeck(request)
    guard let desired = request["playing"] as? Bool,
          let expected = request["expectedTrack"] as? String,
          !transportTitle(expected).isEmpty,
          transportTitle(expected).trimmingCharacters(in:CharacterSet(charactersIn:". ")) != "not loaded" else {
        throw BridgeError("Gewenste afspeelstand en verwachte geladen titel zijn verplicht.")
    }
    let endOnly = request["endOnly"] as? Bool == true
    guard !endOnly || !desired else {
        throw BridgeError("endOnly is uitsluitend toegestaan voor playing=false.")
    }
    let commandID = deck == 1 ? "3006" : "3106"
    guard let key = try mappings().first(where:{$0["commandId"] == commandID})?["key"], !key.isEmpty else {
        throw BridgeError("Play/pauze-sneltoets ontbreekt.")
    }
    let before = try await checkedObservation(recoverablePreDispatch:true,mixerOnly:true)
    guard transportTitle(deckTitle(before,deck)) == transportTitle(expected),
          let current = before.playing(deck:deck) else {
        throw BridgeError("Geladen track of afspeelstand is gewijzigd of onbekend; niets verstuurd.")
    }
    if endOnly {
        let metadata = before.text(in:deck == 1
            ? CGRect(x:45,y:193,width:435,height:20)
            : CGRect(x:731,y:193,width:435,height:20))
        guard endOnlyStopAllowed(desired:desired,metadata:metadata) else {
            throw BridgeError("Trackeinde is niet vers bevestigd met maximaal 0,1 seconde resterend; geen stop verstuurd.")
        }
    }
    if request["openingOnly"] as? Bool == true {
        guard desired else { throw BridgeError("Een openingsstart vereist playing=true.") }
        try requireEmptyStoppedDeck(before,3-deck)
        let vision = MixerVision(bitmap:NSBitmapImageRep(cgImage:before.image))
        let assignments = before.deckAssignments
        guard let channel = before.fader(deck:deck), channel >= 0.9,
              assignments == ["1":"left","2":"right"],
              let cross = vision.crossfader,
              abs(cross-0.5) <= 0.01 || (deck == 1 ? cross <= 0.01 : cross >= 0.99),
              ["low","mid","high","trim"].allSatisfy({vision.neutral(deck,$0) == true}) else {
            throw BridgeError("Openingsdeck heeft geen bevestigde open route met neutrale EQ; niets gestart.")
        }
    }
    if current == desired {
        return ["dispatched":false,"verified":true,"playing":desired,"after":before.json]
    }
    try requireFreshTransport(before,recoverablePreDispatch:true)
    try requireLiveControlRequest(request)
    try await sendKey(key)
    var after = try await observe(mixerOnly:true)
    for attempt in 0..<5 {
        if attempt > 0 { after = try await observe(mixerOnly:true) }
        let trackMatches = after.calibrated && transportTitle(deckTitle(after,deck)) == transportTitle(expected)
        if trackMatches && after.playing(deck:deck) == desired {
            return ["dispatched":true,"verified":true,"playing":desired,"after":after.json]
        }
        if !trackMatches { break }
        try await Task.sleep(nanoseconds:60_000_000)
    }
    return ["dispatched":true,"verified":false,"requestedPlaying":desired,"after":after.json,
            "note":"Afspeelhandeling eenmaal verstuurd; geen automatische herhaling van de toggle."]
}

func openMusicFolder26(_ request: [String:Any] = [:]) async throws -> [String:Any] {
    var current = try await checkedObservation(recoverablePreDispatch:true)
    if current.text(in:CGRect(x:235,y:429,width:450,height:24)) == "26" {
        return ["dispatched":false,"verified":true,"after":current.json]
    }
    func folderRows(_ image: Observation) -> [TextToken] {
        image.tokens.filter {
            $0.text == "26" && $0.confidence > 0.8 &&
            $0.rect.minX >= 60 && $0.rect.maxX < 218 &&
            $0.rect.minY > 510 && $0.rect.maxY < 730
        }
    }
    var folders = folderRows(current)
    if folders.isEmpty {
        // Observed sidebar hierarchy: outer Music starts at x58, the nested
        // Music at x72. Clicking 30 px left of that nested label expands it.
        let parents = current.tokens.filter {
            $0.text == "Music" && $0.confidence > 0.8 &&
            $0.rect.minX >= 65 && $0.rect.maxX < 218 &&
            $0.rect.minY > 510 && $0.rect.maxY < 710
        }
        guard parents.count == 1, let parent = parents.first else {
            throw BridgeError("De waargenomen Music-mapstructuur is niet uniek; niets geselecteerd.")
        }
        try await pointer(CGPoint(x:parent.rect.minX-30,y:parent.rect.midY),observation:current,
                          preDispatch:{try requireLiveControlRequest(request); try requireFreshTransport(current)})
        for attempt in 0..<4 {
            if attempt > 0 { try await Task.sleep(nanoseconds:80_000_000) }
            current = try await checkedObservation()
            folders = folderRows(current)
            if !folders.isEmpty { break }
        }
    }
    guard folders.count == 1, let folder = folders.first else {
        throw BridgeError("Map 26 is niet uniek zichtbaar onder Music; geen gokselectie uitgevoerd.")
    }
    try await pointer(CGPoint(x:folder.rect.midX,y:folder.rect.midY),observation:current,
                      preDispatch:{try requireLiveControlRequest(request); try requireFreshTransport(current)})
    var after = try await observe()
    for attempt in 0..<4 {
        if attempt > 0 { after = try await observe() }
        if after.calibrated && after.text(in:CGRect(x:235,y:429,width:450,height:24)) == "26" {
            return ["dispatched":true,"verified":true,"after":after.json]
        }
        try await Task.sleep(nanoseconds:80_000_000)
    }
    return ["dispatched":true,"verified":false,"after":after.json,
            "note":"Mapselectie eenmaal verstuurd; kop 26 is nog niet bevestigd."]
}

// Startup-only library refresh through Rekordbox's normal menus and save panel.
// Fixed export directory and fixed import folder; no recording/preferences UI.
func refreshLibraryExport(_ request: [String:Any]) async throws -> [String:Any] {
    try requireInputAccess()
    let rb = try app()
    let ax = AXUIElementCreateApplication(rb.processIdentifier)
    let directory = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("evidence/library",isDirectory:true)
    try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true)
    let target = directory.appendingPathComponent("rekordbox-"+UUID().uuidString+".xml")
    func attribute(_ node: AXUIElement, _ name: String) -> CFTypeRef? {
        var result: CFTypeRef?
        guard AXUIElementCopyAttributeValue(node,name as CFString,&result) == .success else { return nil }
        return result
    }
    func find(_ root: AXUIElement, _ predicate: (AXUIElement) -> Bool) -> AXUIElement? {
        var queue: [(AXUIElement,Int)] = [(root,0)]
        var index = 0
        while index < queue.count && index < 2000 {
            let (node,depth) = queue[index]; index += 1
            if predicate(node) { return node }
            if depth < 10, let children = attribute(node,kAXChildrenAttribute) as? [AXUIElement] {
                queue.append(contentsOf:children.map { ($0,depth+1) })
            }
        }
        return nil
    }
    func live() throws {
        try requireLiveControlRequest(request)
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == rb.processIdentifier else {
            throw BridgeError("Bibliotheek bijwerken onderbroken: Rekordbox staat niet meer vooraan.")
        }
    }
    func waitFor(_ predicate: @escaping (AXUIElement) -> Bool) async throws -> AXUIElement {
        for _ in 0..<80 {
            try live()
            if let node = find(ax,predicate) { return node }
            try await Task.sleep(nanoseconds:100_000_000)
        }
        throw BridgeError("Bibliotheek bijwerken: verwacht Rekordbox-venster niet gevonden.")
    }
    func byID(_ id: String) async throws -> AXUIElement {
        try await waitFor { attribute($0,kAXIdentifierAttribute) as? String == id }
    }
    func press(_ node: AXUIElement) throws {
        try live()
        guard attribute(node,kAXEnabledAttribute) as? Bool != false else { throw BridgeError("Bibliotheekknop is nog niet beschikbaar.") }
        markNativeInputSent()
        guard AXUIElementPerformAction(node,kAXPressAction as CFString) == .success else {
            throw BridgeError("Rekordbox bevestigde de bibliotheekknop niet.")
        }
    }
    func set(_ node: AXUIElement, _ value: String) throws {
        try live(); markNativeInputSent()
        guard AXUIElementSetAttributeValue(node,kAXValueAttribute as CFString,value as CFString) == .success else {
            throw BridgeError("Bibliotheeklocatie kon niet worden ingevuld.")
        }
    }
    func menu(_ titles: Set<String>) async throws {
        let node = try await waitFor { titles.contains(attribute($0,kAXTitleAttribute) as? String ?? "") }
        try press(node)
    }
    var exported = false
    defer {
        // Only dismiss the save panel created by this invocation, never a
        // recording, user export, or unrelated dialog.
        if !exported, (try? live()) != nil,
           let panel = find(ax,{ attribute($0,kAXIdentifierAttribute) as? String == "save-panel" }),
           let name = find(panel,{ attribute($0,kAXIdentifierAttribute) as? String == "saveAsNameTextField" }),
           attribute(name,kAXValueAttribute) as? String == target.lastPathComponent,
           let cancel = find(panel,{ attribute($0,kAXIdentifierAttribute) as? String == "CancelButton" }) {
            try? press(cancel)
        }
    }
    func panelKey(_ description: String) async throws {
        try live()
        let key = try prepareKeyEvent(description)
        // AppKit's save-panel shortcuts require a normal foreground key event;
        // Rekordbox's deck mappings also accept postToPid, these panels do not.
        markNativeInputSent()
        key.down.post(tap:.cghidEventTap)
        defer { key.up.post(tap:.cghidEventTap) }
        try await Task.sleep(nanoseconds:30_000_000)
    }
    func goTo(_ path: String) async throws {
        try await panelKey("command + shift + g")
        let field = try await byID("PathTextField")
        try set(field,path)
        try await panelKey("return")
        for _ in 0..<80 {
            try live()
            if find(ax,{ attribute($0,kAXIdentifierAttribute) as? String == "PathTextField" }) == nil { return }
            try await Task.sleep(nanoseconds:100_000_000)
        }
        throw BridgeError("De exportmap is niet bevestigd.")
    }
    if request["importFolder26"] as? Bool == true {
        try await menu(["Bestand","File"])
        try await menu(["Importeren","Import"])
        try await menu(["Map importeren","Import Folder"])
        _ = try await byID("open-panel")
        try await goTo(allowedMusic.path)
        try press(try await byID("OKButton"))
        for _ in 0..<100 {
            try live()
            if (try? requireInputAccess()) != nil { break }
            try await Task.sleep(nanoseconds:100_000_000)
        }
        try requireInputAccess()
    }
    try await menu(["Bestand","File"])
    try await menu(["Verzameling exporteren in XML-indeling","Export Collection in xml format","Export Collection in XML format"])
    let filename = try await byID("saveAsNameTextField")
    try set(filename,target.lastPathComponent)
    try await goTo(directory.path)
    try press(try await byID("OKButton"))
    var previousSize: UInt64 = 0
    for _ in 0..<450 {
        try live()
        let size = (try? FileManager.default.attributesOfItem(atPath:target.path)[.size] as? NSNumber)?.uint64Value ?? 0
        if size > 0 && size == previousSize && (try? requireInputAccess()) != nil {
            exported = true
            return ["dispatched":true,"verified":true,"path":target.path,"bytes":size]
        }
        previousSize = size
        try await Task.sleep(nanoseconds:100_000_000)
    }
    throw BridgeError("De bibliotheekexport is niet tijdig voltooid; geen set gestart.")
}
