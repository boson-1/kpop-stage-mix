# Mask transitions

A mask transition replaces one picture by the other through a mask that changes over time, instead of mixing the two everywhere at once. Film and broadcast have a standard set of them; editing software and motion libraries add more. This file says which ones the pipeline uses, which ones were weighed and left out, and what a new one has to satisfy.

## What the pipeline does

`plan.py` chooses the transition in `mask_fx` and writes it to the EDL as `"fx"`; `render.py` draws it.

| `fx` | What happens | Where the planner puts it |
|---|---|---|
| `bloom` | the new stage shows on the matched face alone, holds there, then spreads over the frame behind a ragged, lit edge | one matched face, change on a bar start |
| `sweep`, `dir` [0, 1] | a straight soft edge runs down the body | one matched face, change on a strong beat that is not a bar start |
| `sweep`, `dir` [±1, 0] | a straight soft edge crosses the frame, the way the faces are travelling | several matched faces, any strong beat |

`plan.py` prints and counts both kinds of sweep as `sweep`. The number of matched faces is the `n_faces` of the change in the EDL.

A sweep is the textbook wipe: a grayscale card gives each pixel its turn (0 first, 1 last), here a ramp built in `order_map`, and a soft edge travels through that order.

A bloom is what editors build with a mask on the face and keyframes on its expansion and feather (`bloom` in `render.py`):

- The opening is an ellipse on the face of the incoming stage, 0.72 face heights wide and 1.3 times as high. It follows that face frame by frame.
- Inside it the face changes like a 2-frame dissolve, on the frame of the change.
- The opening waits `FX_HOLD` frames, creeping outward a little, then runs out towards the corners, slowly at first. What it has not reached on its last frame, the far corners, changes at once on the next.
- It starts on the frame before the change and ends `FX_LEN` frames after it: 11 frames, about a third of a second, the hold included.
- Noise is added to the distance from the opening, none at the face and more further out. This makes the edge ragged, the same shape on every frame of one bloom.
- The light on the edge (`FX_GLOW`) is there only while the edge travels, never on the face.

Rules the planner applies (constants in `references/tuning.md`):

- A strong beat is every `FX_EVERY`-th beat counted from a bar start, in bars of four beats on the grid of `beats.py`: with 2, beats 1 and 3 of the bar; with 4, bar starts only, which leaves no sweep down the body. Only face-matched changes within a frame of a strong beat get a mask transition. All other changes keep the short dissolve.
- Bar starts choose first. Two mask transitions keep `FX_GAP` frames between them, so a run of quick changes stays plain.
- The old face must stay on screen until the transition ends and the new face must be there from its start. This also rules out a camera cut inside the transition. A bloom also needs the new face tracked through all of its frames, because the opening follows it. A change that fails one of these keeps its dissolve; it is not given the other transition.
- A sweep's edge is timed to be half way over the matched face on the frame of the change. A bloom changes the face on that frame.

## Why the face changes on the frame of the change

Two stages are in register at one place and one moment only: the matched face, on the frame of the change. A few frames later the cameras and the members have moved apart, and bodies, hair and bystanders never lined up.

- A dissolve mixes both pictures over the whole frame, so it has to be over in 2 to 4 frames.
- A wipe shows one picture on each side of its edge. Only the narrow soft edge mixes the two, and it crosses the face at the one moment the face is in register. This is why a wipe may take 10 frames where a dissolve may not.
- A bloom changes the face at that moment and nothing else. After it the new face sits in the old picture, and how long it may sit there depends on how far the two faces drift apart. Measured on the 110 face-matched changes of two test songs, the median is 0.1 face heights after 4 frames and 0.2 after 8. Hence `FX_HOLD` is 3.

A new transition must keep both properties: each pixel changes once, in a few frames of its own, and the matched face changes on the frame of the change.

## What was weighed, and the verdict for each

From film and television:

