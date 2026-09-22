import AppKit

@main struct NativeTransportTests {
    static var checks = 0
    static func check(_ result: Bool, _ message: String) {
        precondition(result,message); checks += 1
    }
    static func allowed(_ deck: Int = 1, target: Bool? = false, other: Bool? = true,
                        targetFader: Double? = 1, otherFader: Double? = 1,
                        cross: Double? = 1, assignments: Bool = true) -> Bool {
        replacementLoadAllowed(deck:deck,targetPlaying:target,otherPlaying:other,
            targetFader:targetFader,otherFader:otherFader,crossfader:cross,normalAssignments:assignments)
    }
    static func validRequest() -> [String:Any] {
        ["durationSeconds":4.0,"crossfader":0.5,"clientPID":Int32(999),
         "notAfterMonotonicNS":UInt64(1000),"expectedTracks":["1":"Track A","2":"Track B"]]
    }
    static func rejects(_ request: [String:Any]) -> Bool {
        do { _ = try MixGestureSpec(request); return false } catch { return true }
    }
    static func main() async throws {
        let longTitle = "Could Heaven Ever Be Like This (Walker & Royce and Chris Lorenzo Remix) (Mixed)"
        let shownPrefix = "Could Heaven Ever Be Like This (Walker & Royce and"
        let longFile = "Idris Muhammad - \(longTitle).mp3"
        let longCandidate = FolderTrackIdentity(file:longFile,title:longTitle)
        let longIdentity = ChosenTrackIdentity(file:longFile,candidates:[longCandidate,
            FolderTrackIdentity(file:"Idris Muhammad - Could Heaven Ever Be Like This.mp3",title:"Could Heaven Ever Be Like This"),
            FolderTrackIdentity(file:"Incognito - Could Heaven Ever Be Like This.mp3",title:"Could Heaven Ever Be Like This")])
        check(longIdentity.match(longTitle) == "exact","Full observed titles retain exact identity matching")
        check(longIdentity.match(shownPrefix) == "unique_display_prefix","Saved 50-character visible prefix identifies the chosen remix uniquely")
        check(longIdentity.match(shownPrefix+"…") == "unique_display_prefix" && longIdentity.match(shownPrefix+"... ") == "unique_display_prefix",
              "Only explicit trailing UI ellipses may be removed from a displayed prefix")
        let loadedDeckPrefix = "Could Heaven Ever Be Like This (Walker & Royce and Chris L.."
        check(longIdentity.match(loadedDeckPrefix) == "unique_display_prefix",
              "Actual loaded-deck title ending in two UI dots identifies the chosen remix")
        let literalDots = ChosenTrackIdentity(file:"dots.mp3",candidates:[longCandidate,
            FolderTrackIdentity(file:"dots.mp3",title:loadedDeckPrefix)])
        check(literalDots.match(loadedDeckPrefix) == "exact",
              "An exact literal title ending in two dots takes precedence over truncation handling")
        let ambiguousLoaded = ChosenTrackIdentity(file:longFile,candidates:[longCandidate,
            FolderTrackIdentity(file:"another.mp3",title:"Could Heaven Ever Be Like This (Walker & Royce and Chris Lovely Remix)")])
        check(ambiguousLoaded.match(loadedDeckPrefix) == nil,
              "Two-dot truncation still rejects a shared remix prefix")
        check(longIdentity.match("Could Heaven..") == nil && longIdentity.match(shownPrefix+".") == nil,
              "Two dots do not waive minimum prefix length or permit guessing one-dot punctuation")
        check(longIdentity.match("Could Heaven Ever Be Like This") == nil,"The shorter shared original title cannot identify the remix")
        check(longIdentity.match("Could Heaven Ever Be Like Th") == nil,"A short partial title cannot establish identity")
        check(longIdentity.match(shownPrefix.replacingOccurrences(of:"Heaven",with:"Heavens")) == nil,
              "OCR spelling differences are not fuzzy-matched")
        check(longIdentity.match(shownPrefix+" (Remix)") == nil,"A guessed remix suffix cannot establish identity")
        let ambiguousRemix = ChosenTrackIdentity(file:longFile,candidates:longIdentity.candidates+[
            FolderTrackIdentity(file:"different-remix.mp3",title:shownPrefix+" A Different Remix)")])
        check(ambiguousRemix.match(shownPrefix) == nil,"Two remixes sharing the visible prefix must fail closed")
        let aliasCollision = ChosenTrackIdentity(file:longFile,candidates:longIdentity.candidates+[
            FolderTrackIdentity(file:shownPrefix+" Other File.mp3",title:"Unrelated tag")])
        check(aliasCollision.match(shownPrefix) == nil,"A file-stem collision also makes a displayed prefix ambiguous")
        let wrongFile = ChosenTrackIdentity(file:"different.mp3",candidates:longIdentity.candidates)
        check(wrongFile.match(shownPrefix) == nil,"A uniquely visible title must still identify the explicitly selected file")
        let shortIdentity = ChosenTrackIdentity(file:"Vaal.mp3",candidates:[FolderTrackIdentity(file:"Vaal.mp3",title:"Vaal")])
        check(shortIdentity.match("Vaal") == "exact","An exact short title is allowed and its identical filename alias does not count twice")
        let duplicateExact = ChosenTrackIdentity(file:longFile,candidates:[longCandidate,FolderTrackIdentity(file:"duplicate.mp3",title:longTitle)])
        check(duplicateExact.match(longTitle) == nil,"An ambiguous exact name cannot fall through to prefix matching")
        check(trackIdentityText("  CAFÉ\tStraße ") == "café strasse","Identity normalization folds case and whitespace while preserving diacritics")
        check(trackIdentityText("Cafe\u{301}") == trackIdentityText("Café"),"Canonical Unicode equivalents identify the same literal title")
        check(trackIdentityText("Cafe") != trackIdentityText("Café"),"Accents are not guessed or stripped")
        let clippedToken = TextToken(text:shownPrefix,confidence:1,rect:CGRect(x:552,y:563.5,width:324,height:16))
        let matchedToken = uniqueChosenBrowserToken([clippedToken],title:longTitle,identity:longIdentity)
        check(matchedToken?.text == shownPrefix && matchedToken?.rect == clippedToken.rect,
              "Identity resolution preserves the raw observed title and row geometry")
        check(uniqueChosenBrowserToken([clippedToken,clippedToken],title:longTitle,identity:longIdentity) == nil,
              "Two visible matching rows cannot establish one selection coordinate")
        if let i = CommandLine.arguments.firstIndex(of:"--long-title-fixture"), i+2 < CommandLine.arguments.count {
            let image = NSBitmapImageRep(data:try Data(contentsOf:URL(fileURLWithPath:CommandLine.arguments[i+1])))!.cgImage!
            let inventory = try JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:CommandLine.arguments[i+2]))) as! [String:Any]
            let tracks = inventory["tracks"] as! [[String:Any]]
            let actualIdentity = ChosenTrackIdentity(file:longFile,candidates:tracks.map {
                FolderTrackIdentity(file:$0["file"] as! String,title:$0["title"] as? String)
            })
            let tokens = try recognizeObservationTokens(image,mixerOnly:false).filter {
                $0.rect.minY > 460 && $0.rect.maxY < 739 && $0.rect.minX >= 540 && $0.rect.maxX < 891
            }
            let chosen = uniqueChosenBrowserToken(tokens,title:longTitle,identity:actualIdentity)
            check(tracks.count == 127 && chosen?.text == shownPrefix,"Real screenshot and all127 inventory entries uniquely resolve the clipped remix row")
            let rowTokens = try recognizeTextRegion(image,loadBrowserRowRegion(chosen!.rect))
            let freshlyChosen = uniqueChosenBrowserToken(rowTokens,title:longTitle,identity:actualIdentity)
            print("Long-title fresh row fixture OCR: \(rowTokens.map(\.text))")
            check(freshlyChosen != nil,"Fresh narrow row OCR also identifies the chosen file without requiring hidden suffix text")
            let originals = tokens.filter{$0.text == "Could Heaven Ever Be Like This"}
            check(originals.count == 4 && uniqueChosenBrowserToken(originals,title:longTitle,identity:actualIdentity) == nil,
                  "The four actual neighboring original-title rows cannot be mistaken for the selected remix")
            let actualAmbiguous = ChosenTrackIdentity(file:longFile,candidates:actualIdentity.candidates+[
                FolderTrackIdentity(file:"other-remix.mp3",title:shownPrefix+" Another Remix)")])
            check(uniqueChosenBrowserToken(tokens,title:longTitle,identity:actualAmbiguous) == nil,
                  "The saved row cannot authorize loading if the full library has another matching remix")
        }
        var clock: UInt64 = 1_000_000_000, launchPosts = 0, launchPreparations = 0
        let onTime = try await schedulePreparedLaunch(dueNS:2_000_000_000,now:{clock},
            wait:{clock += $0},prepare:{launchPreparations += 1; clock += 350_000_000},
            post:{launchPosts += 1; return clock})
        check(onTime == 2_000_000_000 && launchPreparations == 1 && launchPosts == 1,
              "350 ms capture/access work is completed ahead of the beat, with exactly one on-time post")
        for lateness: UInt64 in [0,79_999_999] {
            try requireLaunchTiming(dueNS:2_000_000_000,checkedNS:2_000_000_000+lateness,phase:"fixture")
            check(true,"Existing sub-80 ms dispatch allowance remains valid")
        }
        for checked: UInt64 in [1_999_999_999,2_080_000_000,2_400_000_000] {
            do {
                try requireLaunchTiming(dueNS:2_000_000_000,checkedNS:checked,phase:"fixture")
                check(false,"An early or >=80 ms late Play must be rejected")
            } catch is LaunchTimingMiss { check(true,"No early or late Play is authorized") }
        }
        clock = 1_000_000_000; launchPosts = 0; launchPreparations = 0
        var plans = 0, misses: [LaunchTimingMiss] = []
        let replanned = try await retryUndispatchedLaunch(eventSequence:{launchPosts},onMiss:{_,miss in misses.append(miss)}) { _ in
            plans += 1
            let due = clock+1_000_000_000
            return try await schedulePreparedLaunch(dueNS:due,now:{clock},wait:{clock += $0},prepare:{
                launchPreparations += 1
                clock += plans == 1 ? 720_000_000 : 150_000_000
            },post:{launchPosts += 1; return clock})
        }
        check(plans == 2 && launchPreparations == 2 && launchPosts == 1 && replanned == 3_120_000_000,
              "A slow first preflight recaptures/replans the next bar and posts only once")
        check(misses.count == 1 && misses[0].latenessMS == 120 && misses[0].phase == "wake",
              "A missed wake retains the actual 120 ms scheduling evidence")
        clock = 1_000_000_000; launchPosts = 0; plans = 0
        _ = try await retryUndispatchedLaunch(eventSequence:{launchPosts}) { _ in
            plans += 1
            let due = clock+1_000_000_000
            return try await schedulePreparedLaunch(dueNS:due,now:{clock},wait:{clock += $0},prepare:{},post:{
                // Simulate the formerly unmeasured work inside sendKey.
                if plans == 1 { clock += 100_000_000 }
                try requireLaunchTiming(dueNS:due,checkedNS:clock,phase:"immediate_post")
                launchPosts += 1
                return clock
            })
        }
        check(plans == 2 && launchPosts == 1,"Delay after waking is checked at the actual post boundary before any Play")
        plans = 0; launchPosts = 0
        do {
            let _: Bool = try await retryUndispatchedLaunch(eventSequence:{launchPosts}) { _ in
                plans += 1
                if plans == 1 { throw LaunchTimingMiss(dueNS:1,checkedNS:90_000_001,phase:"fixture") }
                throw BridgeError("fixture changed title or incoming already playing")
            }
            check(false,"A changed launch state must not be ignored")
        } catch { check(plans == 2 && launchPosts == 0,"Changed state on recapture stops the old intent without input") }
        plans = 0; launchPosts = 0
        do {
            let _: Bool = try await retryUndispatchedLaunch(eventSequence:{launchPosts}) { _ in
                plans += 1; launchPosts += 1
                throw LaunchTimingMiss(dueNS:1,checkedNS:90_000_001,phase:"fixture post-input failure")
            }
            check(false,"A sent Play must never be replayed")
        } catch { check(plans == 1 && launchPosts == 1 && !(error is LaunchTimingMiss),"Post-input failure cannot re-enter launch scheduling") }
        plans = 0; launchPosts = 0
        do {
            let _: Bool = try await retryUndispatchedLaunch(eventSequence:{launchPosts}) { _ in
                plans += 1
                throw LaunchTimingMiss(dueNS:1,checkedNS:90_000_001,phase:"fixture")
            }
            check(false,"Repeated local deadline misses must be bounded")
        } catch is LaunchTimingMiss { check(plans == 4 && launchPosts == 0,"Four failed fresh plans stop with no Play sent") }
        let markerDue = try nextLaunchDue(markers:[400,464,528,592,656,720,784,848],bpm:120,cueOffset:0.125,
            sampledAt:1_000_000_000,now:1_100_000_000)
        check(markerDue == 1_500_000_000,"Visual marker due time is anchored to capture time, including cue offset and observation age")
        let nextMarkerDue = try nextLaunchDue(markers:[400,464,528,592,656,720,784,848],bpm:120,cueOffset:0.125,
            sampledAt:1_000_000_000,now:1_450_000_000)
        check(nextMarkerDue == 3_500_000_000,"A marker already inside the 80 ms planning margin is skipped for the next visible bar")
        var inputSequence = 8, freshnessAttempts = 0
        let eventualLoad = try await retryUndispatchedLoadInput(eventSequence:{inputSequence}) { _ in
            freshnessAttempts += 1
            if freshnessAttempts == 1 { throw LoadObservationStale(ageMS:810) }
            inputSequence += 1
            return true
        }
        check(eventualLoad && freshnessAttempts == 2 && inputSequence == 9,
              "A stale row guard after prior scrolling recaptures before sending the next input exactly once")
        freshnessAttempts = 0
        do {
            let _: Bool = try await retryUndispatchedLoadInput(eventSequence:{inputSequence}) { _ in
                freshnessAttempts += 1; inputSequence += 1
                throw LoadObservationStale(ageMS:820)
            }
            check(false,"A post-input failure cannot be retried")
        } catch {
            check(!(error is LoadObservationStale) && freshnessAttempts == 1 && inputSequence == 10,
                  "A newly sent event prohibits retry even when its error is a stale observation")
        }
        freshnessAttempts = 0
        do {
            let _: Bool = try await retryUndispatchedLoadInput(eventSequence:{inputSequence}) { _ in
                freshnessAttempts += 1
                throw LoadObservationStale(ageMS:830)
            }
            check(false,"Persistently stale guard must stop")
        } catch is LoadObservationStale {
            check(freshnessAttempts == 3 && inputSequence == 10,
                  "Stale-input reacquisition is bounded at three attempts without another event")
        }
        let browserPoint = CGPoint(x:962,y:681)
        let wheel = browserWheelEvent(pixels:100000,point:browserPoint)
        check(wheel?.location == browserPoint,"Scroll event must explicitly target the browser, irrespective of the last knob/cursor location")
        let wantedToken = TextToken(text:"Vaal",confidence:1,rect:CGRect(x:552,y:680,width:30,height:12))
        check(uniqueChosenBrowserToken([wantedToken],title:"Vaal") != nil,"Fresh exact row title proves identity")
        check(uniqueChosenBrowserToken([TextToken(text:"Vaal2",confidence:1,rect:wantedToken.rect)],title:"Vaal") == nil,
              "Fresh row OCR must not fuzzy-match a different title")
        check(uniqueChosenBrowserToken([wantedToken,wantedToken],title:"Vaal") == nil,"Ambiguous fresh row identity cannot authorize selection")
        if let i = CommandLine.arguments.firstIndex(of:"--browser-row-pair"), i+3 < CommandLine.arguments.count {
            let paths = [CommandLine.arguments[i+1],CommandLine.arguments[i+2]]
            let title = CommandLine.arguments[i+3]
            var rowImages: [CGImage] = [], rowRect: CGRect?
            for path in paths {
                let image = NSBitmapImageRep(data:try Data(contentsOf:URL(fileURLWithPath:path)))!.cgImage!
                let tokens = try recognizeObservationTokens(image,mixerOnly:false).filter {
                    $0.rect.minY > 460 && $0.rect.maxY < 739 && $0.rect.minX >= 540 && $0.rect.maxX < 891
                }
                let row = uniqueChosenBrowserToken(tokens,title:title)
                check(row != nil,"Saved browser must uniquely show the intended track")
                let region = loadBrowserRowRegion(row!.rect)
                let freshTokens = try recognizeTextRegion(image,region)
                print("Fresh row fixture OCR: \(freshTokens.map(\.text))")
                check(uniqueChosenBrowserToken(freshTokens,title:title) != nil,
                      "Both caret phases must freshly recognize the exact same intended title")
                let neighborTokens = try recognizeTextRegion(image,region.offsetBy(dx:0,dy:18))
                check(uniqueChosenBrowserToken(neighborTokens,title:title) == nil,
                      "The actual neighboring row must not satisfy the intended title check")
                check(browserRowSelected(image,row:row!.rect),"Both caret phases retain actual blue row selection")
                rowImages.append(image); rowRect = region
            }
            check(!observationRegionsUnchanged(rowImages[0],rowImages[1],regions:[rowRect!]),
                  "Saved regression pair must actually differ in pixels, exercising the original failure")
            check(observationRegionsUnchanged(rowImages[0],rowImages[1],regions:[loadBrowserHeadingRegion]),
                  "Regression pair retains the same exact folder heading")
        }
        var selectionClicks = 0, editingCancels = 0, inlineEditor = true
        try await establishBrowserSelection(alreadySelected:true,selectOnce:{
            selectionClicks += 1; inlineEditor = true
        },cancelEditingOnce:{editingCancels += 1; inlineEditor = false})
        check(selectionClicks == 0 && editingCancels == 1 && !inlineEditor,
              "Resuming on an already selected title cancels existing editing without clicking into the editor again")
        selectionClicks = 0; editingCancels = 0
        try await establishBrowserSelection(alreadySelected:false,selectOnce:{selectionClicks += 1},
            cancelEditingOnce:{editingCancels += 1})
        check(selectionClicks == 1 && editingCancels == 0,"A different intended row is selected once")
        check(try keyCode(for:"escape") == 53,"Editing cancellation uses the Escape physical key, not a typed character")
        for code in ["rekordbox_not_frontmost","focused_window_unreadable","rekordbox_dialog_open"] {
            do {
                let _: Bool = try await withNativeInputTracking {
                    throw inputAccessFailure(code:code,message:"fixture input access failure",retryable:true)
                }
                check(false,"An unavailable input surface must reject before event dispatch")
            } catch let error as PreDispatchRejection {
                check(error.code == code && error.retryable,"A zero-input transient access failure remains explicitly retryable")
            }
        }
        do {
            let _: Bool = try await withNativeInputTracking {
                throw inputAccessFailure(code:"accessibility_missing",message:"fixture missing permission",retryable:false)
            }
            check(false,"Missing permission must reject")
        } catch let error as PreDispatchRejection {
            check(!error.retryable,"Missing permission must not turn into an automatic retry loop")
        }
        let afterEvent = try await withNativeInputTracking {
            markNativeInputSent()
            await Task.yield()
            return inputAccessFailure(code:"rekordbox_not_frontmost",message:"fixture after event",retryable:true,explicitNoInput:true)
        }
        check(!(afterEvent is PreDispatchRejection),"Focus loss after an event must never report zero input, even with a caller's stale initial flag")
        do {
            let _: Bool = try await withNativeInputTracking {
                markNativeInputSent()
                await Task.yield()
                throw PreDispatchRejection(code:"observation_stale",description:"fixture after first gesture",retryable:true)
            }
            check(false,"Post-event stale observation must still reject")
        } catch {
            check(!(error is PreDispatchRejection),"Request wrapper must prevent any later guard from falsely declaring commandsSent:false")
        }
        let firstScope = Task { try await withNativeInputTracking {
            markNativeInputSent(); await Task.yield(); return mayReportNoInput()
        }}
        let separateScope = Task { try await withNativeInputTracking {
            await Task.yield(); return mayReportNoInput()
        }}
        let firstScopeResult = try await firstScope.value
        let separateScopeResult = try await separateScope.value
        check(!firstScopeResult && separateScopeResult,"Concurrent requests keep separate input-dispatch evidence")
        let readbackTitles = ["1":"Playing track","2":"Stopped track"]
        func quietFacts(_ cross: Double?, playing: [String:Bool] = ["1":false,"2":false],
                        titles: [String:String]? = nil, channel: Double? = 1) -> SilentDeckOpenFacts {
            SilentDeckOpenFacts(calibrated:true,titles:titles ?? readbackTitles,playing:playing,
                normalAssignments:true,selectedChannel:channel,crossfader:cross)
        }
        check(silentDeckOpenAllowed(playing:["1":false,"2":false],selectedChannel:1,crossfader:0,normalAssignments:true),
              "Both confirmed stopped decks permit opening the explicitly chosen silent route")
        for playing in [["1":true,"2":false],["1":false,"2":true],["1":true,"2":true],["1":false],["2":false],[:]] {
            check(!silentDeckOpenAllowed(playing:playing,selectedChannel:1,crossfader:0,normalAssignments:true),
                  "Either playing or unknown transport blocks silent route movement before input")
        }
        for channel: Double? in [nil,0.89,1.01,Double.nan] {
            check(!silentDeckOpenAllowed(playing:["1":false,"2":false],selectedChannel:channel,crossfader:0,normalAssignments:true),
                  "A missing or closed selected channel cannot establish an opening route")
        }
        check(!silentDeckOpenAllowed(playing:["1":false,"2":false],selectedChannel:1,crossfader:nil,normalAssignments:true),
              "Unknown crossfader prevents silent opening")
        check(!silentDeckOpenAllowed(playing:["1":false,"2":false],selectedChannel:1,crossfader:0,normalAssignments:false),
              "Changed routing cannot use the silent opening exception")
        var silentClicks = 0, silentCaptures = 0
        let quietRender = [quietFacts(0),quietFacts(0.5),quietFacts(0.990099)]
        let opened = try await dispatchThenReadback(dispatch:{silentClicks += 1},read:{
            let frame = quietRender[min(silentCaptures,quietRender.count-1)]; silentCaptures += 1
            return frame
        },assess:{assessSilentDeckOpen($0,endpoint:1,expectedTitles:readbackTitles)},
        now:{UInt64(silentCaptures)*100_000_000},pause:{_ in await Task.yield()})
        check(opened.assessment.confirmed && silentCaptures == 3 && silentClicks == 1,
              "Opening B waits through delayed crossfader rendering and never repeats the pointer")
        for frame in [quietFacts(1,playing:["1":true,"2":false]),quietFacts(1,playing:["1":false,"2":true]),
            quietFacts(1,titles:["1":"Changed track","2":"Stopped track"]),quietFacts(1,channel:0.5)] {
            silentCaptures = 0; silentClicks = 0
            let contradicted = try await dispatchThenReadback(dispatch:{silentClicks += 1},read:{
                silentCaptures += 1; return silentCaptures == 1 ? frame : quietFacts(1)
            },assess:{assessSilentDeckOpen($0,endpoint:1,expectedTitles:readbackTitles)},
            now:{UInt64(silentCaptures)*100_000_000},pause:{_ in await Task.yield()})
            check(!contradicted.assessment.confirmed && !contradicted.assessment.mayWaitForRender && silentCaptures == 1 && silentClicks == 1,
                  "A started deck, changed identity or changed channel is a contradiction, not render delay")
        }
        silentClicks = 0; silentCaptures = 0
        let notOpened = try await dispatchThenReadback(maxAttempts:3,dispatch:{silentClicks += 1},read:{
            silentCaptures += 1; return quietFacts(0)
        },assess:{assessSilentDeckOpen($0,endpoint:1,expectedTitles:readbackTitles)},
        now:{UInt64(silentCaptures)*100_000_000},pause:{_ in await Task.yield()})
        check(!notOpened.assessment.confirmed && notOpened.assessment.reasons == ["crossfader_not_at_endpoint"] && silentClicks == 1 && silentCaptures == 3,
              "A fader that never reaches the requested endpoint fails with evidence after bounded read-only checks")
        func facts(_ cross: Double, changedTitle: Bool = false, targetStarted: Bool = false) -> StoppedDeckCloseFacts {
            StoppedDeckCloseFacts(calibrated:true,
                titles:changedTitle ? ["1":"Different track","2":"Stopped track"] : readbackTitles,
                targetPlaying:targetStarted,otherPlaying:true,normalAssignments:true,
                playingChannel:1,crossfader:cross)
        }
        func assessment(_ state: StoppedDeckCloseFacts) -> ReadbackAssessment {
            assessStoppedDeckClose(state,endpoint:0,expectedTitles:readbackTitles)
        }
        var pointerCount = 0, captures = 0
        let renderingFrames = [facts(0.495),facts(0.25),facts(0)]
        let rendered = try await dispatchThenReadback(dispatch:{pointerCount += 1},read:{
            let frame = renderingFrames[min(captures,renderingFrames.count-1)]; captures += 1
            await Task.yield(); return frame
        },assess:{assessStoppedDeckClose($0,endpoint:0,expectedTitles:readbackTitles)},now:{UInt64(captures)*100_000_000},pause:{_ in await Task.yield()})
        check(rendered.assessment.confirmed && captures == 3,"Delayed-render fixture must wait for the actual endpoint")
        check(pointerCount == 1,"Delayed frames must never replay the pointer action")
        check(rendered.attempts == 3 && rendered.assessment.reasons.isEmpty,"Confirmed readback reports actual capture attempts")

        pointerCount = 0; captures = 0
        let stalled = try await dispatchThenReadback(maxAttempts:3,dispatch:{pointerCount += 1},read:{
            captures += 1; await Task.yield(); return facts(0.495)
        },assess:{assessStoppedDeckClose($0,endpoint:0,expectedTitles:readbackTitles)},now:{UInt64(captures)*100_000_000},pause:{_ in await Task.yield()})
        check(!stalled.assessment.confirmed && stalled.assessment.reasons == ["crossfader_not_at_endpoint"],"Unchanged UI must fail with an exact observable reason")
        check(captures == 3 && pointerCount == 1,"A render timeout must remain bounded and never repeat input")

        pointerCount = 0; captures = 0
        let contradicted = try await dispatchThenReadback(dispatch:{pointerCount += 1},read:{
            captures += 1; await Task.yield()
            return captures == 1 ? facts(0.495) : facts(0,changedTitle:true)
        },assess:{assessStoppedDeckClose($0,endpoint:0,expectedTitles:readbackTitles)},now:{UInt64(captures)*100_000_000},pause:{_ in await Task.yield()})
        check(!contradicted.assessment.confirmed && !contradicted.assessment.mayWaitForRender,
              "An endpoint with a changed track is not a successful close")
        check(captures == 2 && pointerCount == 1 && contradicted.assessment.reasons.contains("deck_1_title_changed"),
              "Contradicting title state must stop observation polling without more input")
        check(!assessment(facts(0,targetStarted:true)).mayWaitForRender,"A target that starts playing is a contradiction, not render lag")

        pointerCount = 0; captures = 0
        let failedCapture = try await dispatchThenReadback(dispatch:{pointerCount += 1},read:{
            captures += 1
            if captures == 2 { throw BridgeError("fixture capture failed") }
            return facts(0.495)
        },assess:{assessStoppedDeckClose($0,endpoint:0,expectedTitles:readbackTitles)},now:{UInt64(captures)*100_000_000},pause:{_ in await Task.yield()})
        check(!failedCapture.assessment.confirmed && failedCapture.attempts == 2 && pointerCount == 1,
              "Readback errors after dispatch must not replay input")
        check(failedCapture.assessment.reasons.contains(where:{$0.contains("observation_failed")}),"Readback failure must retain its diagnostic reason")
        func mixFacts(cross: Double? = 0.8, outgoing: Double? = -0.4, incoming: Double? = -0.2,
                      aligned: Bool? = true, playing: [String:Bool] = ["1":true,"2":true],
                      titles: [String:String]? = nil, calibrated: Bool = true) -> MixGestureReadbackFacts {
            MixGestureReadbackFacts(calibrated:calibrated,titles:titles ?? readbackTitles,playing:playing,
                aligned:aligned,crossfader:cross,outgoingBass:outgoing,incomingBass:incoming)
        }
        captures = 0
        let mixRenderFrames = [mixFacts(cross:0.6,outgoing:-0.1,incoming:-0.5),
            mixFacts(cross:nil,outgoing:nil,incoming:nil,aligned:nil),mixFacts()]
        let settledMix = try await pollReadback(read:{
            let frame = mixRenderFrames[min(captures,mixRenderFrames.count-1)]; captures += 1
            await Task.yield(); return frame
        },assess:{assessMixGesture($0,expectedTitles:readbackTitles,targetCross:0.8,startOut:-0.1,startIn:-0.5).readback},
        now:{UInt64(captures)*100_000_000},pause:{_ in await Task.yield()})
        check(settledMix.assessment.confirmed && settledMix.attempts == 3,
              "Completed mix waits through old and unreadable frames until cross and paired bass movement are visible")
        for contradictoryMix in [mixFacts(aligned:false),mixFacts(playing:["1":true,"2":false]),
            mixFacts(titles:["1":"Different track","2":"Stopped track"]),mixFacts(incoming:0.2)] {
            captures = 0
            let result = try await pollReadback(read:{
                captures += 1; await Task.yield(); return captures == 1 ? contradictoryMix : mixFacts()
            },assess:{assessMixGesture($0,expectedTitles:readbackTitles,targetCross:0.8,startOut:-0.1,startIn:-0.5).readback},
            now:{UInt64(captures)*100_000_000},pause:{_ in await Task.yield()})
            check(!result.assessment.confirmed && !result.assessment.mayWaitForRender && captures == 1,
                  "A known mix safety contradiction must not be waited out until a later good frame")
        }
        let unreadableMix = assessMixGesture(mixFacts(aligned:nil,playing:[:],titles:[:],calibrated:false),
            expectedTitles:readbackTitles,targetCross:0.8,startOut:-0.1,startIn:-0.5)
        check(!unreadableMix.readback.confirmed && unreadableMix.readback.mayWaitForRender,
              "An unreadable frame remains unverified and may receive bounded observation-only retries")
        let crossOnly = assessMixGesture(mixFacts(outgoing:nil,incoming:nil),expectedTitles:readbackTitles,
            targetCross:0.8,startOut:nil,startIn:nil)
        check(crossOnly.readback.confirmed,"A crossfader-only request does not require unrelated EQ readings")
        let bassOnly = assessMixGesture(mixFacts(cross:nil),expectedTitles:readbackTitles,
            targetCross:nil,startOut:-0.1,startIn:-0.5)
        check(bassOnly.readback.confirmed,"An EQ-only request does not require unrelated crossfader readback")
        captures = 0
        let unfinishedMix = try await pollReadback(maxAttempts:3,read:{
            captures += 1; return mixFacts(cross:0.6,outgoing:-0.1,incoming:-0.5)
        },assess:{assessMixGesture($0,expectedTitles:readbackTitles,targetCross:0.8,startOut:-0.1,startIn:-0.5).readback},
        now:{UInt64(captures)*100_000_000},pause:{_ in await Task.yield()})
        check(!unfinishedMix.assessment.confirmed && unfinishedMix.attempts == 3 &&
            unfinishedMix.assessment.reasons == ["crossfader_not_at_target","bass_direction_not_confirmed"],
            "Unrendered mix targets must fail boundedly with specific pending-target reasons")
        func staging(_ deck: Int, target: Bool? = false, other: Bool? = true,
                     channel: Double? = 1, cross: Double? = 0.5, assignments: Bool = true) -> Bool {
            stoppedDeckCloseAllowed(deck:deck,targetPlaying:target,otherPlaying:other,
                playingChannel:channel,crossfader:cross,normalAssignments:assignments)
        }
        check(staging(1) && staging(2),"Either stopped deck can be staged while the other plays")
        check(!staging(1,target:true) && !staging(2,target:true),"Staging must never bypass alignment for a playing target")
        check(!staging(1,target:nil),"Unknown target playback is not stopped")
        check(!staging(1,other:false) && !staging(1,other:nil),"Other deck must be confirmed playing")
        check(!staging(1,channel:0) && !staging(1,channel:nil),"Playing channel must be open and known")
        check(!staging(1,cross:nil) && !staging(1,cross:Double.nan),"Unknown crossfader cannot be staged")
        check(!staging(1,assignments:false),"Staging requires normal confirmed assignments")
        var closeRequest = validRequest(); closeRequest.removeValue(forKey:"crossfader"); closeRequest["deck"] = 1
        let closeA = try StoppedDeckCloseSpec(closeRequest)
        let openA = try SilentDeckOpenSpec(closeRequest)
        check(openA.deck == 1 && openA.endpoint == 0,"Silent opening A accepts only A's endpoint")
        check(closeA.playingDeck == 2 && closeA.endpoint == 1,"Closing A may only move to B endpoint")
        closeRequest["deck"] = 2
        let closeB = try StoppedDeckCloseSpec(closeRequest)
        let openB = try SilentDeckOpenSpec(closeRequest)
        check(openB.deck == 2 && openB.endpoint == 1,"Silent opening B accepts only B's endpoint")
        check(closeB.playingDeck == 1 && closeB.endpoint == 0,"Closing B may only move to A endpoint")
        for key in ["deck","clientPID","notAfterMonotonicNS","expectedTracks"] {
            var invalid = closeRequest; invalid.removeValue(forKey:key)
            do { _ = try StoppedDeckCloseSpec(invalid); check(false,"Staging must reject missing \(key)") }
            catch { check(true,"Staging required field rejected") }
            do { _ = try SilentDeckOpenSpec(invalid); check(false,"Silent opening must reject missing \(key)") }
            catch { check(true,"Silent opening required field rejected") }
        }
        closeRequest["value"] = 0.5
        do { _ = try StoppedDeckCloseSpec(closeRequest); check(false,"Staging cannot accept an arbitrary fader destination") }
        catch { check(true,"Arbitrary staging target rejected") }
        do { _ = try SilentDeckOpenSpec(closeRequest); check(false,"Silent opening cannot accept an arbitrary fader destination") }
        catch { check(true,"Arbitrary opening target rejected") }
        var emptyOpening = closeRequest; emptyOpening.removeValue(forKey:"value")
        emptyOpening["expectedTracks"] = ["1":"Track A","2":"Not Loaded."]
        do { _ = try SilentDeckOpenSpec(emptyOpening); check(false,"An empty chosen deck cannot be opened for playback") }
        catch { check(true,"Empty selected deck rejected") }
        check(observationFresh(sampledAt:1_000_000_000,now:1_750_000_000),"750 ms freshness boundary remains inclusive")
        check(!observationFresh(sampledAt:1_000_000_000,now:1_750_000_001),"No observation older than 750 ms becomes valid")
        check(!observationFresh(sampledAt:1_000_000_001,now:1_000_000_000),"Future timestamps cannot count as fresh")
        check(endOnlyStopAllowed(desired:false,metadata:"123.00 Dm -00:00.0 05:07.3"),"Explicit negative zero remaining permits end-only stop")
        check(endOnlyStopAllowed(desired:false,metadata:"123.00 Dm −00:00,1 05:07.2"),"One tenth remaining and Unicode minus are accepted")
        for metadata in ["123.00 Dm -00:00.2 05:07.1","123.00 Dm -01:20.0 03:47.3",
                         "123.00 Dm 00:00.0 05:07.3","123.00 Dm","-00:00.0 -00:01.0",
                         "-00:00.00","-00:00"] {
            check(!endOnlyStopAllowed(desired:false,metadata:metadata),"Running, unsigned, missing or ambiguous remaining clock must not be treated as ended")
        }
        check(!endOnlyStopAllowed(desired:true,metadata:"-00:00.0"),"endOnly cannot start playback")
        do {
            _ = try await setDesiredPlayback(["deck":1,"playing":true,"expectedTrack":"Attract","endOnly":true])
            check(false,"endOnly start must reject before mapping/UI access")
        } catch { check(String(describing:error).contains("endOnly"),"Invalid endOnly request must fail before any input") }
        let host = try NativeProcessRole(arguments:[])
        let worker = try NativeProcessRole(arguments:["--control-worker"])
        let control = try NativeProcessRole(arguments:["--demo-control"])
        let observer = try NativeProcessRole(arguments:["--demo-observer"])
        check(host.socketFile == "control.sock" && host.lockFile == "server.lock","Existing credential host paths must remain unchanged")
        check(worker.socketFile == "control-worker.sock" && worker.lockFile == "control-worker.lock","Existing physical worker paths remain isolated")
        check(control.socketFile == "demo-control.sock" && control.lockFile == "demo-control.lock","Demo control has independent socket and lock")
        check(observer.socketFile == "demo-observer.sock" && observer.lockFile == "demo-observer.lock","Demo observation must not queue behind control/loading")
        check(control.demoName == "control" && observer.demoName == "observer","Demo roles have explicit status names")
        do {
            _ = try NativeProcessRole(arguments:["--demo-control","--demo-observer"])
            check(false,"Conflicting roles must not silently select credential host")
        } catch { check(true,"Conflicting roles rejected") }
        for role in [worker,control,observer] { for command in ["djReady","djAuthorize","djStart","djStop","djAnything"] {
            do { try role.requireCommand(command); check(false,"Physical roles must deny all credential/session commands") }
            catch { check(String(describing:error).contains("geen sleuteltoegang"),"Credential denial must happen first") }
        }}
        for command in ["status","observe","quit"] { try observer.requireCommand(command) }
        for command in ["activate","fader","eq","setPlayback","loadChosenTrack","mixGesture","closeStoppedDeck","openSilentDeck","action","browse","capabilities",""] {
            do { try observer.requireCommand(command); check(false,"Observer must deny \(command)") }
            catch { check(String(describing:error).contains("alleen-lezen"),"Observer command denial must be explicit") }
        }
        try control.requireCommand("activate"); try control.requireCommand("loadChosenTrack"); try control.requireCommand("closeStoppedDeck")
        try control.requireCommand("openSilentDeck")
        func silent(_ target: Bool?, _ other: Bool?) -> Bool {
            replacementLoadAllowed(deck:1,targetPlaying:target,otherPlaying:other,
                targetFader:1,otherFader:1,crossfader:0.5,normalAssignments:true,allowSilentReplacement:true)
        }
        check(silent(false,false),"Explicitly silent replacement permits both stopped decks at open routes")
        check(!silent(true,false),"Silent mode must never overwrite a playing target")
        check(!silent(false,true),"Silent mode must stop if the other deck starts")
        check(!silent(nil,false),"Unknown target playback is not silence")
        check(!silent(false,nil),"Unknown other playback is not silence")
        var pagination = BrowserScanProgress()
        check(pagination.observe(titles:["Alpha","Beta"]),"First browser page is observed")
        for _ in 0..<3 { _ = pagination.observe(titles:["Alpha","Beta"]) }
        check(!pagination.stalled && pagination.scrolls == 0,"Repeated renders without scrolling are not a browser end")
        pagination.finishScroll(changed:false)
        check(!pagination.stalled,"A dropped scroll must not terminate lookup")
        pagination.finishScroll(changed:false)
        check(!pagination.stalled,"A second failed scroll may be retried")
        check(pagination.observe(titles:["Gamma","Delta"]),"A delayed new browser page must be recognized")
        pagination.finishScroll(changed:true)
        check(pagination.unchangedScrollAttempts == 0,"Visible progress resets the stall count")
        for _ in 0..<3 { pagination.finishScroll(changed:false) }
        check(pagination.stalled,"Three independently failed scroll attempts yield a bounded stall")
        check(!pagination.observe(titles:[]) && pagination.lastTitle == "Delta","Unreadable frames do not erase the last confirmed page range")
        let crop = CGRect(x:0,y:16,width:1272,height:309)
        let titleBox = CGRect(x:45.0/1272,y:1.0-178.0/309,width:200.0/1272,height:20.0/309)
        let translated = observationTextBounds(titleBox,crop:crop)
        check(abs(translated.minX-45) < 0.00001 && abs(translated.minY-174) < 0.00001,
              "Crop OCR coordinates must map to the full-frame title location")
        check(abs(translated.width-200) < 0.00001 && abs(translated.height-20) < 0.00001,
              "Crop OCR dimensions must not be scaled by full-frame height")
        let fullRect = observationTextBounds(CGRect(x:0,y:0,width:1,height:1),crop:CGRect(x:0,y:0,width:1272,height:768))
        check(fullRect == CGRect(x:0,y:0,width:1272,height:768),"Full-frame OCR mapping must remain unchanged")
        if let index = CommandLine.arguments.firstIndex(of:"--snapshot"), index+1 < CommandLine.arguments.count {
            let snapshot = URL(fileURLWithPath:CommandLine.arguments[index+1])
            guard let bitmap = NSBitmapImageRep(data:try Data(contentsOf:snapshot)), let image = bitmap.cgImage else {
                fatalError("Snapshot could not be read")
            }
            check(image.width == 1272 && image.height == 768,"Live snapshot must retain calibrated full-frame dimensions")
            fputs("Testing cold fast text-strip OCR on saved snapshot\n",stderr)
            let fast = try recognizeObservationTokens(image,mixerOnly:true)
            check(recognizedLayoutHeader(fast),"Cold fast observation must recognize the upright layout without prior full-frame OCR")
            fputs("Testing full OCR on saved snapshot\n",stderr)
            let full = try recognizeObservationTokens(image,mixerOnly:false)
            func text(_ tokens: [TextToken], _ rect: CGRect) -> String {
                tokens.filter { rect.contains(CGPoint(x:$0.rect.midX,y:$0.rect.midY)) }
                    .sorted { $0.rect.minX < $1.rect.minX }.map(\.text).joined(separator:" ")
            }
            let fields = [CGRect(x:35,y:25,width:250,height:25),CGRect(x:160,y:25,width:160,height:25),
                          CGRect(x:45,y:174,width:430,height:20),CGRect(x:731,y:174,width:430,height:20)]
            for rect in fields {
                let old = text(full,rect), new = text(fast,rect)
                check(!old.isEmpty && old == new,"Full/fast OCR disagreed in \(rect): \(old) vs \(new)")
            }
            for rect in observationMetadataCrops+observationBPMCrops {
                let old = text(full,rect), new = text(fast,rect)
                check(old == new,"Full/fast deck metadata or BPM disagreed: \(old) vs \(new)")
            }
            if let titleIndex = CommandLine.arguments.firstIndex(of:"--selected-title"), titleIndex+1 < CommandLine.arguments.count {
                let wanted = CommandLine.arguments[titleIndex+1]
                let rows = full.filter { $0.rect.minY > 460 && $0.rect.maxY < 739 &&
                    $0.rect.minX >= 540 && $0.rect.maxX < 891 && $0.confidence > 0.8 }
                let selected = rows.filter { browserRowSelected(image,row:$0.rect) }
                check(selected.count == 1 && selected[0].text == wanted,
                      "Real screenshot must prove the intended title is the one blue selected row")
            }
            print("PASS saved screenshot cold-fast/full header, titles, metadata and BPM")
        }
        let liveRequest: [String:Any] = ["clientPID":getpid()]
        try requireLiveControlRequest(liveRequest)
        try FileManager.default.createDirectory(atPath:socketDirectory,withIntermediateDirectories:true)
        let stopFlag = URL(fileURLWithPath:socketDirectory+"/stop-\(getpid())")
        try Data().write(to:stopFlag)
        defer { try? FileManager.default.removeItem(at:stopFlag) }
        do {
            try requireLiveControlRequest(liveRequest)
            check(false,"A live process with a stop flag must not continue")
        } catch {
            check(String(describing:error).contains("Stop gevraagd"),"Stop flag must reject before input")
        }
        try FileManager.default.removeItem(at:stopFlag)
        try requireLiveControlRequest(liveRequest)
        check(socketPath.hasSuffix("/"+nativeProcessRole.socketFile),"Process must use its isolated role socket")
        if isControlWorker {
            for command in ["djReady","djAuthorize","djStart","djStop"] {
                do {
                    _ = try await handle(["command":command])
                    check(false,"Credential/session command must be blocked in worker")
                } catch {
                    check(String(describing:error).contains("geen sleuteltoegang"),"Worker must reject before any credential/session handling")
                }
            }
        }
        if nativeProcessRole == .demoObserver {
            for command in ["activate","setPlayback","loadChosenTrack","mixGesture","closeStoppedDeck","openSilentDeck"] {
                do { _ = try await handle(["command":command]); check(false,"Observer dispatch must block \(command)") }
                catch { check(String(describing:error).contains("alleen-lezen"),"Observer handler must reject before any input") }
            }
        }
        func bitmap() -> NSBitmapImageRep {
            let image = NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:1272,pixelsHigh:768,
                bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,
                colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
            image.bitmapData!.initialize(repeating:0,count:image.bytesPerRow*image.pixelsHigh)
            return image
        }
        let white = NSColor(deviceRed:1,green:1,blue:1,alpha:1)
        let baseline = bitmap()
        let testRow = CGRect(x:550,y:520,width:150,height:12)
        check(!browserRowSelected(baseline.cgImage!,row:testRow),"An unselected title on dark background is not selection evidence")
        let selectedRow = bitmap()
        let selectionBlue = NSColor(deviceRed:0.03,green:0.17,blue:0.38,alpha:1)
        for y in 518..<536 { for x in 540..<891 { selectedRow.setColor(selectionBlue,atX:x,y:y) } }
        check(browserRowSelected(selectedRow.cgImage!,row:testRow),"A blue background across the intended row proves selection")
        check(!browserRowSelected(selectedRow.cgImage!,row:CGRect(x:550,y:540,width:150,height:12)),
              "Selection of a neighboring row must not authorize loading the desired title")
        let onlyGlyphs = bitmap()
        for y in 520..<532 { for x in 550..<565 { onlyGlyphs.setColor(selectionBlue,atX:x,y:y) } }
        check(!browserRowSelected(onlyGlyphs.cgImage!,row:testRow),"A few blue title glyphs are not a selected row background")
        let unrelatedRows = bitmap(); unrelatedRows.setColor(white,atX:600,y:580)
        check(observationRegionsUnchanged(baseline.cgImage!,unrelatedRows.cgImage!,regions:[loadBrowserRowRegion(testRow)]),
              "Animation in another row cannot invalidate the intended row's exact pixel identity")
        let browserSame = bitmap()
        check(loadBrowserPixelsUnchanged(baseline.cgImage!,browserSame.cgImage!,requireRows:true),
              "Identical new browser pixels preserve previously recognized row identity")
        let advancedPlayback = bitmap(); advancedPlayback.setColor(white,atX:200,y:90)
        advancedPlayback.setColor(white,atX:450,y:350)
        check(loadBrowserPixelsUnchanged(baseline.cgImage!,advancedPlayback.cgImage!,requireRows:true),
              "Moving waveform/transport pixels do not invalidate static library OCR; fresh deck guards must read them separately")
        let scrolledList = bitmap(); scrolledList.setColor(white,atX:600,y:520)
        check(!loadBrowserPixelsUnchanged(baseline.cgImage!,scrolledList.cgImage!,requireRows:true),
              "A changed title-row pixel prevents an old row coordinate from being clicked or loaded")
        check(loadBrowserPixelsUnchanged(baseline.cgImage!,scrolledList.cgImage!,requireRows:false),
              "Scrolling needs current folder identity, not unchanged previous rows")
        let changedFolder = bitmap(); changedFolder.setColor(white,atX:250,y:440)
        check(!loadBrowserPixelsUnchanged(baseline.cgImage!,changedFolder.cgImage!,requireRows:false),
              "A changed folder heading blocks scrolling under old folder recognition")
        check(!loadBrowserPixelsUnchanged(baseline.cgImage!,changedFolder.cgImage!,requireRows:true),
              "A changed folder also blocks row selection and loading")
        let headerCache = ValidatedHeaderCache()
        let header = [TextToken(text:"PERFORMANCE",confidence:1,rect:CGRect(x:60,y:30,width:100,height:12)),
            TextToken(text:"2Deck Horizontal",confidence:1,rect:CGRect(x:180,y:30,width:120,height:12))]
        check(!recognizedLayoutHeader(headerCache.resolve([],image:baseline.cgImage!)),"Unreadable first header cannot establish calibration")
        check(recognizedLayoutHeader(headerCache.resolve(header,image:baseline.cgImage!)),"Valid recognition can recover after identical pixels had invalid OCR")
        check(recognizedLayoutHeader(headerCache.resolve([],image:bitmap().cgImage!)),"Unchanged valid header pixels preserve calibration despite subsequent missing OCR")
        let changedHeader = bitmap(); changedHeader.setColor(white,atX:70,y:30)
        check(!recognizedLayoutHeader(headerCache.resolve([],image:changedHeader.cgImage!)),"A changed header cannot inherit calibration from old pixels")
        check(!recognizedLayoutHeader(headerCache.resolve([],image:changedHeader.cgImage!)),"Repeated invalid recognition of changed pixels remains invalid")
        let unrelated = TextToken(text:"123.00",confidence:1,rect:observationBPMCrops[0])
        check(headerCache.resolve([unrelated],image:baseline.cgImage!).contains(where:{$0.text == "123.00"}),"Header cache preserves newly recognized unrelated fields")
        let bpmCache = ObservationTextCache(regions:observationBPMCrops)
        bpmCache.store([TextToken(text:"123.00",confidence:1,rect:observationBPMCrops[0])],image:baseline.cgImage!)
        let changedPitch = bitmap(); changedPitch.setColor(white,atX:510,y:324)
        check(bpmCache.tokens(for:changedPitch.cgImage!)?.first?.text == "123.00","Lower pitch/synced BPM row is outside the observed large-BPM crops")
        let changedTempo = bitmap(); changedTempo.setColor(white,atX:510,y:309)
        check(bpmCache.tokens(for:changedTempo.cgImage!) == nil,"Any large-BPM pixel change requires fresh BPM OCR")
        let changedTempoB = bitmap(); changedTempoB.setColor(white,atX:770,y:309)
        check(bpmCache.tokens(for:changedTempoB.cgImage!) == nil,"Deck B large-BPM changes also invalidate cache")
        let cache = ObservationTextCache(regions:observationTitleCrops)
        check(cache.tokens(for:baseline.cgImage!) == nil,"Uninitialized OCR cache must miss")
        cache.store([TextToken(text:"Title",confidence:1,rect:observationTitleCrops[0])],image:baseline.cgImage!)
        check(cache.tokens(for:bitmap().cgImage!)?.first?.text == "Title" && cache.reused,"Exactly equal title pixels can reuse their OCR")
        check(guardIdentityUnchanged(baseline.cgImage!,bitmap().cgImage!),"Identical static identity pixels can reuse OCR")
        let waveform = bitmap(); waveform.setColor(white,atX:300,y:100)
        check(cache.tokens(for:waveform.cgImage!)?.first?.text == "Title","Changing waveforms may not force repeat title OCR")
        let changedTitle = bitmap(); changedTitle.setColor(white,atX:60,y:180)
        check(cache.tokens(for:changedTitle.cgImage!) == nil && !cache.reused,"Any changed title pixel requires new OCR")
        check(guardIdentityUnchanged(baseline.cgImage!,waveform.cgImage!),"Waveform changes do not change title identity")
        for pixel in [(50,30),(190,40),(60,180),(900,185)] {
            let changed = bitmap(); changed.setColor(white,atX:pixel.0,y:pixel.1)
            check(!guardIdentityUnchanged(baseline.cgImage!,changed.cgImage!),"Any changed header or title pixel must trigger new OCR")
        }
        check(allowed(),"Stopped A at closed crossfader route is replaceable")
        check(allowed(2,cross:0),"Stopped B at closed crossfader route is replaceable")
        check(allowed(targetFader:0,cross:0.5),"Closed A channel with open B center route is replaceable")
        check(allowed(2,targetFader:0,cross:0.5),"Closed B channel with open A center route is replaceable")
        check(!allowed(target:true),"Never replace playing A")
        check(!allowed(2,target:true,cross:0),"Never replace playing B")
        check(!allowed(target:nil),"Unknown target playback is not stopped")
        check(!allowed(other:false),"Other deck must keep playing")
        check(!allowed(other:nil),"Unknown other playback is not playing")
        check(!allowed(cross:0.5),"Center crossfader with open target is unsafe")
        check(!allowed(cross:0),"Open target route is unsafe")
        check(!allowed(otherFader:0),"Closed other channel is unsafe")
        check(!allowed(targetFader:0,cross:0),"Closing target channel must not hide a closed other route")
        check(!allowed(assignments:false),"Unrecognized assignments fail closed")
        check(!allowed(targetFader:nil),"Missing target channel reading fails closed")
        check(!allowed(otherFader:nil),"Missing other channel reading fails closed")
        check(!allowed(cross:nil),"Missing crossfader reading fails closed")
        check(!allowed(cross:Double.nan),"Non-finite crossfader fails closed")
        check(!allowed(targetFader:Double.infinity),"Non-finite target channel fails closed")
        check(!allowed(otherFader:2),"Out-of-range other channel fails closed")
        check(!allowed(3),"Unknown deck fails closed")
        check(!rejects(validRequest()),"Valid crossfader contract")
        var bass = validRequest(); bass.removeValue(forKey:"crossfader")
        bass["bassPixels"] = 10.0; bass["outgoing"] = 1; bass["incoming"] = 2
        check(!rejects(bass),"Valid bass-only contract")
        bass["outgoing"] = 2; bass["incoming"] = 1
        check(!rejects(bass),"Reverse bass transfer is explicit")
        for key in ["durationSeconds","expectedTracks","clientPID","notAfterMonotonicNS"] {
            var value = validRequest(); value.removeValue(forKey:key)
            check(rejects(value),"Missing \(key) must fail before input")
        }
        for duration in [0.0,0.24,12.01,Double.infinity,Double.nan] {
            var value = validRequest(); value["durationSeconds"] = duration
            check(rejects(value),"Unbounded gesture duration must fail")
        }
        for cross in [-0.1,1.1,Double.nan] {
            var value = validRequest(); value["crossfader"] = cross
            check(rejects(value),"Invalid crossfader must fail")
        }
        for pixels in [-1.0,0,40.1,Double.infinity] {
            var value = bass; value["bassPixels"] = pixels
            check(rejects(value),"Invalid bass transfer must fail")
        }
        var same = bass; same["incoming"] = 2
        check(rejects(same),"Same deck cannot be both bass roles")
        var empty = validRequest(); empty.removeValue(forKey:"crossfader")
        check(rejects(empty),"No-op command must fail")
        var title = validRequest(); title["expectedTracks"] = ["1":"A","2":"  "]
        check(rejects(title),"Blank expected title must fail")
        print("PASS \(checks) native transport safety cases")
    }
}
