import AppKit
import Vision

// A deliberately small first effect: the visibly selected Echo in slot 2,
// low wet amount, two beats, then verified OFF. No inferred effect names.
func fxBlueCount(_ image: CGImage, _ rect: CGRect) -> Int {
    guard image.width == 1272, image.height == 768 else { return 0 }
    let bitmap = NSBitmapImageRep(cgImage:image)
    var count = 0
    for y in Int(rect.minY)..<Int(rect.maxY) { for x in Int(rect.minX)..<Int(rect.maxX) {
        guard let c = bitmap.colorAt(x:x,y:y)?.usingColorSpace(.deviceRGB) else { continue }
        if c.blueComponent > 0.4 && c.greenComponent > 0.2 && c.redComponent < 0.2 { count += 1 }
    }}
    return count
}
func fxText(_ frame: Observation, _ rect: CGRect) throws -> String {
    guard let crop = frame.image.cropping(to:rect) else { throw BridgeError("FX-tekst niet leesbaar.") }
    let request = VNRecognizeTextRequest(); request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage:crop,orientation:.up).perform([request])
    return (request.results ?? []).compactMap{$0.topCandidates(1).first?.string}.joined(separator:" ").uppercased()
}
func fxPointerPosition(_ image: CGImage, x:Int, y:Int) -> Double? {
    let bitmap = NSBitmapImageRep(cgImage:image)
    var points:[(Double,Double)] = []
    for px in (x-7)...(x+7) { for py in (y-8)...(y+7) {
        let dx=Double(px-x),dy=Double(py-y),radius=hypot(dx,dy)
        guard radius >= 3, radius <= 8,
              let c=bitmap.colorAt(x:px,y:py)?.usingColorSpace(.deviceRGB),
              min(c.redComponent,c.greenComponent,c.blueComponent)>0.65 else { continue }
        points.append((dx,dy))
    }}
    guard points.count>=2 else { return nil }
    let dx=points.map{$0.0}.reduce(0,+)/Double(points.count)
    let dy=points.map{$0.1}.reduce(0,+)/Double(points.count), radius=hypot(dx,dy)
    guard radius>=3,points.allSatisfy({($0.0*dx+$0.1*dy)/(hypot($0.0,$0.1)*radius)>=cos(.pi/6)}) else { return nil }
    return min(1,max(-1,atan2(dx,-dy)/(135 * .pi/180)))
}
func subtleEchoAllowed(deck:Int, bpm:Double, remaining:Double, playing:Bool, elapsedSinceLast:Double) -> Bool {
    [1,2].contains(deck) && bpm.isFinite && (60...200).contains(bpm) && remaining.isFinite &&
    remaining>20 && playing && elapsedSinceLast>=45
}
private var lastEchoNS: UInt64 = 0

