# Mask transitions

A mask transition replaces one picture by the other through a mask that changes over time, instead of mixing the two everywhere at once. Film and broadcast have a standard set of them. This file says which ones the pipeline uses, which ones were weighed and left out, and what a new one has to satisfy.

## What the pipeline does

Every mask transition is the textbook wipe: a grayscale card gives each pixel its turn (0 first, 1 last), and a soft edge travels through that order. `render.py` builds the card in `order_map`; `plan.py` chooses the transition in `mask_fx` and writes it to the EDL as `"fx"`.

| `fx` | Card | Where the planner puts it |
|---|---|---|
| `iris` | distance from the matched face: the new stage opens out of the face in a growing circle | one matched face, change on a bar start |
| `sweep`, `dir` [0, 1] | ramp from top to bottom: the edge runs down the body | one matched face, change on a strong beat that is not a bar start |
| `sweep`, `dir` [±1, 0] | ramp across the frame, the way the faces are travelling | several matched faces, any strong beat |

`plan.py` prints and counts both kinds of sweep as `sweep`.

Rules the planner applies (constants in `references/tuning.md`):

- A strong beat is every `FX_EVERY`-th beat counted from a bar start: with 2, beats 1 and 3 of the bar; with 4, bar starts only, which leaves no sweep down the body. Only face-matched changes within a frame of a strong beat get a mask transition. All other changes keep the short dissolve.
- Bar starts choose first. Two mask transitions keep `FX_GAP` frames between them, so a run of quick changes stays plain.
- The old face must stay on screen until the transition ends and the new face must be there from its start. This also rules out a camera cut inside the transition.
- The edge is timed to be half way over the matched face on the frame of the change.

## Why the edge is timed to the face

Two stages are in register at one place and one moment only: the matched face, on the frame of the change. A few frames later the cameras and the members have moved apart, and bodies, hair and bystanders never lined up.

- A dissolve mixes both pictures over the whole frame, so it has to be over in 2 to 4 frames.
- A wipe shows one picture on each side of its edge. Only the narrow soft edge mixes the two, and it crosses the face at the one moment the face is in register. This is why a wipe may take 10 frames where a dissolve may not.

A new transition must keep both properties: each pixel changes once, within a few frames, and the matched face changes on the frame of the change.

## The textbook set, and the verdict for each

| Technique | In film and television | Here |
|---|---|---|
| Match dissolve ("no-change dissolve") | two shots composed alike, dissolved: the telephone that becomes another telephone | the base technique of a stage mix: the face-matched dissolve |
| Linear wipe, soft edge | a straight edge crosses the frame; Kurosawa, then *Star Wars* | `sweep` |
| Iris | a circle opens or closes, "centred on a certain focal point"; silent films, cartoon endings | `iris`, centred on the matched face |
| Gradient wipe | any grayscale image as the card (After Effects Gradient Wipe; the SMPTE 258M wipe patterns are such cards) | the engine itself: `order_map` |
| Clock, barn door, blinds, matrix | the card is an angle, two edges, stripes, a grid | not used: one more card each, but the shape has nothing to do with the performer. Add one only when the user asks |
| Star, heart | shaped irises | not used: the sources themselves call them overused and tacky |
| Edge border and glow | broadcast switchers draw a coloured or glowing line on the edge | not used: untested, and the line would have to be drawn in YUV |
| Luma dissolve (luma key transition) | the order comes from the pictures: bright areas of the new shot break through first | tried as a card ranked by how much brighter the new picture is, and dropped. With a narrow soft edge, large bright LED walls came through as blotches. With a wide one, both pictures are mixed over the whole frame for the full length: at 8 frames the faces doubled on 3 of 4 changes in two test songs |
| Natural or invisible wipe | something crosses the lens, or the camera pans fast, and the cut hides in it: the pan between the two U-boat pens in *Das Boot*, a hand or a sleeve in outfit-change clips | not automated: it needs something that covers the frame at the same moment in both stages, and the face tracks cannot see that |
| Subject matte | the performer is cut out and changes before the background does | not done: needs a person-segmentation model, and bodies are in register only at the face, so the outline would not fit |
| Morph | a warp carries the features of one picture to the other while it dissolves | not done: a morph needs matching points all over both pictures, and alignment here is scale and shift on the faces only |
| Flash, dip to white, light-leak overlay | a few bright frames, or a burst of light laid over the cut | not mask transitions; not implemented |

What editors of stage mixes add to this (from 나무위키 and the two papers below): matching the whole body instead of the face when the camera work is alike, changing during a spin, and "one-take" mixes that use face matches only and never cut.

## How much of it

- Glassner: a transition the viewer notices has failed, unless the transition is itself part of the show. In a stage mix the change of outfit is the show, so a visible transition is allowed where the music asks for one, and nowhere else.
- Hence strong beats only, a gap between two of them, and the plain dissolve for everything else. Expect roughly a quarter to a third of the face-matched changes.

## Adding a card

1. `order_map` in `render.py`: return the new card for your `fx["type"]`, 0 first to 1 last.
2. `mask_fx` in `plan.py`: decide when it is chosen, and set `at` to the card's value at the matched face. The timing follows from that.
3. Plan, render a draft, and look at every row of that type on the sheets, against the dissolve of the same changes (`render.py --dissolve 4`).

## Sources

- Andrew Glassner, "A Change of Scene", IEEE Computer Graphics and Applications, May/June 2001: wipes as mask cards, iris, luminance-driven transitions, when a transition fails. https://www.glassner.com/wp-content/uploads/2014/04/CG-CGA-PDF-01-05-A-Change-of-Scene-May01.pdf
- Wikipedia, "Wipe (transition)": iris, clock, star, heart, invisible and object wipes with their film examples. https://en.wikipedia.org/wiki/Wipe_(transition)
- Boris FX Sapphire documentation: S_WipeCircle (centre, edge softness, border, glow) and S_DissolveLuma (which areas change first). https://borisfx.com/documentation/sapphire/ofx/wipecircle/ and https://borisfx.com/documentation/sapphire/ofx/dissolveluma
- muvee GradientFade: a gradient map as the card, with a feather width. https://srikumarks.github.io/muvee-style-authoring/GradientFade.html
- SMPTE 258M wipe codes as listed for DirectShow. https://learn.microsoft.com/en-us/windows/win32/directshow/smpte-wipe-transition
- Lee et al., "PopStage", ACM Transactions on Graphics 41(6), 2022: silhouette alignment, a virtual camera, an editing guideline from interviews with stage-mix creators. https://w-dlee.github.io/popstage
- Oh, Jang, Lee, "AutoStageMix", Applied Sciences 15(13), 2025: face-based transitions, inter-stage and intra-stage cuts. https://www.mdpi.com/2076-3417/15/13/7613
- 나무위키, "교차편집": the editing styles named above. https://namu.wiki/w/교차편집