| Technique | In film and television | Here |
|---|---|---|
| Match dissolve ("no-change dissolve") | two shots composed alike, dissolved: the telephone that becomes another telephone | the base technique of a stage mix: the face-matched dissolve |
| Linear wipe, soft edge | a straight edge crosses the frame; Kurosawa, then *Star Wars* | `sweep` |
| Iris | a circle opens or closes, "centred on a certain focal point"; silent films, cartoon endings | the bar-start transition of v0.2, as a circle growing out of the matched face at constant speed. Replaced by `bloom`, which also opens out of the face: the user chose it in a side-by-side of one song, the circle had no defect of its own |
| Gradient wipe | any grayscale image as the card (After Effects Gradient Wipe; the SMPTE 258M wipe patterns are such cards) | the engine of `sweep`: `order_map` |
| Clock, barn door, blinds, matrix | the card is an angle, two edges, stripes, a grid | not used: one more card each, but the shape has nothing to do with the performer. Add one only when the user asks |
| Star, heart | shaped irises | not used: the sources themselves call them overused and tacky |
| Edge border and glow | broadcast switchers draw a coloured or glowing line on the edge | the light on the edge of `bloom`: added to the luma, and the colour taken out under it. Not on `sweep` |
| Luma dissolve (luma key transition) | the order comes from the pictures: bright areas of the new shot break through first | tried as a card ranked by how much brighter the new picture is, and dropped. With a narrow soft edge, large bright LED walls came through as blotches. With a wide one, both pictures are mixed over the whole frame for the full length: at 8 frames the faces doubled on 3 of 4 changes in two test songs |
| Natural or invisible wipe | something crosses the lens, or the camera pans fast, and the cut hides in it: the pan between the two U-boat pens in *Das Boot*, a hand or a sleeve in outfit-change clips | not automated: it needs something that covers the frame at the same moment in both stages, and the face tracks cannot see that |
| Subject matte | the performer is cut out and changes before the background does | `bloom` is the part of it that needs no model: an ellipse on the face. The outline of hair, outfit or body needs a person-segmentation model, and bodies are in register only at the face |
| Morph | a warp carries the features of one picture to the other while it dissolves | not done: a morph needs matching points all over both pictures, and alignment here is scale and shift on the faces only |
| Flash, dip to white, light-leak overlay | a few bright frames, or a burst of light laid over the cut | not mask transitions; not implemented |

From editing software and motion libraries (numbers are entries of the Motion Library; names in code type are HyperFrames transitions):

| Technique | Where it is found | Here |
|---|---|---|
| Mask on the face, then keyframes on mask expansion and feather | Premiere Pro and After Effects practice; 나무위키 describes cutting the outfit out with the pen tool and unfolding it in the next shot | `bloom` |
| Noise as the card ("burn"), with a lit band on the edge | `ridged-burn`, `domain-warp` | the ragged edge of `bloom`. As a card for the whole frame it would change the face in patches, so the noise is added to the distance from the face instead |
| Stopping on the way, then finishing | `grade-split-reveal` | the hold of `bloom` |
| An ease curve on every transition, mostly ease-in-out | both libraries | `bloom` spreads slowly at first. `sweep` still moves at constant speed: untested |
| Halftone dots, grid cells, blinds or strips revealed one after the other | #340, `halftone-dissolve`, `grid-pixelate-wipe` | not used: one more card each, and the pattern has nothing to do with the performer |
| Pictures that move: push, slide, slices, split doors, zoom through, whip pan, card flip | #267, #279, #311; the push, scale and 3d groups | not used: the old face slides or is cut away, so the face that stays while the outfit changes is lost |
| Something covers the frame in between: curtain, colour dip, flash, light leak, a logo zooming through | #202, #150; the cover and light groups | not used on a face-matched change: the cut hides behind the cover and the match is never seen |
| Both pictures mixed over the frame for several frames: blur crossfade, glitch, chromatic split, ripple, swirl | the dissolve, blur and distortion groups | not used: everything that is not in register doubles, as it did in the luma dissolve |
| The incoming picture settles from 1.04 to 1.35 times its size while it is revealed | #115, #131; the HyperFrames wipes | not used: the incoming camera is fixed by the face match. Zooming both pictures about the face is untested |