func performEchoAccent(_ request: [String:Any]) async throws -> [String:Any] {
    guard let deck=request["deck"] as? Int, [1,2].contains(deck),
          let bpm=request["bpm"] as? Double, bpm.isFinite, (60...200).contains(bpm) else {
        throw BridgeError("Echo vereist een bevestigd deck en tempo.")
    }
    var frame=try await checkedObservation(recoverablePreDispatch:true)
    try validateMixDispatch(request,observation:frame)
    let elapsed=Double(DispatchTime.now().uptimeNanoseconds-lastEchoNS)/1e9
    let metadata=frame.text(in:CGRect(x:deck==1 ? 45 : 731,y:193,width:435,height:20))
    let regex=try NSRegularExpression(pattern:#"[-−]([0-9]{2,3}):([0-5][0-9])[.,]([0-9])"#)
    let matches=regex.matches(in:metadata,range:NSRange(metadata.startIndex...,in:metadata))
    var remaining=0.0
    if matches.count==1 {
        let values=(1...3).compactMap { index -> Double? in
            guard let range=Range(matches[0].range(at:index),in:metadata) else { return nil }
            return Double(metadata[range])
        }
        if values.count==3 { remaining=values[0]*60+values[1]+values[2]/10 }
    }
    guard subtleEchoAllowed(deck:deck,bpm:bpm,remaining:remaining,playing:frame.playing(deck:deck)==true,elapsedSinceLast:elapsed) else { throw BridgeError("Echo wacht op een spelend deck en rust tussen accenten.") }
    let offset=deck==1 ? 0 : 636
    let button=CGPoint(x:290+offset,y:61),knob=CGPoint(x:270+offset,y:81)
    let border=CGRect(x:257+offset,y:53,width:68,height:16)
    func active(_ f:Observation)->Bool { fxBlueCount(f.image,border)>30 }
    func fxFrame(matches: (Observation)->Bool = {_ in true}) async throws -> Observation {
        for _ in 0..<8 {
            let f=try await observe()
            if f.baseLayoutRecognized && f.fxPanelVisible && matches(f) { return f }
            try await Task.sleep(nanoseconds:60_000_000)
        }
        throw BridgeError("FX-teruglezing niet bevestigd na wachten op schermverversing.")
    }
    func normalFrame() async throws -> Observation {
        for _ in 0..<8 {
            let f=try await observe()
            if f.calibrated { return f }
            try await Task.sleep(nanoseconds:60_000_000)
        }
        throw BridgeError("Mixerindeling na Echo niet hersteld.")
    }
    // The socket server handles one command at a time. Stop/quit waits behind
    // this bounded gesture. Cleanup deliberately ignores client cancellation;
    // it only turns OUR effect off and restores the original panel visibility.
    var enabledByUs=false,openedByUs=false
    func cleanup() async throws {
        var f=try await observe()
        guard f.baseLayoutRecognized else { throw BridgeError("FX-herstel vereist dezelfde Rekordbox-indeling.") }
        if enabledByUs {
            guard f.fxPanelVisible else { throw BridgeError("FX-paneel verdwenen; Echo-uit niet bevestigd.") }
            if active(f) {
                try await pointer(button,observation:f,allowFXPanel:true)
                f=try await fxFrame(matches:{ !active($0) })
            }
            guard !active(f) else { throw BridgeError("Echo bleef actief; schakel Echo uit in Rekordbox.") }
            enabledByUs=false
        }
        if openedByUs && f.fxPanelVisible {
            try await pointer(CGPoint(x:314,y:37),observation:f,allowFXPanel:true)
            f=try await normalFrame()
            openedByUs=false
        }
    }
    do {
        try await pointer(CGPoint(x:314,y:37),observation:frame,
            preDispatch:{try requireLiveControlRequest(request)})
        openedByUs=true;frame=try await fxFrame()
        guard try fxText(frame,CGRect(x:257+offset,y:53,width:68,height:16)).contains("ECHO") else {
            throw BridgeError("Slot 2 is geen bevestigd Echo-effect.")
        }
        // Known multi-FX layout, unit assigned only to this deck. Never change
        // the user's routing or combine this accent with other active effects.
        let assignmentX=deck==1 ? 65 : 719
        guard fxBlueCount(frame.image,CGRect(x:assignmentX,y:58,width:16,height:16))>80 else {
            throw BridgeError("FX-decktoewijzing niet bevestigd.")
        }
        for row in [58,75] { for x in [65,83,101] {
            if !(row==58 && x+offset==assignmentX) {
                guard fxBlueCount(frame.image,CGRect(x:x+offset,y:row,width:16,height:16))<40 else {
                    throw BridgeError("FX heeft extra decktoewijzingen; geen Echo.")
                }
            }
        }}
        for unit in [0,636] {
            for x in [161,257,352,454] {
                guard fxBlueCount(frame.image,CGRect(x:x+unit,y:53,width:68,height:16))<30 else {
                    throw BridgeError("Er staat al een effect aan; geen extra Echo.")
                }
            }
        }
        // Reduce amount before activation. Signed angle -.82...-.55 is a low
        // visual setting (roughly the first fifth of travel), NOT measured wet dB.
        var amount:Double?
        for _ in 0..<14 {
            amount=fxPointerPosition(frame.image,x:Int(knob.x),y:Int(knob.y))
            guard let value=amount else { throw BridgeError("Echo-hoeveelheid niet leesbaar.") }
            if (-0.82 ... -0.55).contains(value) { break }
            try await pointer(knob,observation:frame,
                dragTo:CGPoint(x:knob.x,y:knob.y+(value > -0.55 ? 4 : -2)),
                preDispatch:{try requireLiveControlRequest(request)},allowFXPanel:true)
            frame=try await fxFrame(matches:{ f in
                guard let next=fxPointerPosition(f.image,x:Int(knob.x),y:Int(knob.y)) else { return false }
                return value > -0.55 ? next < value-0.01 : next > value+0.01
            })
        }
        guard let amount,(-0.82 ... -0.55).contains(amount) else { throw BridgeError("Lage Echo-hoeveelheid niet bevestigd.") }
        // Exact musical quality remains a listening judgement; only the control
        // position, activation and return to OFF are verified here.
        try requireLiveControlRequest(request)
        enabledByUs=true
        try await pointer(button,observation:frame,
            preDispatch:{try requireLiveControlRequest(request)},allowFXPanel:true)
        frame=try await fxFrame(matches:active)
        guard active(frame) else { throw BridgeError("Echo-aan niet bevestigd.") }
        lastEchoNS=DispatchTime.now().uptimeNanoseconds
        try await Task.sleep(nanoseconds:UInt64(2*60/bpm*1e9))
        try await cleanup()
        let after=try await observe()
        return ["verified":true,"dispatched":true,"effect":"echo","deck":deck,"beats":2,
                "amountPointer":amount,"effectOffVerified":true,"after":after.json]
    } catch {
        let original=error
        do { try await cleanup() }
        catch { throw BridgeError("\(original) Herstelprobleem: \(error)") }
        throw original
    }
}