The verdicts "not used" in this table are for face-matched changes. On a camera-cut switch no face match is at stake; nothing was tried there.

What editors of stage mixes add to this (from 나무위키 and the two papers below): matching the whole body instead of the face when the camera work is alike, changing during a spin, and "one-take" mixes that use face matches only and never cut.

## How much of it

- Glassner: a transition the viewer notices has failed, unless the transition is itself part of the show. In a stage mix the change of outfit is the show, so a visible transition is allowed where the music asks for one, and nowhere else.
- Hence strong beats only, a gap between two of them, and the plain dissolve for everything else. Expect roughly a quarter to a third of the face-matched changes.

## Adding a transition

1. `render.py`: a card (every pixel changes once, in a fixed order) is a branch on `fx["type"]` in `order_map`, which today holds only the sweep's ramp; it returns 0 first to 1 last. A transition with a timing of its own gets its own function beside `bloom`, called from `join`.
2. `mask_fx` in `plan.py`: decide when it is chosen, and write into the `"fx"` whatever the renderer needs, such as the position of the face. For a card, set `at` to the card's value at the matched face; the timing follows from that.
3. Add the type to the count `plan.py` prints at the end, and to the reading of the rows in `SKILL.md`.
4. Plan, render a draft, and look at every row of that type on the sheets, against the dissolve of the same changes (`render.py --dissolve 4`).

## Sources

- Andrew Glassner, "A Change of Scene", IEEE Computer Graphics and Applications, May/June 2001: wipes as mask cards, iris, luminance-driven transitions, when a transition fails. https://www.glassner.com/wp-content/uploads/2014/04/CG-CGA-PDF-01-05-A-Change-of-Scene-May01.pdf
- Wikipedia, "Wipe (transition)": iris, clock, star, heart, invisible and object wipes with their film examples. https://en.wikipedia.org/wiki/Wipe_(transition)
- Boris FX Sapphire documentation: S_WipeCircle (centre, edge softness, border, glow) and S_DissolveLuma (which areas change first). https://borisfx.com/documentation/sapphire/ofx/wipecircle/ and https://borisfx.com/documentation/sapphire/ofx/dissolveluma
- muvee GradientFade: a gradient map as the card, with a feather width. https://srikumarks.github.io/muvee-style-authoring/GradientFade.html
- SMPTE 258M wipe codes as listed for DirectShow. https://learn.microsoft.com/en-us/windows/win32/directshow/smpte-wipe-transition
- Motion Array, "4 Creative Ways to Use Premiere Pro Masks": a mask tracked on a face, with feather and expansion. https://motionarray.com/learn/premiere-pro/premiere-pro-mask-tracking/
- Motion Library 動態收藏: 342 web motion effects with their recipes, 45 of them for transitions. https://kittenjam-motion-library.vercel.app/
- HeyGen HyperFrames 0.9.7, `packages/shader-transitions` and `registry/`: 14 shader transitions, 31 CSS transitions and the reveal components. https://github.com/heygen-com/hyperframes. OpenMotion catalogues them: https://github.com/nexu-io/openmotion
- Lee et al., "PopStage", ACM Transactions on Graphics 41(6), 2022: silhouette alignment, a virtual camera, an editing guideline from interviews with stage-mix creators. https://w-dlee.github.io/popstage
- Oh, Jang, Lee, "AutoStageMix", Applied Sciences 15(13), 2025: face-based transitions, inter-stage and intra-stage cuts. https://www.mdpi.com/2076-3417/15/13/7613
- 나무위키, "교차편집": the editing styles named above. https://namu.wiki/w/교차편집
